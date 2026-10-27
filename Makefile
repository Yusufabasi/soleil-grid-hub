# Soleil Grid Hub: one-command entry points (NFR-14).
#
# Every target runs from the repository root. The backend environment is created on
# demand from backend/uv.lock by uv (bootstrapped into .tools/venv when uv is missing).
# `make verify SLICE=NN` runs one slice's verification commands, the same list the
# verifier runs (docs/plan/slice-NN-*.md, "Verification").

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

CURDIR := $(shell pwd)
VENV := $(CURDIR)/backend/.venv
TOOLS := $(CURDIR)/.tools/venv
export PATH := $(VENV)/bin:$(TOOLS)/bin:$(PATH)

# The stack's variables come from .env; compose looks for that file next to the compose file,
# not in the repository root, so it is passed explicitly (and only once it exists).
ENV_FILE := $(if $(wildcard .env),--env-file .env,)
COMPOSE := docker compose $(ENV_FILE) -f infra/docker/compose.yaml
# `make up WEB=real` mounts the override that builds frontend/ instead of the nginx stub.
COMPOSE_WEB := $(if $(filter real,$(WEB)),-f infra/docker/compose.web.yaml,)

.PHONY: help env venv up down reset test test-tools-t1 lint contracts gen verify

help:
	@echo "make env      copy .env.example to .env when absent, then add variables it lacks"
	@echo "make venv     create backend/.venv from backend/uv.lock"
	@echo "make up       start the local stack (compose, migrate, logins, the provider double)"
	@echo "make test-tools-t1   the provider double's own tests (slice T1)"
	@echo "              WEB=real builds frontend/ instead of the hub-web stub"
	@echo "              SCALE_WEBHOOKS=2 runs two webhook replicas"
	@echo "make down     stop the local stack"
	@echo "make reset    stop the local stack and delete its volumes"
	@echo "make test     backend unit and architecture tests (+ frontend unit tests)"
	@echo "make lint     ruff, ruff format, mypy, import-linter, Django config checks"
	@echo "make contracts  alignment, schema, traceability and OpenAPI checks"
	@echo "make gen      regenerate the generated artefacts when present"
	@echo "make verify SLICE=NN   run the verification commands of one slice"

# The example is copied on the first run; after that the file belongs to the developer. This
# target therefore also *tops it up*: a checkout that predates a slice is missing that slice's
# variables, and compose reports it as an interpolation error naming a variable nobody has seen.
# Values already in the file are never rewritten (tools/scripts/sync_env.py, docs/plan/progress.md).
env:
	@test -f .env || cp .env.example .env
	@python3 tools/scripts/sync_env.py

$(TOOLS)/bin/uv:
	@python3 -m venv $(TOOLS)
	@$(TOOLS)/bin/pip install --quiet --upgrade uv
	@echo "bootstrapped uv into $(TOOLS)"

venv: backend/uv.lock | $(TOOLS)/bin/uv
	@$(TOOLS)/bin/uv sync --project backend --quiet
	@$(VENV)/bin/python --version

backend/uv.lock: backend/pyproject.toml | $(TOOLS)/bin/uv
	@$(TOOLS)/bin/uv lock --project backend --quiet

up: env
	@if [ "$(WEB)" = "real" ] && [ ! -d frontend ]; then \
		echo "WEB=real needs frontend/ (fe-00); the nginx stub is the default" >&2; exit 1; \
	fi
	@bash tools/scripts/gen_dev_userlist.sh
	# The `stubs` profile is on by default here: the payments routes are useless without the
	# provider double to collect from, and the end-to-end tests in slice 04 drive it. Its
	# sibling `meter-sim` is still a placeholder and costs one idle busybox container.
	$(COMPOSE) $(COMPOSE_WEB) --profile stubs up -d --build --wait \
		$(if $(SCALE_WEBHOOKS),--scale hub-ingest-webhooks=$(SCALE_WEBHOOKS),)

down:
	$(COMPOSE) down

# The provider double (slice T1) has its own environment: it is a standalone package with one
# dependency, so it is locked and synced on its own rather than sharing the backend's venv. The
# double never imports the hub, and this keeps that true at the level of what is installed.
STUB_VENV := $(CURDIR)/tools/provider_stub/.venv

tools/provider_stub/uv.lock: tools/provider_stub/pyproject.toml | $(TOOLS)/bin/uv
	@$(TOOLS)/bin/uv lock --project tools/provider_stub --quiet

$(STUB_VENV)/bin/python: tools/provider_stub/uv.lock | $(TOOLS)/bin/uv
	@$(TOOLS)/bin/uv sync --project tools/provider_stub --quiet

stub-venv: $(STUB_VENV)/bin/python

# `make test-tools-t1` runs the double's suites (signing, server, delivery faults, statements,
# probe, contract), which is what `make verify SLICE=T1` checks first.
test-tools-t1: stub-venv
	cd tools/provider_stub && .venv/bin/python -m pytest -q

# Published test identities (db/seed/test_users.json): one account per staff role, a finance
# lead, a second administrator and one customer. Idempotent, and the command itself refuses to
# run anywhere but a local, test or ci database.
seed: env
	$(COMPOSE) exec -T hub-api python manage.py load_fixtures

# The slice 01 demo (docs/plan/slice-01-identity-rbac-approvals.md): sign-in, refresh reuse,
# maker-checker, audit and the customer code, against a stack that `make seed` has filled.
# It creates one account, so it wants a fresh volume.
demo: venv
	backend/.venv/bin/python tools/demo_slice01.py

reset:
	$(COMPOSE) down -v --remove-orphans

test: env venv
	cd backend && python -m pytest tests/unit tests/architecture -q
	@if [ -f frontend/package.json ]; then cd frontend && npm test --silent; fi

lint: env venv
	cd backend && ruff check .
	cd backend && ruff format --check .
	cd backend && DJANGO_SETTINGS_MODULE=hub.settings.test mypy
	cd backend && lint-imports --config .importlinter
	python tools/check_django_config.py
	@if command -v actionlint >/dev/null 2>&1; then actionlint; fi
	@if [ -f frontend/package.json ]; then cd frontend && npm run lint --silent; fi

contracts: env venv
	python -m pytest tests/test_alignment.py tests/test_schema_in_postgres.py tests/test_traceability.py -q
	python tools/check_traceability.py
	python -c "import sys, yaml; from openapi_spec_validator import validate as v; v(yaml.safe_load(open('api/openapi.yaml'))); print('openapi.yaml is valid')"

gen:
	@if [ -f frontend/package.json ]; then cd frontend && npm run gen:api && npm run gen:permissions; else echo "no frontend yet: nothing to generate"; fi

verify:
	@bash tools/scripts/verify_slice.sh $(SLICE)
