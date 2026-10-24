"""URLs served by every Django deployable.

Only the two health operations of ``api/openapi.yaml`` are mounted here: the executor guide
forbids any route that the contract does not define (no metrics route, no test-only route,
D-38). Every pool's URLConf includes this module under ``api/v1/``.
"""

from typing import Any

from hub.shared.api.health import health_urlpatterns

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [*health_urlpatterns]
