# CodeMate — AI Coding Agent Backend

A Retrieval-Augmented Generation (RAG) backend powered by FastAPI, PostgreSQL (with pgvector), CocoIndex, Python's `ast` module, and Google Gemini. Upload a codebase `.zip`, it gets semantically indexed and AST-parsed into an import/call graph, and you chat with an AI assistant that answers questions grounded in both.

## Core Flow

Upload zip → background validate/extract → CocoIndex chunks + embeds code → AST-parse Python files into a symbol/import/call graph → status flips to `ready` → user chats → vector search + AST context map both feed the LLM prompt → response + sources saved.

## Project Structure

```text
├── alembic/                     # DB migrations (alembic.ini is gitignored — create locally)
│   ├── env.py                  # include_object filter excludes CocoIndex-managed objects
│   └── versions/
├── models/                      # SQLAlchemy ORM models & Pydantic schemas
│   ├── database.py
│   └── schemas.py
├── services/                    # Core business logic, one class per concern
│   ├── cocoindex_service.py    # chunking + embedding + vector search
│   ├── ast_service.py          # AST symbol/edge extraction, context map, orphan detection
│   ├── llm_service.py          # Gemini client wrapper (tenacity retries)
│   ├── rag_service.py          # orchestrator: vector search -> AST context -> LLM
│   ├── onboarding_service.py   # architecture doc generation + bundled CVE scan
│   ├── blast_radius_service.py # agentic downstream-impact analysis (get_callers/get_callees)
│   ├── stack_trace_service.py  # traceback parsing + explanation, reuses graph_tools
│   ├── graph_tools.py          # shared get_callers/get_callees factory
│   └── zip_validator.py        # upload safety checks
├── exceptions.py                # domain exceptions (mapped to HTTP responses in main.py)
├── main.py                      # FastAPI app, routes, background indexing task
├── config.py                    # Pydantic settings (env vars, upload dir, Gemini config)
├── create_db.py                 # helper script to create the DB + enable the pgvector extension
├── requirements.txt
├── .env.example
└── README.md
```

## Setup & Installation

### 1. Prerequisites

- **Python 3.10+**
- **PostgreSQL** with the `pgvector` extension installed.

### 2. Configure Database & Extensions

```sql
CREATE DATABASE coding_agent;
CREATE EXTENSION IF NOT EXISTS vector;
```

### 3. Environment Configuration

```bash
cp .env.example .env
```

Set `DATABASE_URL`, `COCOINDEX_DATABASE_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL`.

### 4. Install Dependencies

```bash
python -m venv codingagent
source codingagent/Scripts/activate  # Windows: .\codingagent\Scripts\activate
pip install -r requirements.txt
```

### 5. Migrations

```bash
alembic revision --autogenerate -m "..."
alembic upgrade head
```

`alembic.ini` is gitignored — create it locally pointing `sqlalchemy.url` at your DB. **Always manually review autogenerate output before running it** — it reliably proposes dropping CocoIndex-managed objects (tracking tables, the HNSW vector index) since those live outside SQLAlchemy's `Base.metadata`.

### 6. Running the Application

```bash
uvicorn main:app --reload
```

Serves at `http://127.0.0.1:8000`, interactive docs at `/docs`.

There is currently no automated test suite, linter config, or CI pipeline — every phase is verified manually end-to-end via Postman before merge.

## Features Implemented So Far

- **LLM reliability:** `tenacity` retry/backoff on Gemini calls (503/429), bounded chat history (last 20 messages), configurable `top_k` for vector search.
- **Security:** zip upload validation (file count, uncompressed size, path-traversal, disallowed-extension/dir filtering that skips offending files instead of failing the whole upload), typed domain exceptions with FastAPI exception handlers, uniform `{success, data, error}` response envelope.
- **AST Context Map:** Python files parsed via the stdlib `ast` module into a symbol table (`CodeSymbol`: functions/methods/classes/top-level variables) and an import/call edge graph (`CodeEdge`). A 1-hop context map (capped at 30 edges) is built from the files returned by vector search and injected into the LLM prompt alongside retrieved code, to ground cross-file relationships. AST parsing failures are non-fatal and logged per-file (`ASTSkippedFile`) — chat still works from vector search alone if AST indexing fails.
- **Declarative/variable symbols:** module- and class-scope assignments (`agent = Agent(...)`) are now captured as `"variable"` symbols, closing most of the earlier blind spot where declarative/framework-style code produced zero symbols. Function-local assignments are still excluded to avoid noise.
- **Dead Code / Orphan Detector:** `GET /api/orphans/{project_id}` flags `CodeSymbol` rows with zero inbound `CodeEdge` references as dead-code candidates, reusing the AST graph with no new tables/migrations. Dunder methods excluded by default (`?include_dunder=` to include them). Heuristic by design — see Known Limitations below for false-positive classes.
- **Automated Onboarding & Architecture Generation + CVE scan:** `GET /api/onboarding/{project_id}` builds a directory tree, parses `requirements.txt`/`package.json` into a dependency manifest, and generates a README-style architecture doc via Gemini grounded in the directory structure, dependencies, and AST symbol summary. Bundles a non-fatal CVE scan of declared dependencies against OSV.dev's public batch API (PyPI/npm) — degrades to an empty result with `vulnerability_scan_degraded: true` rather than failing the request if OSV.dev is unreachable. No new tables/migrations.
- **Blast Radius Checker (Impact Analysis Agent):** `GET /api/blast-radius/{project_id}` gives Gemini two graph-query tools (`get_callers`/`get_callees`) via the `google-genai` SDK's Automatic Function Calling and lets it traverse the `CodeEdge` graph outward from a target symbol to produce a downstream-impact report. Also ships a scoped call-resolution improvement — simple `var = ClassName(...)` assignments are now tracked so `var.method()` calls on instances of imported classes resolve cross-file, closing a gap present since Phase 5. No new tables/migrations.
- **Post-Phase-8 hardening pass:** fixed a Windows-specific path-separator mismatch between CocoIndex-written and AST-service-written filenames that had been silently returning an empty `context_map` from `/api/chat` since Phase 5, and a "synthetic wrapping folder" bug where zipping a project inside a top-level folder (the default behavior of most zip tools) broke resolution of every absolute import in the codebase. Both verified fixed against a real `/api/chat` call. Also added CocoIndex silent-row-failure detection (new `EmbeddingSkippedFile` table), an Alembic `include_object` filter so `autogenerate` stops proposing to drop CocoIndex-managed objects, gated `Base.metadata.create_all()` behind an `ENVIRONMENT` setting so it never runs in production, and removed `.env` from the upload allowlist.
- **"Explain This Stack Trace" Mode:** `POST /api/explain-trace` parses a raw Python traceback, maps each frame to real code via the AST graph (progressive suffix-matching against stored filenames, since a traceback's paths come from whatever environment produced it), and asks Gemini to explain the failure — reusing the same `get_callers`/`get_callees` tool-calling as Blast Radius (now extracted into a shared `services/graph_tools.py`) so it can explore downstream impact. Handles `SyntaxError`/`IndentationError` frames (which omit the usual `, in <function>` suffix) and rejects basename-only file matches whose line number doesn't fit the candidate file, to avoid misattributing a frame to a same-named file in a third-party package. Frames that don't resolve to exactly one project file are silently dropped rather than erroring. No new tables/migrations.
- **Language-aware embeddings + chat filtering:** `CodeEmbedding.language` is now populated with the real per-file language (`python`, `javascript`, `typescript`, `tsx`, ...) via a `cocoindex.op.function()`-registered transform in the indexing flow, instead of a hardcoded placeholder. `POST /api/chat` accepts an optional `language` filter, scoping vector search to just that language — verified against a real mixed Python/React-TypeScript repo: filtering by `python` returned only backend `.py` sources, `javascript` returned only plain-`.js` config files, and `tsx` returned only the actual React component code, each producing a correctly different, non-hallucinated answer for the same underlying question.
- **Agentic reasoning trace + multi-level import resolution:** Blast Radius and Stack Trace Explainer responses now include `tool_calls` — the real `get_callers`/`get_callees` calls the agent made, with args and results — so a report's claims are auditable, not just plausible. If the agent hits its tool-call limit mid-exploration, a synthesis step now writes a final answer from whatever it already discovered instead of returning nothing. Also fixes `detect_synthetic_prefix()` to handle projects wrapped in *multiple* levels of nesting (e.g. a zip's own wrapping folder stacked on a monorepo subproject folder like `Backend/`) — the original fix only handled one level. Verified end-to-end on a real two-level-nested Flask+React monorepo: cross-file import resolution went from completely broken to fully correct, and both agentic endpoints independently named the exact same real callers for the same symbol.

## API Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/upload-codebase` | Multipart zip upload; kicks off background indexing (`status: indexing` → `ready`/`error`) |
| GET | `/api/indexing-status/{project_id}` | Status, file count, `ast_skipped_files: [{filename, reason}]`, and `embedding_skipped_files: [{filename, reason}]` |
| POST | `/api/chat` | Vector search + AST context map + history → Gemini; optional `language` filter scopes retrieval to one language; returns `context_map` in the response |
| POST | `/api/session/save` | Upsert a chat session (title/timestamps) |
| GET | `/api/sessions/{project_id}` | Paginated, sortable by `updated_at`/`created_at` |
| GET | `/api/sessions/{session_id}/messages` | Paginated, chronological order |
| GET | `/api/symbols/{project_id}` | Optional `?filename=` exact-match filter (includes the zip's top-level extracted folder prefix) |
| GET | `/api/context-map/{project_id}?filenames=a.py&filenames=b.py` | Ad-hoc 1-hop context map for given files |
| GET | `/api/orphans/{project_id}` | Dead-code candidates; `?include_dunder=true` to include magic methods |
| GET | `/api/onboarding/{project_id}` | Architecture doc + dependency list + bundled CVE scan; requires `status: "ready"` |
| GET | `/api/blast-radius/{project_id}?filename=&symbol_name=&max_hops=` | Agentic downstream-impact report for a target symbol; requires `status: "ready"`; 404 if the symbol doesn't exist; returns `tool_calls` (the real get_callers/get_callees calls made) |
| POST | `/api/explain-trace` | Body: `{project_id, traceback, max_hops}`; requires `status: "ready"`; returns `explanation`, `resolved_frames`, `used_agentic_tools`, `tool_calls` |

All responses use the envelope `{ "success": bool, "data": ..., "error": ... }`. Domain errors (`ProjectNotFoundError`, `SessionNotFoundError`, `ProjectNotReadyError`) map to 404/404/409 respectively.

## Known Limitations / Open Items

### AST & call-graph heuristics

The schema is language-agnostic by design, but resolution accuracy is bounded in known ways:

- Call/reference resolution is name-based, not type-aware for the general case — precise enough for prompt grounding and orphan-candidate heuristics, not full static analysis.
- Cross-file instance-method resolution (`var.method()`) uses flat, unscoped variable-type tracking — doesn't distinguish function-local vs module-level variables of the same name. A deliberate, bounded heuristic, not full type inference.
- Call resolution can't see a bare function *reference* passed as an argument (e.g. `asyncio.to_thread(some_func, ...)`, `executor.submit(...)`) — only direct `Call` nodes are tracked, so Blast Radius/orphan detection will miss real callers that invoke a symbol this way.
- Orphan detection is heuristic: framework-invoked code (e.g. FastAPI route handlers), dynamic/reflective access, and cross-file method calls on class instances can all appear as false-positive "dead code."
- Stack-trace frame-to-file matching (progressive path-suffix matching + a line-count sanity check) can still misattribute a frame if two files share both a basename *and* have enough lines to make the frame's line number plausible in the wrong one. Not expected to be common, but not impossible.
- Python-only. AST parsing, symbol/edge extraction, and every graph-powered feature (orphans, Blast Radius, Stack Trace Explainer) only see `.py` files — a JS/TS/other-language file in an uploaded repo is embedded/searchable via vector search, but invisible to all AST-graph features.

### Agentic reports (Blast Radius, Stack Trace Explainer)

- The fixed tool-call budget (`max_remote_calls`, capped via `max_hops ≤ 10`) can be exhausted on a genuinely wide or deep real call graph — confirmed in testing against a real symbol with 9 direct callers and a second hop of real cross-file callers, which used all 20 available calls just to fully map the graph. When this happens, a synthesis step writes the final answer from whatever was already discovered rather than returning nothing, but a wider graph than this could still hit the same ceiling before finishing exploration (not just before summarizing).

### CVE scan

- Queries unpinned dependencies (no `==` in `requirements.txt`) by package name alone, so results can include CVEs already fixed in the actually-installed version.
- Two advisory IDs for the same package (e.g. a GHSA ID and a PYSEC ID) can carry identical summary text if they reference the same underlying CVE — results aren't de-duplicated by underlying vulnerability.

### Indexing

- `?language=` filtering matches `determine_language()`'s mapping exactly, which keeps `javascript`, `typescript`, and `tsx` as three distinct values rather than folding them into one "JS-family" bucket — a `.js` file won't match `language=javascript` if you meant to also catch `.tsx` components. Intentional (precise filtering, not lossy), but worth knowing before assuming a language filter returned nothing when it just needed the more specific value.
- CocoIndex's auth registry is process-global — `index_codebase()` previously failed on any upload after the first one in a given server run (`RuntimeError: Auth entry already exists`), masked for most of this project's history by `uvicorn --reload` resetting the registry on every code change. Fixed with a lazy singleton, but not yet re-verified under the specific condition that caused it (two uploads, same process, no restart in between).
