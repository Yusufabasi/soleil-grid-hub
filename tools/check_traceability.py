"""Check that the design package is complete and consistent.

    python tools/check_traceability.py [project_root]

Reads docs/*.md, docs/adr/*.md and api/openapi.yaml. Exits 1 if any ERROR is found.

Checks: every document exists; every FR has acceptance criteria and a release slice in the PRD; every
FR is served by an OpenAPI operation (x-fr) or documented as having none; every operation has
operationId, x-fr and x-roles; every FR and NFR has a test case, and every FR a negative one;
every FR and NFR appears in the implementation plan; every ADR reference resolves; no unknown
requirement ids; no placeholder text; relative links resolve.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_DOCS = [
    "00-shared-context.md",
    "01-prd.md",
    "02-system-architecture.md",
    "03-code-architecture.md",
    "04-data-model.md",
    "05-api-design.md",
    "06-ai-integration.md",
    "07-security-threat-model.md",
    "08-frontend-ux.md",
    "09-devops-sre.md",
    "10-test-strategy.md",
    "11-implementation-plan.md",
]
MODULES = [
    "identity",
    "customers",
    "payments",
    "ledger",
    "meters",
    "telemetry",
    "support",
    "ai_gateway",
    "analytics",
    "notifications",
    "reconciliation",
    "admin",
    "shared",
]
MIN_CRITERIA_PER_FR = 3
PLACEHOLDER = re.compile(r"\b(TBD|TODO|FIXME|lorem ipsum)\b|\bXXX\b", re.IGNORECASE)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")


@dataclass(frozen=True)
class Problem:
    severity: str  # "ERROR" or "WARN"
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity:5} {self.where}: {self.message}"


def requirement_ids(shared_text: str) -> tuple[list[str], list[str]]:
    """FR-nn and NFR-nn ids defined as the first cell of a table row in the shared context."""
    frs = re.findall(r"^\|\s*(FR-\d{2})\s*\|", shared_text, re.MULTILINE)
    nfrs = re.findall(r"^\|\s*(NFR-\d{2})\s*\|", shared_text, re.MULTILINE)
    return frs, nfrs


def parse_matrix(
    tests_text: str,
) -> dict[str, tuple[list[str], list[str], list[str], list[str]]] | None:
    """Rows of the test strategy's traceability matrix.

    Columns: requirement, test case ids, levels, descriptions, statuses. The four list columns
    hold one entry per test case, separated by <br> and in the same order.
    """
    heading = re.search(
        r"^##\s+\d*\.?\s*Traceability matrix\s*$", tests_text, re.MULTILINE
    )
    if heading is None:
        return None
    section = tests_text[heading.end() :]
    following = re.search(r"^## ", section, re.MULTILINE)
    if following:
        section = section[: following.start()]
    def cells_of(row: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
        parts = ([part.strip() for part in cell.split("<br>") if part.strip()] for cell in row)
        first, second, third, fourth = parts
        return first, second, third, fourth

    rows: dict[str, tuple[list[str], list[str], list[str], list[str]]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and re.fullmatch(r"N?FR-\d{2}", cells[0]):
            rows[cells[0]] = cells_of(cells[1:5])
    return rows


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def check(root: Path) -> list[Problem]:
    problems: list[Problem] = []
    docs = root / "docs"

    def err(where: str, message: str) -> None:
        problems.append(Problem("ERROR", where, message))

    texts: dict[str, str] = {}
    for name in REQUIRED_DOCS:
        text = _read(docs / name)
        if text is None:
            err(name, "document is missing")
        else:
            texts[name] = text
    if "00-shared-context.md" not in texts:
        return problems

    frs, nfrs = requirement_ids(texts["00-shared-context.md"])
    if len(frs) != len(set(frs)) or len(nfrs) != len(set(nfrs)):
        err("00-shared-context.md", "duplicate requirement ids")
    if not frs or not nfrs:
        err("00-shared-context.md", "no FR or NFR ids found")
    known = set(frs) | set(nfrs)

    # --- PRD: acceptance criteria and release slice for every FR ----------------------------
    prd = texts.get("01-prd.md")
    if prd is not None:
        for fr in frs:
            count = len(set(re.findall(rf"\bAC-{fr}-\d+\b", prd)))
            if count < MIN_CRITERIA_PER_FR:
                err(
                    "01-prd.md",
                    f"{fr} has {count} acceptance criteria, need at least {MIN_CRITERIA_PER_FR}",
                )
            if not re.search(rf"^\|[^\n]*\b{fr}\b[^\n]*\bR[0-4]\b", prd, re.MULTILINE):
                err("01-prd.md", f"{fr} has no release slice (R0..R4) in a table row")

    # --- OpenAPI: operations, x-fr coverage ---------------------------------------------------
    served: set[str] = set()
    spec_path = root / "api" / "openapi.yaml"
    if not spec_path.is_file():
        err("api/openapi.yaml", "specification is missing")
    else:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        seen_ids: set[str] = set()
        for path, item in (spec.get("paths") or {}).items():
            for method, op in item.items():
                if method not in {"get", "put", "post", "patch", "delete"}:
                    continue
                where = f"openapi {method.upper()} {path}"
                op_id = op.get("operationId")
                if not op_id:
                    err(where, "missing operationId")
                elif op_id in seen_ids:
                    err(where, f"duplicate operationId {op_id}")
                else:
                    seen_ids.add(op_id)
                x_fr = op.get("x-fr")
                if not x_fr and not op.get("x-nfr"):
                    err(where, "missing x-fr (or x-nfr for operational endpoints)")
                else:
                    for fr in x_fr or []:
                        if fr not in frs:
                            err(where, f"x-fr names unknown requirement {fr}")
                        served.add(fr)
                if not op.get("x-roles"):
                    err(where, "missing x-roles")
    api_doc = texts.get("05-api-design.md", "")
    for fr in frs:
        if fr in served:
            continue
        no_op = re.search(
            rf"^[^\n]*\b{fr}\b[^\n]*\b(no HTTP operation|no operation|no endpoint)\b",
            api_doc,
            re.MULTILINE | re.IGNORECASE,
        )
        if not no_op:
            err(
                "05-api-design.md",
                f"{fr} has no OpenAPI operation and no documented reason",
            )

    # --- Tests: every requirement has a case, every FR a negative case --------------------------
    tests = texts.get("10-test-strategy.md")
    test_ids: set[str] = set()
    if tests is not None:
        matrix = parse_matrix(tests)
        if matrix is None:
            err(
                "10-test-strategy.md",
                "no 'Traceability matrix' section with requirement rows",
            )
            matrix = {}
        for req in frs + nfrs:
            entry = matrix.get(req)
            if entry is None:
                err(
                    "10-test-strategy.md",
                    f"{req} has no row in the traceability matrix",
                )
                continue
            ids, levels, descriptions, statuses = entry
            if not (len(ids) == len(levels) == len(descriptions) == len(statuses)):
                err(
                    "10-test-strategy.md",
                    f"{req} columns are misaligned: {len(ids)} ids, {len(levels)} levels, "
                    f"{len(descriptions)} descriptions, {len(statuses)} statuses",
                )
            for tc in ids:
                if not tc.startswith(f"TC-{req}-"):
                    err(
                        "10-test-strategy.md",
                        f"{req} lists {tc!r}, which is not a TC-{req}-n id",
                    )
                else:
                    test_ids.add(tc)
            if not ids:
                err("10-test-strategy.md", f"{req} has no test case")
            elif req in frs and not any(d.startswith("NEG:") for d in descriptions):
                err(
                    "10-test-strategy.md",
                    f"{req} has no negative case (description starting NEG:)",
                )

    # --- Implementation plan ------------------------------------------------------------------
    plan = texts.get("11-implementation-plan.md")
    if plan is not None:
        for req in frs + nfrs:
            if not re.search(rf"\b{req}\b", plan):
                err("11-implementation-plan.md", f"{req} is not planned")

    # --- Code architecture names every module ----------------------------------------------------
    code = texts.get("03-code-architecture.md")
    if code is not None:
        for module in MODULES:
            if module not in code:
                err("03-code-architecture.md", f"module {module} is not described")

    # --- ADR references ----------------------------------------------------------------------------
    adr_dir = docs / "adr"
    adr_ids: set[str] = set()
    if adr_dir.is_dir():
        for adr in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
            adr_ids.add(f"ADR-{adr.name[:4]}")
            if not re.search(
                r"^\*\*Status\*\*:", adr.read_text(encoding="utf-8"), re.MULTILINE
            ):
                err(f"adr/{adr.name}", "no Status line")
    else:
        err("docs/adr", "no ADR directory")

    # --- cross-document hygiene -----------------------------------------------------------------------
    all_docs = {p.relative_to(docs).as_posix(): p for p in docs.rglob("*.md")}
    for rel, path in sorted(all_docs.items()):
        text = path.read_text(encoding="utf-8")
        for ref in sorted(set(re.findall(r"\bADR-\d{4}\b", text))):
            if ref not in adr_ids:
                err(rel, f"references {ref} which does not exist")
        for ref in sorted(set(re.findall(r"\b(?:N?FR)-\d{2}\b", text))):
            if ref not in known:
                err(rel, f"references unknown requirement {ref}")
        if rel not in {"00-shared-context.md", "12-review-notes.md"}:
            for ref in sorted(set(re.findall(r"\bTC-(?:N?FR)-\d{2}-\d+\b", text))):
                if test_ids and ref not in test_ids and rel != "10-test-strategy.md":
                    err(
                        rel,
                        f"references test case {ref} which is not in the test strategy",
                    )
        match = PLACEHOLDER.search(text)
        if match and rel not in {"12-review-notes.md"}:
            err(rel, f"placeholder text left in the document ({match.group(0)!r})")
        for target in LINK.findall(text):
            if re.match(r"^[a-z]+:", target):
                continue
            if not (path.parent / target).resolve().exists():
                err(rel, f"broken link {target}")
    return problems


def main(argv: list[str] | None = None) -> int:
    root = Path((argv or sys.argv[1:] or ["."])[0]).resolve()
    problems = check(root)
    for problem in problems:
        print(problem)
    errors = [p for p in problems if p.severity == "ERROR"]
    print(f"\n{len(errors)} error(s), {len(problems) - len(errors)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
