"""The mobile-money provider double the hub is developed and demonstrated against (slice T1).

Nothing here imports the hub: the double is the *provider*, and a double that shared code with
the code under test could agree with it about a mistake. What it shares is the wire contract —
`schemas/` holds the JSON schemas every document it produces is validated against, and
`schemas/webhook_envelope.schema.json` is a copy of the OpenAPI schema the hub validates with.

Modules:

* :mod:`provider_stub.config` — environment, read once, failing fast on a missing secret.
* :mod:`provider_stub.state` — collections and deliveries, appended to a journal as they change.
* :mod:`provider_stub.server` — the HTTP surface: the provider API and the control API.
* :mod:`provider_stub.signing` — the HMAC the hub verifies (D-09).
* :mod:`provider_stub.deliver` — the scheduler that sends signed envelopes.
* :mod:`provider_stub.scenarios` — the faults a test asks for by name.
* :mod:`provider_stub.statements` — the daily and monthly CSVs slice 14 reconciles.
* :mod:`provider_stub.probe` — the synthetic liveness probe (D-39).
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
