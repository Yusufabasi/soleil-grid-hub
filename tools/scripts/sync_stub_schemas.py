#!/usr/bin/env python3
"""Keep the double's copy of the webhook envelope equal to the API's schema.

The double signs and sends `WebhookEnvelope` bodies, and the hub validates what it receives
against the same schema from `api/openapi.yaml`. If the copy inside
`tools/provider_stub/schemas/` drifted, the double would keep producing documents that the
frozen contract no longer describes — and every hub signature test would still pass, because the
signature is over bytes and does not care what they say.

So the copy is *generated*: this script extracts the component, inlines the `$ref`s it uses (the
spec's `#/components/schemas/Money` is not resolvable from a standalone file), and writes it. Run
it with no arguments to update the copy; `--check` (what the tests and `make verify` run) makes no
change and exits non-zero when the committed copy differs, which is the only way a drift is ever
noticed.

`api/openapi.yaml` is a frozen artefact: this script reads it and never writes it.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI = REPO_ROOT / "api" / "openapi.yaml"
TARGET = REPO_ROOT / "tools" / "provider_stub" / "schemas" / "webhook_envelope.schema.json"

COMPONENT = "WebhookEnvelope"
# The heading every generated file carries, so a reader knows not to edit it by hand.
GENERATED_NOTE = (
    "Generated from api/openapi.yaml by tools/scripts/sync_stub_schemas.py — do not edit by "
    "hand; run the script instead."
)


class SyncError(RuntimeError):
    """The spec does not hold what this script expects."""


def _components() -> dict[str, Any]:
    try:
        spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - a broken checkout
        raise SyncError(f"cannot read {OPENAPI}: {exc}") from exc
    schemas = (spec or {}).get("components", {}).get("schemas", {})
    if COMPONENT not in schemas:
        raise SyncError(f"{COMPONENT} is not in api/openapi.yaml#/components/schemas")
    return schemas


def _inline(node: Any, schemas: dict[str, Any], *, seen: frozenset[str] = frozenset()) -> Any:
    """Replace every local ``$ref`` with the schema it names, refusing a cycle.

    The spec's own components reference each other by name; a standalone file cannot. A cycle
    would mean two schemas that cannot both be inlined, which is a spec problem worth stopping on
    rather than writing a half-resolved file.
    """
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str):
            prefix = "#/components/schemas/"
            if not reference.startswith(prefix):
                raise SyncError(f"cannot inline a non-component reference: {reference}")
            name = reference[len(prefix) :]
            if name in seen:
                raise SyncError(f"cyclic schema reference: {' -> '.join([*seen, name])}")
            if name not in schemas:
                raise SyncError(f"{COMPONENT} references an unknown schema: {name}")
            resolved = _inline(copy.deepcopy(schemas[name]), schemas, seen=seen | {name})
            # A `$ref` may sit beside a description; keep it, because that is the sentence the
            # spec chose for this use of the shared schema.
            extra = {key: value for key, value in node.items() if key != "$ref"}
            if extra and isinstance(resolved, dict):
                return {**resolved, **extra}
            return resolved
        return {key: _inline(value, schemas, seen=seen) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline(item, schemas, seen=seen) for item in node]
    return node


def extract() -> dict[str, Any]:
    """The schema as it should be committed."""
    schemas = _components()
    body = _inline(copy.deepcopy(schemas[COMPONENT]), schemas)
    if not isinstance(body, dict):  # pragma: no cover - the component is an object
        raise SyncError(f"{COMPONENT} is not an object schema")
    document: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://soleilgrid.example/schemas/provider_stub/{TARGET.name}",
        "title": COMPONENT,
        "description": (
            "What the double signs and posts to the hub, copied from the frozen API contract. "
            + GENERATED_NOTE
        ),
        "x-source": f"api/openapi.yaml#/components/schemas/{COMPONENT}",
    }
    document.update(body)
    return document


def render(document: dict[str, Any]) -> str:
    """The file's exact bytes as text: two-space indent, spec order, trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed copy matches the spec and change nothing",
    )
    args = parser.parse_args(argv)

    try:
        expected = render(extract())
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if not TARGET.exists():  # pragma: no cover - the file is committed
            print(f"error: {TARGET.relative_to(REPO_ROOT)} is missing", file=sys.stderr)
            return 1
        actual = TARGET.read_text(encoding="utf-8")
        if actual != expected:
            print(
                f"{TARGET.relative_to(REPO_ROOT)} differs from api/openapi.yaml; "
                "run python tools/scripts/sync_stub_schemas.py",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.relative_to(REPO_ROOT)} matches api/openapi.yaml")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(expected, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - the process entry point
    raise SystemExit(main())
