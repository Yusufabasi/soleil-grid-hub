"""Shared settings for every deployable (docs/03 section 1, docs/04 section 10.10).

Configuration comes from the environment only (12 factor). A local ``.env`` created by
``make env`` is a convenience: variables already set always win, so the file can never
override a real deployment value. Each deployable reads only its own role's connection
string, in its own module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub.shared.config import env

BACKEND_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_DIR.parent

env.load(REPO_ROOT / ".env")

# --- runtime identity -------------------------------------------------------------------
HUB_ENV = env.str("HUB_ENV", default="production")  # local | test | staging | production
HUB_VERSION = env.str("HUB_VERSION", default="dev")
# Service label of this deployable's log lines and Prometheus series (docs/09 section 5);
# every settings module overrides it with its own name.
SERVICE_NAME = "hub"

# Required: the process must not start without them (docs/03 section 7.2).
SECRET_KEY = env.str("HUB_SECRET_KEY")  # used by Django internals only, never for tokens
ALLOWED_HOSTS = [host for host in env.str("HUB_ALLOWED_HOSTS").split(",") if host]
SITE_ORIGIN = env.str("SITE_ORIGIN")
HUB_REDIS_URL = env.str("HUB_REDIS_URL")

DEBUG = HUB_ENV == "local"

TIME_ZONE = "UTC"
USE_TZ = True

# Environment knobs (Interfaces, slice 00).
HUB_TRUST_LB_REQUEST_ID = env.bool("HUB_TRUST_LB_REQUEST_ID", default=False)
HUB_DRAIN_FILE = env.str("HUB_DRAIN_FILE", default="/tmp/draining")  # noqa: S108 - drain flag, documented in .env.example
HUB_METRICS_PORT = env.int("HUB_METRICS_PORT", default=9102)
HUB_PROBLEM_BASE_URL = env.str(
    "HUB_PROBLEM_BASE_URL", default="https://hub.soleilgrid.example/problems/"
)

# --- logging (docs/03 section 7.4, docs/09 section 6) -----------------------------------
# Django's DEFAULT_LOGGING puts a plain console handler on `django` and `django.server`,
# which would print a second, non-JSON copy of every record it sees. The format itself is
# owned by `hub.shared.logging.configure_logging` (one ProcessorFormatter on the root
# handler, every stdlib logger propagating to it), so the entrypoints configure; this dict
# only empties Django's own handler lists, and is applied by django.setup() right after
# DEFAULT_LOGGING. Without configure_logging (manage.py) records fall through to the root
# handler set by the process's logging configuration.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "django": {"handlers": [], "level": "INFO", "propagate": True},
        "django.server": {"handlers": [], "level": "INFO", "propagate": True},
    },
}

# --- surface: no admin, no sessions, no contenttypes (ADR-0019, D-42, docs/03 1.2) ------
INSTALLED_APPS = [
    "hub.shared",
    "hub.hubdb",
    "hub.identity",
    "hub.customers",
    "hub.payments",
    "hub.ledger",
    "hub.meters",
    "hub.telemetry",
    "hub.support",
    "hub.ai_gateway",
    "hub.analytics",
    "hub.notifications",
    "hub.reconciliation",
    "hub.admin",
    "rest_framework",
    "django.contrib.postgres",
]

# RequestContextMiddleware binds request_id/correlation_id and records request metrics
# first (docs/03 7.3); DomainErrorMiddleware renders problem+json (docs/03 7.1).
MIDDLEWARE = [
    "hub.shared.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "hub.shared.middleware.DomainErrorMiddleware",
]

# Deploy-check noise that this architecture deliberately accepts, each with its reason:
#   security.W008  TLS is terminated at the edge (HAProxy), the app never redirects
#                   (docs/09 1.4 and 4.8).
#   security.W003  Django's CSRF middleware is not used: only two cookie endpoints exist
#                   and they are guarded by Origin + X-Hub-CSRF (D-46).
#   security.W002  the API answers JSON only; framing of page content is blocked by the
#                   edge `Content-Security-Policy: frame-ancestors 'none'` for /api/*
#                   (docs/09 1.4) and by the nonce CSP of hub-web (D-44).
SILENCED_SYSTEM_CHECKS = [
    "security.W008",
    "security.W003",
    "security.W002",
]

# --- transport security (docs/09 4.8) ---------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 15552000  # matches the edge header (docs/09 1.4)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_SSL_REDIRECT = False  # the edge redirects (see W008 above)
X_FRAME_OPTIONS = "DENY"

DEFAULT_CHARSET = "utf-8"

# --- REST framework (docs/03 7.7) -------------------------------------------------------
# The exception handler, permission class and pagination arrive with slices 01 and later;
# from the start: JSON only, no browsable API, deny by default, no anonymous user object.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "hub.shared.api.problem.exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_THROTTLE_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}


def build_database(url_var: str, *, app_name: str) -> dict[str, Any]:
    """Build one DATABASES entry from this deployable's own connection URL (docs/04 10.10).

    ``ATOMIC_REQUESTS`` is off on every alias (D-42): transactions are explicit, because
    the webhook path needs one precisely bounded transaction. PgBouncer runs in
    transaction mode, so ``CONN_MAX_AGE`` is 0 and server-side cursors stay disabled.
    """
    config = env.db_url(url_var)
    config.update(
        {
            "ENGINE": "django.db.backends.postgresql",
            "ATOMIC_REQUESTS": False,
            "AUTOCOMMIT": True,
            "DISABLE_SERVER_SIDE_CURSORS": True,
            "TIME_ZONE": "UTC",
            "USE_TZ": True,
            "OPTIONS": {"connect_timeout": 5, "application_name": app_name},
        }
    )
    return config


# Prepared statements are disabled per connection: psycopg 3 would otherwise prepare
# after five executions, which needs PgBouncer support (docs/04 10.10, A-13).
from django.db.backends.signals import connection_created  # noqa: E402


def _disable_prepared_statements(sender: object, connection: object, **kwargs: object) -> None:
    raw = getattr(connection, "connection", None)
    if raw is not None:
        raw.prepare_threshold = None


connection_created.connect(_disable_prepared_statements)
