"""Shared settings for every deployable (docs/03 section 1, docs/04 section 10.10).

Configuration comes from the environment only (12 factor). A local ``.env`` created by
``make env`` is a convenience: variables already set always win, so the file can never
override a real deployment value. Each deployable reads only its own role's connection
string, in its own module.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.exceptions import ImproperlyConfigured

from hub.shared.config import env

BACKEND_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_DIR.parent
# Where db/ (schema.sql, baseline, migrations) lives. Empty means "derive it from this
# file's location", which is right for a checkout; the image sets it to /app.
HUB_REPO_ROOT = env.str("HUB_REPO_ROOT", default="") or str(REPO_ROOT)

# Registry defaults (slice 02): the country prefix a national phone number is completed
# with, and the key the pseudonymous view derives from a customer id.
HUB_DEFAULT_PHONE_PREFIX = env.str("HUB_DEFAULT_PHONE_PREFIX", default="+243")
HUB_PSEUDONYM_KEY = env.str("HUB_PSEUDONYM_KEY", default="dev-pseudonym-key-not-a-secret")
HUB_IMPORT_MAX_ROWS = env.int("HUB_IMPORT_MAX_ROWS", default=5000)
HUB_IMPORT_MAX_BYTES = env.int("HUB_IMPORT_MAX_BYTES", default=2_097_152)

# The contract files the permission class reads at start (docs/03 7.7 item 4): the allowed
# roles per operation, and the operation list with its x-step-up / x-approval extensions.
HUB_CONTRACT_DIR = str(Path(HUB_REPO_ROOT) / "api")

# The custom user model is a shim over hub.users and has no Django auth machinery behind it
# (docs/03 2.2); it is named here so a get_user_model() call resolves to the real table.
AUTH_USER_MODEL = "identity.User"

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


# --- tokens, sessions and MFA (docs/03 2.2, D-16, D-17) -----------------------------------
# The Ed25519 key pair is the only signing authority of the API; a missing pair in a real
# deployment is a start-up failure, never a silent fallback. Local and test runs derive a
# *fixed, public* pair from a constant seed so `make up` needs no secret in the repository
# and every gunicorn worker of the local stack signs with the same key: a per-process
# ephemeral pair made a token minted by one worker unverifiable by the next, which the
# gunicorn stack showed as a random 401 but a single-process test run never could.
LOCAL_JWT_KEY_SEED = b"soleil-grid-hub/local-only/not-a-secret"


def _jwt_keys() -> tuple[str, str]:
    configured_private = env.str("HUB_JWT_PRIVATE_KEY", default="")
    configured_public = env.str("HUB_JWT_PUBLIC_KEY", default="")
    if configured_private and configured_public:
        # A PEM in a single-line variable escapes its newlines; accept both spellings.
        return (
            configured_private.replace("\\n", "\n"),
            configured_public.replace("\\n", "\n"),
        )
    if HUB_ENV not in ("local", "test"):
        raise ImproperlyConfigured(
            "Set HUB_JWT_PRIVATE_KEY and HUB_JWT_PUBLIC_KEY (Ed25519, PEM) outside local/test"
        )
    # Deterministic and therefore public: local/test only, guarded above.
    key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(LOCAL_JWT_KEY_SEED).digest())
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


HUB_JWT_PRIVATE_KEY, HUB_JWT_PUBLIC_KEY = _jwt_keys()
HUB_JWT_ISSUER = env.str("HUB_JWT_ISSUER", default="hub")
HUB_JWT_AUDIENCE = env.str("HUB_JWT_AUDIENCE", default="hub-api")
HUB_ACCESS_TTL_SECONDS = env.int("HUB_ACCESS_TTL_SECONDS", default=600)
HUB_CLOCK_LEEWAY_SECONDS = env.int("HUB_CLOCK_LEEWAY_SECONDS", default=30)

# Idle and absolute session limits per account kind (D-17).
HUB_STAFF_IDLE_SECONDS = env.int("HUB_STAFF_IDLE_SECONDS", default=1800)
HUB_STAFF_ABSOLUTE_SECONDS = env.int("HUB_STAFF_ABSOLUTE_SECONDS", default=28800)
HUB_CUSTOMER_IDLE_SECONDS = env.int("HUB_CUSTOMER_IDLE_SECONDS", default=300)
HUB_CUSTOMER_ABSOLUTE_SECONDS = env.int("HUB_CUSTOMER_ABSOLUTE_SECONDS", default=43200)
HUB_STEP_UP_MAX_AGE_SECONDS = env.int("HUB_STEP_UP_MAX_AGE_SECONDS", default=300)
HUB_APPROVAL_TTL_HOURS = env.int("HUB_APPROVAL_TTL_HOURS", default=72)

# The balance below which `wallet.low_balance` is emitted, in minor units of the wallet's
# currency (slice 03 open item: the threshold by currency is finance's to tune).
HUB_LEDGER_LOW_BALANCE_MINOR = env.int("HUB_LEDGER_LOW_BALANCE_MINOR", default=100)

# --- payments: top-ups and the provider webhook (slice 04, docs/03 6.1) -------------------
# How long a customer waits for the provider before the top-up expires, and the two ceilings
# that stop one meter or one payer from minting collections: 10 per meter per 24 h is the
# contract's number (docs/05 tier table), 5 per payer per hour is what one phone may spend.
HUB_TOPUP_EXPIRY_MINUTES = env.int("HUB_TOPUP_EXPIRY_MINUTES", default=15)
HUB_TOPUP_MAX_PER_METER_24H = env.int("HUB_TOPUP_MAX_PER_METER_24H", default=10)
HUB_TOPUP_MAX_PER_PAYER_1H = env.int("HUB_TOPUP_MAX_PER_PAYER_1H", default=5)

# `{provider: base_url}` for the HTTP adapter. A provider with no base URL uses the in-memory
# stub, which is what local and test runs want: nothing leaves the process. The provider a
# top-up names is part of its own request (`TopUpCreate.provider`), never a process-wide setting,
# because one deployable collects for every channel the site is configured for.
HUB_PROVIDER_BASE_URLS: dict[str, str] = env.json("HUB_PROVIDER_BASE_URLS", default={})

# An amount at or above this floor is confirmed by pulling the provider's status API, not by
# trusting the envelope alone (slice 04 open item: 100 USD, at an assumed 2,800 CDF per USD).
HUB_PULL_VERIFY_THRESHOLD_MINOR = {
    "USD": env.int("HUB_PULL_VERIFY_THRESHOLD_USD_MINOR", default=10_000),
    "CDF": env.int("HUB_PULL_VERIFY_THRESHOLD_CDF_MINOR", default=28_000_000),
}
HUB_PULL_TIMEOUT_S = env.float("HUB_PULL_TIMEOUT_S", default=1.5)
HUB_PROVIDER_INITIATE_TIMEOUT_S = env.float("HUB_PROVIDER_INITIATE_TIMEOUT_S", default=10.0)

# The webhook body cap is checked from Content-Length before a byte is read (docs/03 6.1).
HUB_WEBHOOK_MAX_BODY_BYTES = env.int("HUB_WEBHOOK_MAX_BODY_BYTES", default=65_536)
HUB_WEBHOOK_TOLERANCE_SECONDS = env.int("HUB_WEBHOOK_TOLERANCE_SECONDS", default=300)

# `{provider: [current, previous]}`: the previous secret is accepted during the 24 h rotation
# overlap. A JSON object in one variable, because a provider's secret list is a data structure
# and four environment variables per provider would invite a typo nobody notices until a
# rotation silently rejects every delivery.
HUB_PROVIDER_WEBHOOK_SECRETS: dict[str, list[str]] = env.json(
    "HUB_PROVIDER_WEBHOOK_SECRETS",
    default={"synthetic": ["dev-synthetic-webhook-secret"]},
)

# How long an initiation whose provider outcome is unknown waits before the poller looks again.
HUB_PENDING_UNKNOWN_POLL_MINUTES = env.int("HUB_PENDING_UNKNOWN_POLL_MINUTES", default=30)

# The refresh cookie is scoped to the auth routes only (D-17).
HUB_REFRESH_COOKIE_NAME = "sg_refresh"
HUB_REFRESH_COOKIE_PATH = "/api/v1/auth"
HUB_OIDC_STATE_COOKIE_NAME = "sg_oidc_state"
HUB_OIDC_ROUTES_ENABLED = env.bool("HUB_OIDC_ROUTES_ENABLED", default=False)

# WebAuthn go-live (slice 01 open item 2): off in base, on in production API settings.
HUB_WEBAUTHN_ENFORCED = env.bool("HUB_WEBAUTHN_ENFORCED", default=HUB_ENV == "production")
HUB_WEBAUTHN_RP_NAME = env.str("HUB_WEBAUTHN_RP_NAME", default="Soleil Grid Hub")

# Customer one-time codes (D-21).
HUB_POW_DIFFICULTY_BITS = env.int("HUB_POW_DIFFICULTY_BITS", default=18)
HUB_OTP_ALLOWED_PREFIXES = tuple(
    prefix.strip()
    for prefix in env.str("HUB_OTP_ALLOWED_PREFIXES", default="+243,+33,+1").split(",")
    if prefix.strip()
)
HUB_PHONE_HMAC_KEY = env.str("HUB_PHONE_HMAC_KEY", default="dev-phone-hmac-key-not-a-secret")
HUB_OTP_TTL_SECONDS = env.int("HUB_OTP_TTL_SECONDS", default=300)
HUB_OTP_PER_PHONE_HOURLY = env.int("HUB_OTP_PER_PHONE_HOURLY", default=3)
HUB_OTP_PER_IP_HOURLY = env.int("HUB_OTP_PER_IP_HOURLY", default=10)
HUB_OTP_PER_NETWORK_HOURLY = env.int("HUB_OTP_PER_NETWORK_HOURLY", default=30)
HUB_OTP_DORMANT_DAYS = env.int("HUB_OTP_DORMANT_DAYS", default=90)

# The identity provider (slice 01 open item 1): required only when the OIDC routes are on.
HUB_OIDC_ISSUER = env.str("HUB_OIDC_ISSUER", default="")
HUB_OIDC_CLIENT_ID = env.str("HUB_OIDC_CLIENT_ID", default="")
HUB_OIDC_CLIENT_SECRET = env.str("HUB_OIDC_CLIENT_SECRET", default="")
HUB_OIDC_REDIRECT_URI = env.str("HUB_OIDC_REDIRECT_URI", default="")

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
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "hub.identity.api.authentication.BearerJwtAuthentication",
    ],
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
