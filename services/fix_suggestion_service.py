"""
Feature 4 — "Suggest a Fix" Mode.

Given an already-computed Blast Radius or Stack Trace Explainer result,
asks Gemini to draft a unified-diff fix suggestion — never applied
automatically, always returned for the user to review. Reuses whichever
context the caller already computed rather than re-running the agentic
exploration a second time.

Passes the WHOLE target file as context, not just the isolated symbol's
own lines — a unified diff has to reference the file's real surrounding
lines (imports, module-level constants, neighboring code) to be
appliable at all. An isolated function snippet caused Gemini to
fabricate plausible-but-fake definitions for module-level names the
function referenced (e.g. inventing a DB_PATH value that doesn't exist
in the real file) — confirmed via direct testing against a real file.

Blast Radius reports impact of a *planned* change, not a known bug —
there's no "failing code" in that case. Framed as a suggested safe
modification considering downstream impact, not a bug fix, to avoid
claiming a defect that was never identified.
"""

from pathlib import Path

_DIFF_MARKERS = ("--- a/", "+++ b/", "@@ ")


class FixSuggestionService:

    async def suggest_fix(
        self, project_id: str, source: str, context: dict, codebase_path: str,
        llm_service,
    ) -> dict:
        if source == "trace":
            code_block, location_desc = self._trace_context(context, codebase_path)
            prior_analysis = context.get("explanation", "")
        else:
            code_block, location_desc = self._blast_radius_context(context, codebase_path)
            prior_analysis = context.get("impact_report", "")

        prompt = f"""You are a senior software engineer reviewing a reported issue.

{location_desc}

Full file content:
{code_block}



Prior analysis:
{prior_analysis}

Respond in EXACTLY this format, nothing else:

EXPLANATION:
<1-3 sentences: what's wrong (or what should change) and what your fix does>

DIFF:
<a unified diff only, standard --- a/file / +++ b/file / @@ hunk format, using the real lines shown above>

This is a SUGGESTION for a human to review — never claim it has been applied."""

        raw = await llm_service.generate_document(prompt)
        explanation, suggested_diff = self._parse_response(raw)
        diff_may_be_invalid = not all(marker in suggested_diff for marker in _DIFF_MARKERS)

        return {
            "explanation": explanation,
            "suggested_diff": suggested_diff,
            "diff_may_be_invalid": diff_may_be_invalid,
        }

    def _trace_context(self, context: dict, codebase_path: str):
        frames = context.get("resolved_frames") or []
        if not frames:
            return "(no resolved frames available)", "No project file could be matched to this traceback."

        primary = frames[-1]
        code_block = self._read_full_file(codebase_path, primary["filename"])
        location_desc = f"Failing frame: {primary['filename']}:{primary['line']} in {primary['function']}"
        return code_block, location_desc

    def _blast_radius_context(self, context: dict, codebase_path: str):
        code_block = self._read_full_file(codebase_path, context["filename"])
        location_desc = (
            f"Target symbol: {context['symbol_name']} in {context['filename']} "
            f"(considering downstream impact below — not a known bug)"
        )
        return code_block, location_desc

    def _read_full_file(self, codebase_path: str, filename: str) -> str:
        try:
            return (Path(codebase_path) / filename).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return "(file unavailable)"

    def _parse_response(self, raw: str) -> tuple[str, str]:
        if "DIFF:" in raw:
            before, _, after = raw.partition("DIFF:")
            explanation = before.replace("EXPLANATION:", "").strip()
            diff = after.strip()
        else:
            explanation = ""
            diff = raw.strip()
        return explanation, diff