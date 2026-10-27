"""The double's command line: `serve`, `probe` and `statement`.

Three commands, because the double has three jobs:

* `serve` — the long-running HTTP double the compose stack brings up.
* `probe` — the synthetic liveness probe, run as its own process so its interval is not tied to
  the HTTP server's lifetime (D-39).
* `statement` — render a statement of a given size off-line. The verification uses it to prove the
  hub's 10 MiB upload cap is reachable with a file the double itself produced; nothing else in the
  double needs a statement that big.

Logging is one JSON object per line, which is what the compose stack's collector expects (docs/09).
Secrets never appear in a line: the commands log the *number* of configured providers, never a
value.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from provider_stub import __version__
from provider_stub.config import ConfigError, PROVIDERS, load
from provider_stub.deliver import Deliverer
from provider_stub.probe import run as run_probe
from provider_stub.server import make_server
from provider_stub.state import Store
from provider_stub.statements import (
    KINDS,
    render,
    render_expected_json,
    rows_for_collections,
    rows_for_day,
    rows_for_month,
    synthetic_rows,
)


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, with any structured `extra` merged in."""

    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("probe_result", "line"):
            if key in record.__dict__:
                document[key] = record.__dict__[key]
        return json.dumps(document, separators=(",", ":"), sort_keys=True)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def _parse_injections(values: list[str]) -> list[dict[str, Any]]:
    """``--inject kind:count`` into the control API's document, refusing what it refuses."""
    injections: list[dict[str, Any]] = []
    for value in values:
        kind, _, raw_count = value.partition(":")
        if kind not in KINDS:
            raise SystemExit(f"not a statement injection kind: {kind!r} (one of {', '.join(KINDS)})")
        injections.append({"kind": kind, "count": int(raw_count or 1)})
    return injections


def _serve(args: argparse.Namespace) -> int:
    config = load()
    store = Store(config.journal_path, clock=time.time)
    deliverer = Deliverer(config=config, store=store)
    server = make_server(
        config=config, store=store, deliverer=deliverer, clock=time.time, port=args.port
    )
    deliverer.start()
    host, port = server.server_address[:2]
    logging.getLogger("provider_stub").info(
        "provider_stub.serving",
        extra={
            "line": (
                f"listening on {host}:{port} with {len(config.secrets)} provider secrets, "
                f"journal {config.journal_path}, hub {config.hub_base_url}"
            )
        },
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - the ordinary way the service stops
        logging.getLogger("provider_stub").info("provider_stub.stopping")
    finally:
        deliverer.stop()
        server.server_close()
    return 0


def _probe(args: argparse.Namespace) -> int:
    config = load()
    top_up_id = os.environ.get("STUB_PROBE_TOP_UP_ID", "").strip()
    if not top_up_id:
        raise SystemExit(
            "STUB_PROBE_TOP_UP_ID is required: the probe posts a real delivery for a real top-up"
        )
    results = run_probe(
        hub_url=args.hub_url or config.hub_base_url,
        region=args.region,
        secret=config.secret_for("synthetic").encode("utf-8"),
        top_up_id=top_up_id,
        interval=args.interval,
        iterations=args.iterations,
    )
    # A non-zero exit is reserved for "the probe could not run at all"; an unhealthy hub is data
    # the alerting rule reads from the log lines, not a reason to crash-loop the probe.
    return 0 if results else 1


def _statement(args: argparse.Namespace) -> int:
    """Render a statement: generated rows off-line, or the collections this journal holds."""
    injections = _parse_injections(args.inject)
    if args.rows:
        rows = synthetic_rows(
            args.rows,
            provider=args.provider,
            day=date.fromisoformat(args.day) if args.day else None,
        )
    else:
        config = load()
        store = Store(config.journal_path, clock=time.time)
        rows = rows_for_collections(store.collections(args.provider))
    if args.month:
        rows = rows_for_month(rows, args.month)
    elif args.day:
        rows = rows_for_day(rows, date.fromisoformat(args.day))
    payload = render(rows, injections)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    if args.expected:
        Path(args.expected).write_bytes(render_expected_json(rows, injections))
    logging.getLogger("provider_stub").info(
        "provider_stub.statement",
        extra={
            "line": (
                f"wrote {len(payload)} bytes for {args.provider} "
                f"({len(rows)} rows, {len(injections)} injection(s)) to {destination}"
            )
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="provider_stub", description="the mobile-money double")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the provider and control APIs")
    serve.add_argument("--port", type=int, default=None, help="default: STUB_PORT (9100)")
    serve.set_defaults(handler=_serve)

    probe = sub.add_parser("probe", help="send one signed synthetic delivery per interval")
    probe.add_argument("--interval", type=float, default=30.0)
    probe.add_argument("--hub-url", default=None, help="default: STUB_HUB_BASE_URL")
    probe.add_argument("--region", default="local")
    probe.add_argument("--iterations", type=int, default=None, help="stop after N (for a demo)")
    probe.set_defaults(handler=_probe)

    statement = sub.add_parser("statement", help="render a statement CSV")
    statement.add_argument("--provider", default=PROVIDERS[0])
    statement.add_argument("--month", default=None, help="YYYY-MM (the rows come from the journal)")
    statement.add_argument("--day", default=None, help="YYYY-MM-DD for generated rows")
    statement.add_argument("--rows", type=int, default=0, help="generate N synthetic rows")
    statement.add_argument("--out", required=True)
    statement.add_argument("--expected", default=None, help="also write expected_items.json")
    statement.add_argument("--inject", action="append", default=[], help="kind[:count]")
    statement.set_defaults(handler=_statement)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ConfigError as exc:
        # The message names the variable and never its value.
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - the process entry point
    raise SystemExit(main())
