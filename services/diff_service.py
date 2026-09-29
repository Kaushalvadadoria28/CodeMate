"""
Feature 5 — Re-Index Diffing ("What Changed Since Last Upload").

Compares two projects' CodeSymbol/CodeEdge rows — added/removed symbols
and edges, plus a cheap "likely modified" signal for symbols whose
start_line/end_line shifted between the two uploads. Not a real content
diff (that's Feature 8's job, on a pasted unified diff) — this compares
the AST graph's own extracted structure.

Filenames are normalized per-project before comparing: each project may
have been zipped with a differently-named top-level wrapping folder
(e.g. re-zipping "the same" codebase later can easily produce a
different wrapper name), which would otherwise make every symbol look
removed-then-added even when nothing changed. Reuses
ASTIndexerService.detect_synthetic_prefix() (already built/verified for
Feature 6's import resolution) to strip each project's own wrapping
prefix before joining on filename.

The caller is responsible for the assumption that old_project_id and
new_project_id are "the same" codebase at two points in time — no
lineage/relatedness is inferred or verified here.
"""


class DiffService:

    def diff_projects(self, old_project_id: str, new_project_id: str, db_session, ast_service) -> dict:
        from models.database import CodeEdge

        old_prefix, old_symbols = self._load_symbols(old_project_id, db_session, ast_service)
        new_prefix, new_symbols = self._load_symbols(new_project_id, db_session, ast_service)

        old_by_key = {self._symbol_key(s, old_prefix): s for s in old_symbols}
        new_by_key = {self._symbol_key(s, new_prefix): s for s in new_symbols}

        added_symbols = [
            self._symbol_entry(s, new_prefix) for k, s in new_by_key.items() if k not in old_by_key
        ]
        removed_symbols = [
            self._symbol_entry(s, old_prefix) for k, s in old_by_key.items() if k not in new_by_key
        ]
        modified_symbols = []
        for k, new_s in new_by_key.items():
            old_s = old_by_key.get(k)
            if old_s and (old_s.start_line != new_s.start_line or old_s.end_line != new_s.end_line):
                modified_symbols.append({
                    "filename": self._strip(new_s.filename, new_prefix),
                    "symbol_name": new_s.symbol_name,
                    "symbol_type": new_s.symbol_type,
                    "old_start_line": old_s.start_line,
                    "old_end_line": old_s.end_line,
                    "new_start_line": new_s.start_line,
                    "new_end_line": new_s.end_line,
                })

        old_edges = db_session.query(CodeEdge).filter(CodeEdge.project_id == old_project_id).all()
        new_edges = db_session.query(CodeEdge).filter(CodeEdge.project_id == new_project_id).all()

        old_edge_keys = {self._edge_key(e, old_prefix) for e in old_edges}
        new_edge_keys = {self._edge_key(e, new_prefix) for e in new_edges}

        added_edges = [
            self._edge_entry(e, new_prefix) for e in new_edges
            if self._edge_key(e, new_prefix) not in old_edge_keys
        ]
        removed_edges = [
            self._edge_entry(e, old_prefix) for e in old_edges
            if self._edge_key(e, old_prefix) not in new_edge_keys
        ]

        return {
            "added_symbols": added_symbols,
            "removed_symbols": removed_symbols,
            "modified_symbols": modified_symbols,
            "added_edges": added_edges,
            "removed_edges": removed_edges,
        }

    def _load_symbols(self, project_id: str, db_session, ast_service):
        from models.database import CodeSymbol
        symbols = db_session.query(CodeSymbol).filter(CodeSymbol.project_id == project_id).all()
        filenames = {s.filename for s in symbols}
        prefix = ast_service.detect_synthetic_prefix(dict(enumerate(filenames))) if filenames else None
        return prefix, symbols

    def _strip(self, filename: str, prefix: str | None) -> str:
        if prefix and filename.startswith(f"{prefix}/"):
            return filename[len(prefix) + 1:]
        return filename

    def _symbol_key(self, s, prefix) -> tuple:
        return (self._strip(s.filename, prefix), s.symbol_name, s.symbol_type)

    def _symbol_entry(self, s, prefix) -> dict:
        return {"filename": self._strip(s.filename, prefix), "symbol_name": s.symbol_name, "symbol_type": s.symbol_type}

    def _edge_key(self, e, prefix) -> tuple:
        return (
            self._strip(e.source_file, prefix),
            self._strip(e.target_file, prefix) if e.target_file else None,
            e.edge_type, e.source_symbol, e.target_symbol,
        )

    def _edge_entry(self, e, prefix) -> dict:
        return {
            "source_file": self._strip(e.source_file, prefix),
            "target_file": self._strip(e.target_file, prefix) if e.target_file else None,
            "edge_type": e.edge_type,
            "source_symbol": e.source_symbol,
            "target_symbol": e.target_symbol,
        }