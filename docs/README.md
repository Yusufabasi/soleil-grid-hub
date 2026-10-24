# Soleil Grid Hub: design package

A prepaid solar mini-grid platform (Django 5 + DRF, Next.js, PostgreSQL 16, Redis, HAProxy, Claude
through one gateway). This folder is a plan, not code. A cheaper agent builds from `plan/`; the tech
lead verifies with `plan/99-verification.md`.

| Read | What |
|---|---|
| [00-shared-context.md](00-shared-context.md) | The contract: invariants and decisions D-01 to D-50 |
| [01-prd.md](01-prd.md) | Requirements and acceptance criteria |
| [02-system-architecture.md](02-system-architecture.md) | Deployment, load balancing, caching, data flow |
| [03-code-architecture.md](03-code-architecture.md) | Modules, layout, ports, build order |
| [04-data-model.md](04-data-model.md) | Schema, roles, Django mapping |
| [05-api-design.md](05-api-design.md) | API conventions |
| [06-ai-integration.md](06-ai-integration.md) | AI gateway, features, budgets, evaluation |
| [07-security-threat-model.md](07-security-threat-model.md) | Threats and controls |
| [08-frontend-ux.md](08-frontend-ux.md) | Screens, accessibility, i18n |
| [09-devops-sre.md](09-devops-sre.md) | Infrastructure, CI, SLOs, runbooks |
| [10-test-strategy.md](10-test-strategy.md) | Test strategy and the TC- matrix |
| [11-implementation-plan.md](11-implementation-plan.md) | Slice index for the executor |
| [12-review-notes.md](12-review-notes.md) | Review history and known limits |
| [adr/](adr/README.md) | Architecture decision records |
| [plan/](plan/00-executor-guide.md) | Executor guide, slice files, frontend plans, verification |

Frozen machine-checked contracts live beside this folder: `../api/openapi.yaml`,
`../api/permissions.yaml`, `../db/schema.sql`, `../db/invariant_tests.sql`. Check them with
`.venv/bin/pytest tests` and `.venv/bin/python tools/check_traceability.py` from the project root.
