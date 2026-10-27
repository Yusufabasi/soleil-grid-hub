"""Top up `.env` with the variables `.env.example` defines and it does not.

`make env` copies the example on a first run and the file belongs to the developer from then on,
so a checkout that predates a slice is missing whatever that slice added. The symptom is a compose
interpolation error naming a variable nobody has seen before, which reads like a bug in the
compose file rather than a stale `.env` (slice T1's `STUB_SECRET_*` are the first such variables:
the stack cannot start without them, and the guard is deliberate — a webhook secret without a value
must fail loudly rather than default).

So: append the absent keys, keep the values the example gives (development values only), and never
touch, reorder or rewrite a line that is already there — a local override is the whole point of the
file. Running it twice adds nothing the second time.

The output names the variables and never their values; the values in `.env.example` are not
secrets, but this script must stay safe to run on a `.env` that has been replaced by real ones.

    python tools/scripts/sync_env.py            # top up, reporting what it added
    python tools/scripts/sync_env.py --check    # report what is missing, add nothing (CI, review)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# A variable definition in a dotenv file: the name is what matters, and only an uncommented line
# at the start of a line counts. Nothing here parses quoting or `export` — the example is written
# as plain `KEY=value`, and this script deliberately cannot invent a variable the example lacks.
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

HEADING = "# --- added by `make env`: variables .env.example defines and this file did not ---"


def defined_names(text: str) -> set[str]:
    """The variable names a dotenv text defines."""
    names: set[str] = set()
    for line in text.splitlines():
        match = ASSIGNMENT.match(line.strip())
        if match is not None:
            names.add(match.group(1))
    return names


def missing_lines(example: str, current: str) -> list[str]:
    """The whole lines of ``example`` whose variables ``current`` does not define, in order."""
    present = defined_names(current)
    added: list[str] = []
    for line in example.splitlines():
        match = ASSIGNMENT.match(line.strip())
        if match is not None and match.group(1) not in present:
            added.append(line.strip())
    return added


def sync(example_path: Path, env_path: Path, *, check: bool) -> list[str]:
    """Append the missing variables to ``env_path``; returns the names it added (or would)."""
    if not example_path.exists():  # pragma: no cover - the example is in the repository
        raise FileNotFoundError(example_path)
    example = example_path.read_text(encoding="utf-8")
    current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = missing_lines(example, current)
    if lines and not check:
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{HEADING}\n")
            for line in lines:
                handle.write(f"{line}\n")
    return [line.split("=", 1)[0] for line in lines]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sync_env", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report the missing variables and exit non-zero without changing the file",
    )
    parser.add_argument("--example", default=str(REPO_ROOT / ".env.example"))
    parser.add_argument("--env", default=str(REPO_ROOT / ".env"))
    args = parser.parse_args(argv)

    env_path = Path(args.env)
    if not env_path.exists():
        # `make env` copies the example first; nothing to top up, and nothing to report.
        print(f"sync_env: {env_path.name} does not exist yet (run `make env`)")
        return 0

    added = sync(Path(args.example), env_path, check=args.check)
    if not added:
        print(f"sync_env: {env_path.name} defines every variable in .env.example")
        return 0
    verb = "missing from" if args.check else "added to"
    print(f"sync_env: {len(added)} variable(s) {verb} {env_path.name}: {', '.join(added)}")
    return 1 if args.check else 0


if __name__ == "__main__":  # pragma: no cover - exercised through `make env`
    sys.exit(main())
