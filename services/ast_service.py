"""
Phase 5 — AST Context Map service.

Parses a project's Python files into a lightweight symbol + edge graph
(functions/classes defined per file, import edges, resolved call edges).
Used at index time to populate CodeSymbol/CodeEdge, and at query time to
build a compact "context map" injected into the LLM prompt alongside the
vector-search results.

Design note: uses the stdlib `ast` module rather than tree-sitter — see
Phase 5 design notes in the LLD. Python-only for this phase; the schema
this produces (CodeSymbol/CodeEdge) is language-agnostic, so a tree-sitter
extractor for other languages can populate the same tables later.
"""

import ast
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_


class _SymbolVisitor(ast.NodeVisitor):
    """Extracts function/class/method definitions from a single file's AST.

    Also captures top-level (module- or class-body-scope) variable
    assignments as "variable" symbols — e.g. `agent = Agent(...)` — since
    declarative-style code defines no def/class but is still a meaningful
    unit other phases (dead-code detection, onboarding gen) need to see.
    Function-local assignments are deliberately excluded (tracked via
    `_func_depth`) to avoid flooding the table with local variable noise.
    """

    def __init__(self, project_id: str, rel_path: str):
        self.project_id = project_id
        self.rel_path = rel_path
        self.symbols: list[dict] = []
        self._class_stack: list[str] = []
        self._func_depth = 0

    def visit_ClassDef(self, node: ast.ClassDef):
        self.symbols.append(self._row(node.name, "class", node))
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node):
        symbol_type = "method" if self._class_stack else "function"
        self.symbols.append(self._row(node.name, symbol_type, node))
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign):
        if self._func_depth == 0:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.symbols.append(self._row(target.id, "variable", node))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if self._func_depth == 0 and isinstance(node.target, ast.Name):
            self.symbols.append(self._row(node.target.id, "variable", node))
        self.generic_visit(node)

    def _row(self, symbol_name: str, symbol_type: str, node) -> dict:
        return {
            "id": str(uuid4()),
            "project_id": self.project_id,
            "filename": self.rel_path,
            "symbol_name": symbol_name,
            "symbol_type": symbol_type,
            "start_line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
        }


class _ImportVisitor(ast.NodeVisitor):
    """Extracts import statements and resolves them against the project's
    module map where possible. Also builds a local_name -> (file, symbol)
    map used later to resolve call sites in the same file."""

    def __init__(self, project_id: str, rel_path: str, dotted_module: str, module_map: dict[str, str]):
        self.project_id = project_id
        self.rel_path = rel_path
        self.dotted_module = dotted_module
        self.module_map = module_map
        self.edges: list[dict] = []
        self.resolved_imports: dict[str, tuple[str, str | None]] = {}

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            target_file = self.module_map.get(alias.name)
            local_name = alias.asname or alias.name.split(".")[0]
            self.edges.append(self._edge(target_file, None, alias.name))
            if target_file:
                self.resolved_imports[local_name] = (target_file, None)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.level and node.level > 0:
            target_module = self._resolve_relative(node.module, node.level)
        else:
            target_module = node.module

        target_file = self.module_map.get(target_module) if target_module else None

        for alias in node.names:
            local_name = alias.asname or alias.name
            self.edges.append(self._edge(target_file, alias.name, target_module or node.module or "?"))
            if target_file:
                self.resolved_imports[local_name] = (target_file, alias.name)

    def _resolve_relative(self, module: str | None, level: int) -> str | None:
        parts = self.dotted_module.split(".")
        base_parts = parts[:-level] if level <= len(parts) else []
        if module:
            base_parts = base_parts + module.split(".")
        return ".".join(base_parts) if base_parts else None

    def _edge(self, target_file: str | None, target_symbol: str | None, raw_reference: str) -> dict:
        return {
            "id": str(uuid4()),
            "project_id": self.project_id,
            "source_file": self.rel_path,
            "target_file": target_file,
            "edge_type": "import",
            "source_symbol": None,
            "target_symbol": target_symbol,
            "raw_reference": raw_reference,
        }


class _CallVisitor(ast.NodeVisitor):
    """Walks function bodies and records call sites that resolve to either
    a locally-defined symbol or an imported symbol. Unresolved calls
    (builtins, stdlib, untyped attribute access) are dropped rather than
    stored — keeps the edge table signal-heavy instead of noisy."""

    def __init__(self, project_id: str, rel_path: str, local_symbols: set[str],
                 resolved_imports: dict[str, tuple[str, str | None]]):
        self.project_id = project_id
        self.rel_path = rel_path
        self.local_symbols = local_symbols
        self.resolved_imports = resolved_imports
        self.edges: list[dict] = []
        self._func_stack: list[str] = []

    def visit_FunctionDef(self, node):
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call):
        called_name = self._resolve_call_name(node.func)
        if called_name:
            edge = self._try_build_edge(called_name)
            if edge:
                self.edges.append(edge)
        self.generic_visit(node)

    def _resolve_call_name(self, func_node) -> str | None:
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            return func_node.attr  # self.method() -> "method", module.func() -> "func"
        return None

    def _try_build_edge(self, called_name: str) -> dict | None:
        source_symbol = self._func_stack[-1] if self._func_stack else None

        if called_name in self.local_symbols:
            return self._row(self.rel_path, called_name, source_symbol, called_name)

        if called_name in self.resolved_imports:
            target_file, target_symbol = self.resolved_imports[called_name]
            return self._row(target_file, target_symbol or called_name, source_symbol, called_name)

        return None

    def _row(self, target_file, target_symbol, source_symbol, raw_reference) -> dict:
        return {
            "id": str(uuid4()),
            "project_id": self.project_id,
            "source_file": self.rel_path,
            "target_file": target_file,
            "edge_type": "calls",
            "source_symbol": source_symbol,
            "target_symbol": target_symbol,
            "raw_reference": raw_reference,
        }


class ASTIndexerService:
    """Public entry point. No DB dependency in parsing (keeps it testable
    in isolation) — main.py owns writing the returned rows to the DB."""

    EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", "venv", ".venv", "dist", "build"}

    def build_module_map(self, codebase_path: str) -> dict[str, str]:
        """dotted module path -> relative filename, e.g.
        'services.rag_service' -> 'services/rag_service.py'"""
        module_map: dict[str, str] = {}
        base = Path(codebase_path)

        for root, dirs, files in os.walk(codebase_path):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                abs_path = Path(root) / fname
                rel_path = abs_path.relative_to(base).as_posix()

                if fname == "__init__.py":
                    dotted = str(abs_path.relative_to(base).parent).replace(os.sep, ".")
                    if dotted in (".", ""):
                        continue
                else:
                    dotted = rel_path[:-3].replace("/", ".")

                module_map[dotted] = rel_path

        return module_map

    def parse_codebase(self, project_id: str, codebase_path: str) -> tuple[list[dict], list[dict], list[dict]]:
        """Returns (symbol_rows, edge_rows, skipped_files).
        skipped_files is a list of {"filename": str, "reason": str} for
        files that failed to parse — surfaced to the API so upload results
        aren't silently thinner than the actual codebase."""
        module_map = self.build_module_map(codebase_path)
        base = Path(codebase_path)

        symbol_rows: list[dict] = []
        edge_rows: list[dict] = []
        seen_edges: set[tuple] = set()
        skipped_files: list[dict] = []

        for dotted_module, rel_path in module_map.items():
            abs_path = base / rel_path
            try:
                source = abs_path.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=rel_path)
            except (SyntaxError, UnicodeDecodeError, ValueError) as e:
                reason = f"{type(e).__name__}: {e}"
                print(f"AST parse skipped for {rel_path}: {reason}")
                skipped_files.append({"filename": rel_path, "reason": reason})
                continue

            sym_visitor = _SymbolVisitor(project_id, rel_path)
            sym_visitor.visit(tree)
            symbol_rows.extend(sym_visitor.symbols)
            local_symbol_names = {s["symbol_name"] for s in sym_visitor.symbols}

            imp_visitor = _ImportVisitor(project_id, rel_path, dotted_module, module_map)
            imp_visitor.visit(tree)

            call_visitor = _CallVisitor(project_id, rel_path, local_symbol_names, imp_visitor.resolved_imports)
            call_visitor.visit(tree)

            for edge in imp_visitor.edges + call_visitor.edges:
                key = (
                    edge["source_file"], edge["target_file"], edge["edge_type"],
                    edge["source_symbol"], edge["target_symbol"]
                )
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edge_rows.append(edge) 

        return symbol_rows, edge_rows, skipped_files

    def build_context_map(self, project_id: str, filenames: list[str], db_session, hop_limit: int = 30) -> str:
        """1-hop neighbor lookup for a set of files, rendered as compact
        text for LLM prompt injection. hop_limit caps token budget impact."""
        from models.database import CodeEdge

        if not filenames:
            return ""

        edges = (
            db_session.query(CodeEdge)
            .filter(
                CodeEdge.project_id == project_id,
                or_(
                    CodeEdge.source_file.in_(filenames),
                    CodeEdge.target_file.in_(filenames),
                ),
            )
            .limit(hop_limit)
            .all()
        )

        if not edges:
            return ""

        lines = []
        for e in edges:
            if e.edge_type == "import":
                if e.target_file:
                    ref = f" ({e.target_symbol})" if e.target_symbol else ""
                    lines.append(f"{e.source_file} imports{ref} from {e.target_file}")
                else:
                    lines.append(f"{e.source_file} imports external module '{e.raw_reference}'")
            elif e.edge_type == "calls" and e.target_file:
                caller = f"{e.source_file}:{e.source_symbol}" if e.source_symbol else e.source_file
                lines.append(f"{caller} calls {e.target_symbol} in {e.target_file}")

        return "\n".join(lines)