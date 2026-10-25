"""Routes of the identity module. The URL name is the ``operationId`` (docs/03 7.7).

Only operations that ``api/openapi.yaml`` declares are mounted here (executor guide rule 4):
the cookie endpoints of D-17 today, the DRF resources as later tasks of slice 01 add them.
"""

from typing import Any

from django.urls import path

from hub.identity.api.cookie_views import logout_view, refresh_view

__all__ = ["urlpatterns"]

urlpatterns: list[Any] = [
    path("auth/refresh", refresh_view, name="refreshToken"),
    path("auth/logout", logout_view, name="logout"),
]
