"""
Phase 7 — Automated Onboarding & Architecture Generation.

Builds a directory tree + dependency manifest summary + AST symbol
summary, feeds it to Gemini to produce a README-style architecture doc,
and bundles a non-fatal CVE scan of declared dependencies via OSV.dev's
public batch API.
"""


import json
import os
import re
from pathlib import Path

import httpx

from services.ast_service import ASTIndexerService

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"


class OnboardingService:

    def build_directory_tree(self, codebase_path: str, max_entries: int = 200) -> str:
        base = Path(codebase_path)
        lines = []
        count = 0
        for root, dirs, files in os.walk(codebase_path):
            dirs[:] = sorted(d for d in dirs if d not in ASTIndexerService.EXCLUDED_DIRS)
            rel_root = Path(root).relative_to(base)
            depth = len(rel_root.parts)
            indent = "  " * depth
            if str(rel_root) != ".":
                lines.append(f"{indent}{rel_root.name}/")
                count += 1
            for fname in sorted(files):
                lines.append(f"{indent}  {fname}")
                count += 1
                if count >= max_entries:
                    lines.append(f"{indent}  ... (truncated at {max_entries} entries)")
                    return "\n".join(lines)
        return "\n".join(lines)

    def read_manifests(self, codebase_path: str) -> dict:
        base = Path(codebase_path)
        manifests = {"pypi": {}, "npm": {}}

        for req_file in base.rglob("requirements.txt"):
            for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|<=|~=)?\s*([A-Za-z0-9_.\-]+)?", line)
                if match:
                    name, _, version = match.groups()
                    manifests["pypi"][name.lower()] = version

        for pkg_file in base.rglob("package.json"):
            try:
                data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for name, version in deps.items():
                manifests["npm"][name] = re.sub(r"^[^\d]*", "", version) or None

        return manifests

    async def scan_vulnerabilities(self, manifests: dict, timeout: float = 10.0) -> tuple[list[dict], bool]:
        """Non-fatal: network/API failures degrade to an empty result
        rather than blocking onboarding doc generation."""
        queries = []
        for ecosystem, packages in manifests.items():
            osv_ecosystem = "PyPI" if ecosystem == "pypi" else "npm"
            for name, version in packages.items():
                query = {"package": {"name": name, "ecosystem": osv_ecosystem}}
                if version:
                    query["version"] = version
                queries.append(query)

        if not queries:
            return [], False

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(OSV_BATCH_URL, json={"queries": queries})
                response.raise_for_status()
                results = response.json().get("results", [])
        except Exception as e:
            print(f"CVE scan failed (non-fatal): {e}")
            return [], True

        vulnerabilities = []
        for query, result in zip(queries, results):
            for vuln in result.get("vulns", []):
                vulnerabilities.append({
                    "package": query["package"]["name"],
                    "ecosystem": query["package"]["ecosystem"],
                    "version": query.get("version"),
                    "vulnerability_id": vuln.get("id"),
                    "summary": vuln.get("summary"),
                })

        return vulnerabilities, False

    async def generate_architecture_doc(self, project_id: str, codebase_path: str, db_session, llm_service) -> dict:
        from models.database import CodeSymbol

        tree = self.build_directory_tree(codebase_path)
        manifests = self.read_manifests(codebase_path)
        vulnerabilities, degraded = await self.scan_vulnerabilities(manifests)

        symbol_rows = (
            db_session.query(CodeSymbol.filename, CodeSymbol.symbol_type)
            .filter(CodeSymbol.project_id == project_id)
            .all()
        )
        by_file = {}
        for filename, symbol_type in symbol_rows:
            by_file.setdefault(filename, []).append(symbol_type)
        symbol_summary = "\n".join(
            f"{fname}: {len(types)} symbols ({', '.join(sorted(set(types)))})"
            for fname, types in sorted(by_file.items())
        ) or "No symbols indexed."

        prompt = f"""Generate a concise architecture/onboarding markdown document for this codebase.

DIRECTORY STRUCTURE:
{tree}

DEPENDENCIES:
PyPI: {manifests['pypi']}
npm: {manifests['npm']}

SYMBOL SUMMARY (per file):
{symbol_summary}

Write a README-style overview covering: what the project does, its architecture/layers, and key modules. Keep it factual and grounded only in the information given above."""

        architecture_doc = await llm_service.generate_document(prompt)

        return {
            "architecture_doc": architecture_doc,
            "dependencies": manifests,
            "vulnerabilities": vulnerabilities,
            "vulnerability_scan_degraded": degraded,
        }