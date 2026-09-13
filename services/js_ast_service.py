"""
Feature 6 — Multi-Language AST support (JavaScript/TypeScript).

Standalone tree-sitter-based extractor mirroring ASTIndexerService's shape
(parse_codebase() -> (symbol_rows, edge_rows, skipped_files)) so it can
populate the same language-agnostic CodeSymbol/CodeEdge tables. Runs at
upload time via asyncio.to_thread, same as ast_service.py — a different,
already-abandoned integration point than CocoIndex's chunking DAG (where
tree-sitter was dropped once before, due to DAG construction errors); this
extractor never touches CocoIndex at all.

v1 scope (deliberate): symbol extraction (functions/classes/methods/
interfaces/type aliases/function-valued consts) and import edges only.
Call-edge resolution is deferred, same precedent as Phase 5 shipping
symbols+imports before Phase 8 added call resolution for Python.

Import resolution is a bounded heuristic: only relative specifiers
(./foo, ../foo) are resolved, against files actually present on disk.
Bare specifiers (npm packages) and tsconfig.json path-alias imports
(e.g. `@/utils/foo`) are NOT resolved — recorded as external, same as an
unresolvable Python import.
"""

import os
from pathlib import Path
from uuid import uuid4

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser

from services.ast_service import ASTIndexerService

_JS_LANGUAGE = Language(tsjs.language())
_TS_LANGUAGE = Language(tsts.language_typescript())
_TSX_LANGUAGE = Language(tsts.language_tsx())

_LANGUAGE_BY_EXT = {
    ".js": _JS_LANGUAGE,
    ".jsx": _JS_LANGUAGE,
    ".ts": _TS_LANGUAGE,
    ".tsx": _TSX_LANGUAGE,
}

_FUNCTION_VALUE_TYPES = ("arrow_function", "function_expression")

_RESOLVE_CANDIDATES = [
    "", ".ts", ".tsx", ".js", ".jsx",
    "/index.ts", "/index.tsx", "/index.js", "/index.jsx",
]


class JSASTIndexerService:
    """Public entry point, mirrors ASTIndexerService's shape. No DB
    dependency in parsing — main.py owns writing the returned rows."""

    EXCLUDED_DIRS = ASTIndexerService.EXCLUDED_DIRS

    def __init__(self):
        self._parsers = {ext: Parser(lang) for ext, lang in _LANGUAGE_BY_EXT.items()}

    def _iter_source_files(self, codebase_path: str):
        base = Path(codebase_path)
        for root, dirs, files in os.walk(codebase_path):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIRS]
            for fname in files:
                ext = Path(fname).suffix
                if ext in _LANGUAGE_BY_EXT:
                    abs_path = Path(root) / fname
                    yield ext, abs_path, abs_path.relative_to(base).as_posix()

    def parse_codebase(self, project_id: str, codebase_path: str) -> tuple[list[dict], list[dict], list[dict]]:
        """Returns (symbol_rows, edge_rows, skipped_files) — same contract
        as ASTIndexerService.parse_codebase()."""
        files = list(self._iter_source_files(codebase_path))
        known_files = {rel_path for _, _, rel_path in files}

        symbol_rows: list[dict] = []
        edge_rows: list[dict] = []
        seen_edges: set[tuple] = set()
        skipped_files: list[dict] = []

        for ext, abs_path, rel_path in files:
            parser = self._parsers[ext]

            try:
                source = abs_path.read_bytes()
            except OSError as e:
                skipped_files.append({"filename": rel_path, "reason": f"{type(e).__name__}: {e}"})
                continue

            tree = parser.parse(source)
            root = tree.root_node

            if root.has_error:
                skipped_files.append({
                    "filename": rel_path,
                    "reason": "tree-sitter reported one or more syntax errors; "
                              "extraction proceeded on the parts that parsed cleanly",
                })

            symbol_rows.extend(self._extract_symbols(project_id, rel_path, root))

            for edge in self._extract_import_edges(project_id, rel_path, root, known_files):
                key = (edge["source_file"], edge["target_file"], edge["edge_type"],
                       edge["source_symbol"], edge["target_symbol"])
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edge_rows.append(edge)

        return symbol_rows, edge_rows, skipped_files

    def _extract_symbols(self, project_id: str, rel_path: str, root) -> list[dict]:
        rows: list[dict] = []
        self._walk_symbols(root, project_id, rel_path, rows, func_depth=0)
        return rows

    def _walk_symbols(self, node, project_id: str, rel_path: str, rows: list[dict], func_depth: int):
        for child in node.children:
            t = child.type

            if t == "class_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    rows.append(self._row(project_id, rel_path, name_node.text.decode(), "class", child))
                self._walk_symbols(child, project_id, rel_path, rows, func_depth)

            elif t == "method_definition":
                name_node = child.child_by_field_name("name")
                if name_node:
                    rows.append(self._row(project_id, rel_path, name_node.text.decode(), "method", child))
                self._walk_symbols(child, project_id, rel_path, rows, func_depth + 1)

            elif t in ("function_declaration", "function_expression", "arrow_function"):
                name_node = child.child_by_field_name("name")  # None for anonymous/arrow
                if name_node:
                    rows.append(self._row(project_id, rel_path, name_node.text.decode(), "function", child))
                self._walk_symbols(child, project_id, rel_path, rows, func_depth + 1)

            elif t == "interface_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    rows.append(self._row(project_id, rel_path, name_node.text.decode(), "interface", child))

            elif t == "type_alias_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    rows.append(self._row(project_id, rel_path, name_node.text.decode(), "type_alias", child))

            elif t == "lexical_declaration":
                for declarator in child.children:
                    if declarator.type != "variable_declarator":
                        continue
                    name_node = declarator.child_by_field_name("name")
                    value_node = declarator.child_by_field_name("value")
                    if not name_node or name_node.type != "identifier":
                        continue
                    if self._is_function_like(value_node):
                        rows.append(self._row(project_id, rel_path, name_node.text.decode(), "function", declarator))
                        self._walk_symbols(value_node, project_id, rel_path, rows, func_depth + 1)
                    elif func_depth == 0:
                        rows.append(self._row(project_id, rel_path, name_node.text.decode(), "variable", declarator))

            else:
                self._walk_symbols(child, project_id, rel_path, rows, func_depth)

    def _extract_import_edges(self, project_id: str, rel_path: str, root, known_files: set[str]) -> list[dict]:
        edges: list[dict] = []
        self._walk_imports(root, project_id, rel_path, known_files, edges)
        return edges

    def _walk_imports(self, node, project_id: str, rel_path: str, known_files: set[str], edges: list[dict]):
        for child in node.children:
            if child.type == "import_statement":
                source_node = child.child_by_field_name("source")
                if source_node:
                    specifier = source_node.text.decode().strip("'\"")
                    target_file = self._resolve_import(rel_path, specifier, known_files)
                    edges.append({
                        "id": str(uuid4()),
                        "project_id": project_id,
                        "source_file": rel_path,
                        "target_file": target_file,
                        "edge_type": "import",
                        "source_symbol": None,
                        "target_symbol": None,
                        "raw_reference": specifier,
                    })
            self._walk_imports(child, project_id, rel_path, known_files, edges)

    def _resolve_import(self, source_rel_path: str, specifier: str, known_files: set[str]) -> str | None:
        """Resolves a relative import specifier (./foo, ../foo) against
        the importing file's directory. Bare specifiers (npm packages)
        and tsconfig.json path-alias imports are deliberately NOT
        handled — returns None (external/unresolved), same convention as
        a Python import that doesn't match the project's module map."""
        if not specifier.startswith("."):
            return None

        source_dir = str(Path(source_rel_path).parent)
        candidate_base = os.path.normpath(os.path.join(source_dir, specifier)).replace(os.sep, "/")

        for suffix in _RESOLVE_CANDIDATES:
            candidate = f"{candidate_base}{suffix}"
            if candidate in known_files:
                return candidate
        return None

    def _row(self, project_id: str, rel_path: str, symbol_name: str, symbol_type: str, node) -> dict:
        return {
            "id": str(uuid4()),
            "project_id": project_id,
            "filename": rel_path,
            "symbol_name": symbol_name,
            "symbol_type": symbol_type,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
        }

    def _is_function_like(self, value_node) -> bool:
        """True for a direct function/arrow-function value, or a call
        expression wrapping one as an argument (useCallback(fn, deps),
        useMemo(fn, deps), memo(Component), forwardRef(fn), etc.) — the
        dominant idiom for defining named functions in React/hooks code,
        where the actual callable is one level removed from a bare
        arrow_function/function_expression."""
        if value_node is None:
            return False
        if value_node.type in _FUNCTION_VALUE_TYPES:
            return True
        if value_node.type == "call_expression":
            args_node = value_node.child_by_field_name("arguments")
            if args_node:
                return any(arg.type in _FUNCTION_VALUE_TYPES for arg in args_node.children)
        return False
