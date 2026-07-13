# CodeMate — AI Coding Agent Backend

A Retrieval-Augmented Generation (RAG) backend powered by FastAPI, PostgreSQL (with pgvector), CocoIndex, Python's `ast` module, and Google Gemini. Upload a codebase `.zip`, it gets semantically indexed and AST-parsed into an import/call graph, and you chat with an AI assistant that answers questions grounded in both.

## Core Flow

Upload zip → background validate/extract → CocoIndex chunks + embeds code → AST-parse Python files into a symbol/import/call graph → status flips to `ready` → user chats → vector search + AST context map both feed the LLM prompt → response + sources saved.

## Project Structure

```text
├── models/                    # SQLAlchemy ORM models & Pydantic schemas
│   ├── database.py
│   └── schemas.py
├── services/                  # Core business logic, one class per concern
│   ├── cocoindex_service.py   # chunking + embedding + vector search
│   ├── ast_service.py         # AST symbol/edge extraction, context map, orphan detection
│   ├── llm_service.py         # Gemini client wrapper (tenacity retries)
│   ├── rag_service.py         # orchestrator: vector search -> AST context -> LLM
│   └── zip_validator.py       # upload safety checks
├── exceptions.py               # domain exceptions (mapped to HTTP responses in main.py)
├── main.py                     # FastAPI app, routes, background indexing task
├── config.py                   # Pydantic settings (env vars, upload dir, Gemini config)
├── create_db.py                # helper script to create the DB + enable the pgvector extension
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

## API Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/upload-codebase` | Multipart zip upload; kicks off background indexing (`status: indexing` → `ready`/`error`) |
| GET | `/api/indexing-status/{project_id}` | Status, file count, and `ast_skipped_files: [{filename, reason}]` |
| POST | `/api/chat` | Vector search + AST context map + history → Gemini; returns `context_map` in the response |
| POST | `/api/session/save` | Upsert a chat session (title/timestamps) |
| GET | `/api/sessions/{project_id}` | Paginated, sortable by `updated_at`/`created_at` |
| GET | `/api/sessions/{session_id}/messages` | Paginated, chronological order |
| GET | `/api/symbols/{project_id}` | Optional `?filename=` exact-match filter (includes the zip's top-level extracted folder prefix) |
| GET | `/api/context-map/{project_id}?filenames=a.py&filenames=b.py` | Ad-hoc 1-hop context map for given files |
| GET | `/api/orphans/{project_id}` | Dead-code candidates; `?include_dunder=true` to include magic methods |

All responses use the envelope `{ "success": bool, "data": ..., "error": ... }`. Domain errors (`ProjectNotFoundError`, `SessionNotFoundError`, `ProjectNotReadyError`) map to 404/404/409 respectively.

## Known Limitations / Open Items

- Call/reference resolution in the AST graph is name-based, not type-aware — precise enough for prompt grounding and orphan-candidate heuristics, not yet for deeper impact analysis (planned for a later "Blast Radius" phase).
- CocoIndex writes embeddings directly to Postgres, bypassing the SQLAlchemy ORM — a row-level insert failure there doesn't currently surface as an API-visible error.
- `.env` files are still accepted by the zip validator's extension allowlist; if present in an uploaded codebase, they could be extracted and potentially embedded.
- `Base.metadata.create_all()` runs on every app startup, which can create new model tables outside of Alembic's tracking.
- Orphan detection is heuristic: framework-invoked code (e.g. FastAPI route handlers), dynamic/reflective access, and cross-file method calls on class instances can all appear as false-positive "dead code."

## Roadmap

Dead Code Detector (done) → Automated Onboarding/Architecture Doc Generation + CVE scan → Blast Radius / Impact Analysis → Stack Trace Explainer → Git History / Time-Travel RAG.
