"""
Shared CodeEdge graph-query tools for any Gemini Automatic Function
Calling (AFC) agent that needs to traverse caller/callee relationships.
Extracted out of blast_radius_service.py in Phase 9 so Blast Radius and
the Stack Trace Explainer don't maintain two copies of the same closures.
"""


def build_graph_tools(project_id: str, db_session):
    """Returns (get_callers, get_callees) closures scoped to one
    project_id/db_session, ready to pass to LLMService.generate_with_tools."""
    from models.database import CodeEdge

    async def get_callers(filename: str, symbol_name: str) -> list[dict]:
        """Find everything that calls, imports, or otherwise references
        the given symbol. Use this to discover what would break if this
        symbol's behavior changes or it's removed.

        Args:
            filename: relative path of the file containing the symbol
            symbol_name: name of the function/method/class/variable being referenced
        """
        rows = (
            db_session.query(CodeEdge)
            .filter(
                CodeEdge.project_id == project_id,
                CodeEdge.target_file == filename,
                CodeEdge.target_symbol == symbol_name,
            )
            .limit(50)
            .all()
        )
        return [
            {"source_file": r.source_file, "source_symbol": r.source_symbol, "edge_type": r.edge_type}
            for r in rows
        ]

    async def get_callees(filename: str, symbol_name: str) -> list[dict]:
        """Find everything that the given symbol itself calls or
        imports. Use this to understand what the symbol depends on.

        Args:
            filename: relative path of the file containing the symbol
            symbol_name: name of the function/method/class being inspected
        """
        rows = (
            db_session.query(CodeEdge)
            .filter(
                CodeEdge.project_id == project_id,
                CodeEdge.source_file == filename,
                CodeEdge.source_symbol == symbol_name,
            )
            .limit(50)
            .all()
        )
        return [
            {"target_file": r.target_file, "target_symbol": r.target_symbol, "edge_type": r.edge_type}
            for r in rows
        ]

    return get_callers, get_callees