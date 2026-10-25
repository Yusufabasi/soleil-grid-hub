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

.PHONY: help env venv up down reset test lint contracts gen verify

help:
	@echo "make env      copy .env.example to .env when absent"
	@echo "make venv     create backend/.venv from backend/uv.lock"
	@echo "make up       start the local stack (compose, migrate, logins)"
	@echo "              WEB=real builds frontend/ instead of the hub-web stub"
	@echo "              SCALE_WEBHOOKS=2 runs two webhook replicas"
	@echo "make down     stop the local stack"
	@echo "make reset    stop the local stack and delete its volumes"
	@echo "make test     backend unit and architecture tests (+ frontend unit tests)"
	@echo "make lint     ruff, ruff format, mypy, import-linter, Django config checks"
	@echo "make contracts  alignment, schema, traceability and OpenAPI checks"
	@echo "make gen      regenerate the generated artefacts when present"
	@echo "make verify SLICE=NN   run the verification commands of one slice"

env:
	@test -f .env || cp .env.example .env

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
	$(COMPOSE) $(COMPOSE_WEB) up -d --build --wait \
		$(if $(SCALE_WEBHOOKS),--scale hub-ingest-webhooks=$(SCALE_WEBHOOKS),)

down:
	$(COMPOSE) down

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
