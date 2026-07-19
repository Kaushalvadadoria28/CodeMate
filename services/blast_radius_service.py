"""
Phase 8 — Blast Radius Checker (Impact Analysis Agent).

Gives Gemini two graph-query tools (get_callers, get_callees) via the
google-genai SDK's Automatic Function Calling (AFC) and lets the model
decide how far to traverse the CodeEdge graph before producing a
downstream-impact report for a target symbol. AFC handles the multi-step
tool-calling loop internally (default cap: 10 remote calls) — no manual
ReAct loop implemented here.
"""


class BlastRadiusService:

    async def generate_blast_radius(
        self, project_id: str, filename: str, symbol_name: str,
        db_session, llm_service, max_hops: int = 5
    ) -> str:
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

        prompt = f"""You are a code-impact analysis assistant. A developer is planning to change the symbol `{symbol_name}` in file `{filename}`.

Use the get_callers and get_callees tools to explore the codebase's call/import graph and determine the "blast radius" of this change: everything that could be affected.

Call get_callers repeatedly, following the chain outward (a caller's caller, and so on) up to about {max_hops} hops, to build a full picture of downstream impact. Stop once you've explored thoroughly or reach files with no further callers.

Produce a concise impact-analysis report covering:
1. Direct callers/dependents of `{symbol_name}`.
2. Transitive (indirect) impact further out in the graph.
3. An overall risk assessment (low/medium/high) based on how widely this symbol is depended upon.

Be factual and grounded only in what the tools return — do not invent files or symbols that weren't returned."""

        return await llm_service.generate_with_tools(
            prompt=prompt,
            tools=[get_callers, get_callees],
            max_remote_calls=max_hops * 2,
        )
