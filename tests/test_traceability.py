import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from check_traceability import MODULES, REQUIRED_DOCS, check, main, requirement_ids

SHARED = """# Shared
| ID | Requirement |
|---|---|
| FR-01 | One |
| FR-02 | Two |

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | Avail | 99.9 |
"""


MATRIX = """# Tests

## 8. Traceability matrix

| Req | Test cases | Level | Description | Status |
|---|---|---|---|---|
| FR-01 | TC-FR-01-1<br>TC-FR-01-2 | unit<br>integration | Happy path<br>NEG: bad input | auto<br>auto |
| FR-02 | TC-FR-02-1 | integration | NEG: failure path | auto |
| NFR-01 | TC-NFR-01-1 | perf | latency under load | auto |

## 9. Next
"""


def build(tmp_path):
    docs = tmp_path / "docs"
    (docs / "adr").mkdir(parents=True)
    (tmp_path / "api").mkdir()
    for name in REQUIRED_DOCS:
        (docs / name).write_text(f"# {name}\n")
    (docs / "00-shared-context.md").write_text(SHARED)
    criteria = " ".join(f"AC-FR-0{n}-{k}" for n in (1, 2) for k in (1, 2, 3))
    (docs / "01-prd.md").write_text(
        f"# PRD\n{criteria}\n| FR-01 | p | j | R1 |\n| FR-02 | p | j | R2 |\n"
    )
    (docs / "03-code-architecture.md").write_text("# Code\n" + " ".join(MODULES))
    (docs / "05-api-design.md").write_text(
        "# API\n| FR-02 | no HTTP operation, internal only |\n"
    )
    (docs / "10-test-strategy.md").write_text(MATRIX)
    (docs / "11-implementation-plan.md").write_text(
        "# Plan\nFR-01 FR-02 NFR-01 per ADR-0001\n"
    )
    (docs / "adr" / "0001-choice.md").write_text("# ADR-0001\n\n**Status**: accepted\n")
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/a": {
                "get": {"operationId": "getA", "x-fr": ["FR-01"], "x-roles": ["admin"]}
            }
        },
    }
    (tmp_path / "api" / "openapi.yaml").write_text(yaml.safe_dump(spec))
    return tmp_path


def errors(tmp_path):
    return [str(p) for p in check(tmp_path)]


def test_requirement_ids_are_read_from_table_first_cells():
    assert requirement_ids(SHARED) == (["FR-01", "FR-02"], ["NFR-01"])


def test_a_complete_package_passes(tmp_path):
    assert errors(build(tmp_path)) == []
    assert main([str(tmp_path)]) == 0


def test_a_missing_document_is_reported(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "06-ai-integration.md").unlink()

    assert any("06-ai-integration.md: document is missing" in e for e in errors(root))


def test_too_few_acceptance_criteria_and_missing_slice_are_reported(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "01-prd.md").write_text(
        "# PRD\nAC-FR-01-1 AC-FR-01-2\n| FR-01 | p | j | later |\n"
    )

    found = errors(root)

    assert any("FR-01 has 2 acceptance criteria" in e for e in found)
    assert any("FR-01 has no release slice" in e for e in found)
    assert any("FR-02 has 0 acceptance criteria" in e for e in found)


def test_an_fr_with_no_operation_needs_a_documented_reason(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "05-api-design.md").write_text("# API\n")

    assert any("FR-02 has no OpenAPI operation" in e for e in errors(root))


def test_openapi_operations_need_id_fr_and_roles(tmp_path):
    root = build(tmp_path)
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/a": {
                "get": {"summary": "x"},
                "post": {"operationId": "p", "x-fr": ["FR-09"], "x-roles": []},
            }
        },
    }
    (root / "api" / "openapi.yaml").write_text(yaml.safe_dump(spec))

    found = " ".join(errors(root))

    assert "missing operationId" in found and "missing x-fr" in found
    assert "unknown requirement FR-09" in found and "missing x-roles" in found


def test_every_requirement_needs_a_row_and_every_fr_a_negative_case(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "10-test-strategy.md").write_text(
        "# Tests\n\n## 8. Traceability matrix\n\n| Req | Cases | Level | Description | Status |\n"
        "|---|---|---|---|---|\n| FR-01 | TC-FR-01-1 | unit | happy path only | auto |\n"
    )

    found = " ".join(errors(root))

    assert "FR-01 has no negative case" in found
    assert "FR-02 has no row in the traceability matrix" in found
    assert "NFR-01 has no row in the traceability matrix" in found


def test_misaligned_matrix_columns_and_foreign_ids_are_reported(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "10-test-strategy.md").write_text(
        MATRIX.replace("unit<br>integration", "unit").replace(
            "TC-FR-02-1", "TC-FR-09-1"
        )
    )

    found = " ".join(errors(root))

    assert "FR-01 columns are misaligned: 2 ids, 1 levels" in found
    assert "FR-02 lists 'TC-FR-09-1'" in found


def test_a_missing_matrix_section_is_reported(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "10-test-strategy.md").write_text("# Tests\nTC-FR-01-1 NEG: x\n")

    assert any("no 'Traceability matrix' section" in e for e in errors(root))


def test_operational_endpoints_may_carry_x_nfr_instead_of_x_fr(tmp_path):
    root = build(tmp_path)
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/a": {
                "get": {"operationId": "getA", "x-fr": ["FR-01"], "x-roles": ["admin"]}
            },
            "/health": {
                "get": {"operationId": "h", "x-nfr": ["NFR-01"], "x-roles": ["public"]}
            },
        },
    }
    (root / "api" / "openapi.yaml").write_text(yaml.safe_dump(spec))

    assert errors(root) == []


def test_unplanned_requirements_are_reported(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "11-implementation-plan.md").write_text("# Plan\nFR-01 only\n")

    found = " ".join(errors(root))

    assert "FR-02 is not planned" in found and "NFR-01 is not planned" in found


def test_dangling_adr_unknown_requirement_and_unknown_test_references(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "02-system-architecture.md").write_text(
        "# A\nSee ADR-0042, FR-21 and TC-FR-01-9.\n"
    )

    found = " ".join(errors(root))

    assert "ADR-0042 which does not exist" in found
    assert "unknown requirement FR-21" in found
    assert "TC-FR-01-9 which is not in the test strategy" in found


def test_placeholders_and_broken_links_are_reported(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "08-frontend-ux.md").write_text(
        "# UX\nTBD later. See [x](nope.md) and [ok](00-shared-context.md).\n"
    )

    found = " ".join(errors(root))

    assert "placeholder text" in found and "broken link nope.md" in found
    assert "broken link 00-shared-context.md" not in found


def test_an_adr_without_status_and_missing_modules_are_reported(tmp_path):
    root = build(tmp_path)
    (root / "docs" / "adr" / "0002-x.md").write_text("# ADR-0002\n")
    (root / "docs" / "03-code-architecture.md").write_text("# Code\nidentity only\n")

    found = " ".join(errors(root))

    assert "adr/0002-x.md: no Status line" in found
    assert "module ledger is not described" in found


def test_a_missing_specification_is_reported_not_raised(tmp_path):
    root = build(tmp_path)
    (root / "api" / "openapi.yaml").unlink()

    assert any("specification is missing" in e for e in errors(root))
    assert main([str(root)]) == 1
