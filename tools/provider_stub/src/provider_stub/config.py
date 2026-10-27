"""Configuration of the double: read from the environment once, refused when incomplete.

**No secret has a default.** A double that invents a webhook secret would let a test pass while
a deployment silently signed with the wrong key, and the whole point of the double is to make
the hub's verification meaningful. So `load()` raises :class:`ConfigError` and the process
refuses to start; the compose service passes every value it needs and nothing here carries one.

The latency knobs are not secrets and do have defaults: a double is *supposed* to be slow enough
to look like a mobile-money provider, but only when `STUB_SIMULATE_LATENCY=1`, which keeps unit
tests instant and the local stack realistic.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["PROVIDERS", "Config", "ConfigError", "load"]

# The channels the contract exposes, plus `synthetic` for the availability probe (D-39, and see
# plan note 1: the OpenAPI enum does not know it yet).
PROVIDERS = ("mpesa_cd", "airtel_cd", "orange_cd", "synthetic")

# `STUB_SECRET_<PROVIDER>` — one required secret per channel, derived from the name so adding a
# channel cannot silently leave it unverified.
def _secret_variable(provider: str) -> str:
    return f"STUB_SECRET_{provider.upper()}"


@dataclass(frozen=True, slots=True)
class Config:
    """Everything the double needs, resolved once at start-up."""

    secrets: Mapping[str, str]
    control_token: str
    hub_base_url: str
    journal_path: str
    latency_p50_ms: int
    latency_p99_ms: int
    auto_confirm_seconds: float
    simulate_latency: bool
    seed: int
    port: int

    def secret_for(self, provider: str) -> str:
        """The webhook secret of ``provider``; an unknown channel is a bug, not a 500."""
        try:
            return self.secrets[provider]
        except KeyError as exc:  # pragma: no cover - the router refuses unknown providers first
            raise ConfigError(f"no secret configured for provider {provider!r}") from exc

    def webhook_url(self, provider: str) -> str:
        """The hub's webhook route for ``provider`` — the only path the double ever posts to."""
        return f"{self.hub_base_url.rstrip('/')}/api/v1/webhooks/providers/{provider}"


class ConfigError(RuntimeError):
    """A required variable is missing or unusable: the process must not start."""


def _required_text(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required and has no default (no secret lives on disk)")
    return value


def _int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def load(environ: Mapping[str, str] | None = None) -> Config:
    """Read the configuration, or refuse to start.

    ``environ`` is a parameter so a test can prove the refusal without touching the process
    environment — including the case that matters most: a *missing* secret.
    """
    source = os.environ if environ is None else environ
    secrets = {provider: _required_text(source, _secret_variable(provider)) for provider in PROVIDERS}
    return Config(
        secrets=secrets,
        control_token=_required_text(source, "STUB_CONTROL_TOKEN"),
        hub_base_url=source.get("STUB_HUB_BASE_URL", "").strip() or "http://haproxy:8080",
        journal_path=source.get("STUB_JOURNAL_PATH", "").strip()
        or "/var/lib/provider_stub/journal.jsonl",
        latency_p50_ms=_int(source, "STUB_LATENCY_P50_MS", 400),
        latency_p99_ms=_int(source, "STUB_LATENCY_P99_MS", 3000),
        auto_confirm_seconds=_float(source, "STUB_AUTO_CONFIRM_SECONDS", 5.0),
        simulate_latency=source.get("STUB_SIMULATE_LATENCY", "").strip() in {"1", "true", "yes"},
        seed=_int(source, "STUB_SEED", 20261027),
        port=_int(source, "STUB_PORT", 9100),
    )
