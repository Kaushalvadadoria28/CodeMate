"""
Phase 9 — "Explain This Stack Trace" Mode.

Parses a raw Python traceback, maps each frame to real code in the
project via the AST graph (CodeSymbol), and asks Gemini to explain the
failure — with the same Blast-Radius tool-calling (get_callers/
get_callees) available so the model can explore downstream impact when
it decides that's useful. Frames that can't be confidently matched to
exactly one project file (stdlib, site-packages, ambiguous basenames)
are silently dropped rather than surfaced as an error — matches this
project's existing degrade-gracefully pattern (ASTSkippedFile,
EmbeddingSkippedFile, CVE scan degradation).
"""

import re
from pathlib import Path

_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>\S+))?')
_MAX_RESOLVED_FRAMES = 20
_SNIPPET_CONTEXT_LINES = 3


class StackTraceExplainerService:

    def _parse_frames(self, raw_traceback: str) -> list[dict]:
        """Extracts (path, line, func) triples from a standard Python
        traceback, deduped. Returns [] for non-traceback input."""
        frames = []
        seen = set()
        for m in _FRAME_RE.finditer(raw_traceback):
            key = (m.group("path"), m.group("line"), m.group("func"))
            if key in seen:
                continue
            seen.add(key)
            frames.append({
                "raw_path": m.group("path"),
                "line": int(m.group("line")),
                "function": m.group("func") or "<module>",
            })
        return frames

    def _match_project_file(self, trace_path: str, known_segments: dict) -> str | None:
        """Progressive suffix match between a traceback's file path (which
        may be absolute, from an environment CodeMate never saw) and the
        project's stored relative filenames. Starts from the longest
        possible suffix and shrinks — matches only ever grow as the suffix
        shortens, so the first depth with exactly one match is the most
        specific unambiguous answer; the first depth with more than one
        match means it's genuinely ambiguous (e.g. two files sharing a
        basename), and shrinking further would never resolve that."""
        segments = [s for s in trace_path.replace("\\", "/").split("/") if s]
        if not segments:
            return None

        for depth in range(len(segments), 0, -1):
            suffix = segments[-depth:]
            matches = [
                fn for fn, segs in known_segments.items()
                if len(segs) >= depth and segs[-depth:] == suffix
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
        return None

    def _read_snippet(self, file_cache: dict, codebase_path: Path, filename: str, line_number: int) -> str | None:
        """Reads a window of source around line_number."""
        lines = self._get_file_lines(file_cache, codebase_path, filename)
        if lines is None:
            return None

        start = max(0, line_number - 1 - _SNIPPET_CONTEXT_LINES)
        end = min(len(lines), line_number + _SNIPPET_CONTEXT_LINES)
        return "\n".join(lines[start:end])


    async def explain_trace(
        self, project_id: str, raw_traceback: str, codebase_path: str,
        db_session, llm_service, max_hops: int = 5
    ) -> dict:
        from models.database import CodeSymbol
        from services.graph_tools import build_graph_tools

        frames = self._parse_frames(raw_traceback)
        if not frames:
            return {
                "explanation": (
                    'This doesn\'t look like a Python traceback — no \'File "...", '
                    "line N, in ...' frames were found. Paste the raw traceback text "
                    "as printed by Python."
                ),
                "resolved_frames": [],
                "used_agentic_tools": False,
            }

        known_filenames = {
            row[0] for row in
            db_session.query(CodeSymbol.filename)
            .filter(CodeSymbol.project_id == project_id)
            .distinct()
            .all()
        }
        known_segments = {
            fn: [s for s in fn.replace("\\", "/").split("/") if s]
            for fn in known_filenames
        }

        base = Path(codebase_path)
        file_cache: dict = {}

        matched = []
        for frame in frames:
            candidate = self._match_project_file(frame["raw_path"], known_segments)
            if not candidate:
                continue
            lines = self._get_file_lines(file_cache, base, candidate)
            if lines is not None and frame["line"] > len(lines):
                # Line number doesn't fit this file — a basename-only match
                # almost certainly picked up a same-named file from a
                # third-party package (e.g. uvicorn's own config.py
                # matching this project's config.py). Drop it rather than
                # attribute the frame to the wrong file.
                continue
            matched.append({**frame, "filename": candidate})
        matched = matched[-_MAX_RESOLVED_FRAMES:]

        symbol_rows = []
        if matched:
            matched_filenames = {f["filename"] for f in matched}
            symbol_rows = (
                db_session.query(CodeSymbol)
                .filter(
                    CodeSymbol.project_id == project_id,
                    CodeSymbol.filename.in_(matched_filenames),
                )
                .all()
            )

        resolved_frames = []
        for frame in matched:
            enclosing = min(
                (
                    s for s in symbol_rows
                    if s.filename == frame["filename"]
                    and s.start_line <= frame["line"] <= s.end_line
                ),
                key=lambda s: s.end_line - s.start_line,
                default=None,
            )
            snippet = self._read_snippet(file_cache, base, frame["filename"], frame["line"])
            resolved_frames.append({
                "filename": frame["filename"],
                "line": frame["line"],
                "function": frame["function"],
                "enclosing_symbol": enclosing.symbol_name if enclosing else None,
                "code_snippet": snippet,
            })

        frame_context = "\n\n".join(
            f"Frame: {f['filename']}:{f['line']} in {f['function']}"
            + (f" (enclosing symbol: {f['enclosing_symbol']})" if f["enclosing_symbol"] else "")
            + (f"\n```\n{f['code_snippet']}\n```" if f["code_snippet"] else "")
            for f in resolved_frames
        )

        no_local_frames_note = (
            "(No frames in this traceback map to files in this project — the "
            "failure appears to originate entirely outside the codebase. Explain "
            "the exception using the raw traceback and general Python knowledge only.)"
        )

        prompt = f"""You are a debugging assistant. A user hit the following exception:
        {raw_traceback}
        
Here is the source code context for each stack frame that maps to this project's codebase (frames from third-party libraries/stdlib are omitted):

{frame_context if frame_context else no_local_frames_note}

Explain:
1. What actually went wrong and why (the root cause).
2. Which project file/function is most likely responsible.
3. What else in the codebase could be affected by this failure (use the get_callers/get_callees tools if available to check what depends on the failing code).

Be factual — do not invent files, functions, or line numbers that weren't shown to you."""

        if resolved_frames:
            get_callers, get_callees = build_graph_tools(project_id, db_session)
            explanation = await llm_service.generate_with_tools(
                prompt=prompt,
                tools=[get_callers, get_callees],
                max_remote_calls=max_hops * 2,
            )
            used_agentic_tools = True
        else:
            explanation = await llm_service.generate_document(prompt)
            used_agentic_tools = False

        return {
            "explanation": explanation,
            "resolved_frames": resolved_frames,
            "used_agentic_tools": used_agentic_tools,
        }

    def _get_file_lines(self, file_cache: dict, codebase_path: Path, filename: str) -> list[str] | None:
        """Reads and caches a file's lines by filename, so both the
        line-count validation and snippet extraction share one disk read
        per file instead of two."""
        if filename not in file_cache:
            try:
                text = (codebase_path / filename).read_text(encoding="utf-8", errors="ignore")
                file_cache[filename] = text.splitlines()
            except OSError:
                file_cache[filename] = None
        return file_cache[filename]



