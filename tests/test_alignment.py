"""Cross-artefact alignment: the API and the schema must not drift apart again.

Decisions D-01 and D-14 in docs/00-shared-context.md make db/schema.sql the authority for every
enumeration, and api/permissions.yaml the single source of the roles on every operation.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db" / "schema.sql").read_text()
SPEC = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text())
SCHEMAS = SPEC["components"]["schemas"]

# (schema table, column) -> (OpenAPI component schema, property). Edit this table when a property
# is renamed; the enumerations themselves must never be edited to make the test pass.
ENUM_PAIRS = {
    ("top_ups", "status"): ("TopUp", "status"),
    ("meter_tokens", "status"): ("Token", "status"),
    ("alerts", "kind"): ("Alert", "kind"),
    ("reconciliation_items", "kind"): ("ReconException", "kind"),
    ("reconciliation_items", "status"): ("ReconException", "status"),
    ("customers", "status"): ("Customer", "status"),
    ("meters", "status"): ("Meter", "status"),
    ("users", "status"): ("User", "status"),
    ("tickets", "status"): ("Ticket", "status"),
    ("data_erasure_requests", "status"): ("ErasureRequest", "status"),
    ("ai_runs", "status"): ("AiRun", "status"),
    ("ai_runs", "feature"): ("AiRun", "feature"),
}

# Roles may only be these; machine principals are security schemes, not database roles (D-13).
ALLOWED_ROLES = {
    "support_agent",
    "finance_officer",
    "operations_engineer",
    "administrator",
    "customer",
    "integrator",
    "gateway",
    "provider",
    "public",
}
# D-14: the administrator holds no money capability and reads no ticket or customer content.
ADMIN_FORBIDDEN_FRS = {
    "FR-02",
    "FR-04",
    "FR-08",
    "FR-09",
    "FR-10",
    "FR-11",
    "FR-12",
    "FR-14",
}


def schema_enums() -> dict[tuple[str, str], list[str]]:
    found = {}
    for table, body in re.findall(
        r"CREATE TABLE (?:hub\.)?(\w+)\s*\((.*?)\n\);", SCHEMA, re.DOTALL
    ):
        for column, values in re.findall(
            r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)\s*\)", body
        ):
            found[(table, column)] = sorted(re.findall(r"'([^']+)'", values))
    return found


def spec_enum(component: str, prop: str):
    node = SCHEMAS.get(component, {}).get("properties", {}).get(prop)
    if node is None:
        return None
    if "$ref" in node:
        node = SCHEMAS[node["$ref"].rsplit("/", 1)[1]]
    values = node.get("enum")
    return sorted(values) if values else None


@pytest.mark.parametrize(("table_column", "target"), sorted(ENUM_PAIRS.items()))
def test_every_api_enum_equals_the_schema_check_constraint(table_column, target):
    expected = schema_enums().get(table_column)
    actual = spec_enum(*target)

    assert expected is not None, f"schema has no CHECK ... IN list for {table_column}"
    assert actual is not None, f"openapi {target[0]}.{target[1]} has no enum"
    assert actual == expected, f"{target[0]}.{target[1]} differs from {table_column}"


def operations():
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            if method in {"get", "put", "post", "patch", "delete"}:
                yield path, method, op


def test_permissions_file_is_the_single_source_of_x_roles():
    perms = yaml.safe_load((ROOT / "api" / "permissions.yaml").read_text())[
        "operations"
    ]
    spec_roles = {op["operationId"]: set(op["x-roles"]) for _, _, op in operations()}

    assert set(perms) == set(spec_roles), (
        "permissions.yaml and openapi list different operations"
    )
    mismatched = {
        k: (sorted(perms[k]), sorted(spec_roles[k]))
        for k in perms
        if set(perms[k]) != spec_roles[k]
    }
    assert mismatched == {}


def test_only_known_roles_appear():
    used = {r for _, _, op in operations() for r in op["x-roles"]}

    assert used <= ALLOWED_ROLES, sorted(used - ALLOWED_ROLES)


def test_the_administrator_has_no_money_or_content_operations():
    offenders = [
        f"{op['operationId']} ({sorted(set(op['x-fr']) & ADMIN_FORBIDDEN_FRS)})"
        for _, _, op in operations()
        if "administrator" in op["x-roles"]
        and set(op.get("x-fr", [])) & ADMIN_FORBIDDEN_FRS
    ]

    assert offenders == []
