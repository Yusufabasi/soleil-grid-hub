"""RFC 9457 problem documents for every failure path (docs/03 sections 7.1 and 7.7, docs/05 section 5).

One converter serves the DRF exception handler, the middleware ``process_exception`` and
the URLConf handlers, so a client branches on ``code`` alone and never sees a stack trace.
"""

from __future__ import annotations

import json

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import path
from rest_framework.exceptions import ValidationError

from hub.shared.api.problem import (
    exception_handler,
    handler404,  # noqa: F401 - bound so this module, used as URLConf, exposes handler404
    problem_response,
    to_problem,
)
from hub.shared.errors import (
    Conflict,
    Duplicate,
    ExternalUnavailable,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    RateLimited,
    Unauthenticated,
    Unexpected,
    ValidationFailed,
)

PROBLEM_BASE = "https://hub.soleilgrid.example/problems/"

# docs/03 section 7.1 table: (class, http status, default code from the docs/05 catalogue).
TAXONOMY = [
    (ValidationFailed, 422, "validation_error"),
    (Unauthenticated, 401, "unauthenticated"),
    (PermissionDenied, 403, "forbidden"),
    (NotFound, 404, "not_found"),
    (Conflict, 409, "conflict"),
    (Duplicate, 409, "duplicate_resource"),
    (RateLimited, 429, "rate_limited"),
    (ExternalUnavailable, 503, "provider_unavailable"),
    (InvariantViolation, 500, "invariant_violation"),
    (Unexpected, 500, "internal_error"),
]


def _raising_view(request, **kwargs):  # noqa: ARG001
    raise NotFound("customer_not_found")


urlpatterns = [path("api/v1/customers/<uuid:pk>/", _raising_view)]


@override_settings(ROOT_URLCONF=__name__, HUB_PROBLEM_BASE_URL=PROBLEM_BASE, DEBUG=False)
class ToProblemTests(SimpleTestCase):
    def test_domain_error_maps_to_status_and_code(self):
        for error_type, expected_status, expected_code in TAXONOMY:
            error = error_type()

            status_code, body = to_problem(
                error, instance="/api/v1/x", request_id="rid-1", accept_language=None
            )

            assert status_code == expected_status, error_type
            assert body["status"] == expected_status, error_type
            assert body["code"] == expected_code, error_type
            if isinstance(error, RateLimited):
                response = exception_handler(error, {"request": RequestFactory().get("/x")})
                assert response is not None
                assert response["Retry-After"] == str(error.retry_after_seconds)

    def test_body_shape_and_content_type(self):
        status_code, body = to_problem(
            NotFound("customer_not_found"),
            instance="/api/v1/customers/abc",
            request_id="rid-2",
            accept_language=None,
        )

        assert status_code == 404
        assert {
            "type",
            "title",
            "status",
            "code",
            "detail",
            "instance",
            "request_id",
        } <= set(body)
        assert body["type"].startswith(PROBLEM_BASE)
        assert body["instance"] == "/api/v1/customers/abc"
        assert body["request_id"] == "rid-2"
        # The same document served by a plain view carries the RFC media type.
        response = problem_response(status_code, body)
        assert response["Content-Type"].split(";")[0] == "application/problem+json"

    def test_unexpected_exception_hides_detail(self):
        status_code, body = to_problem(
            RuntimeError("secret sql"), instance="/x", request_id="rid-3", accept_language=None
        )
        response = exception_handler(
            RuntimeError("secret sql"), {"request": RequestFactory().get("/x")}
        )
        assert response is not None

        assert status_code == 500
        assert body["code"] == "internal_error"
        assert "secret" not in json.dumps(body)
        assert response.status_code == 500
        assert response.data["code"] == "internal_error"
        assert "secret" not in json.dumps(response.data)

    def test_drf_validation_error_has_errors_list(self):
        request = RequestFactory().post("/api/v1/top-ups")

        response = exception_handler(
            ValidationError({"amount_minor": ["A valid integer is required."]}),
            {"request": request},
        )
        assert response is not None

        assert response.status_code == 422
        assert response.data["code"] == "validation_error"
        assert response.data["errors"][0]["field"] == "/amount_minor"
        assert {"field", "code", "message"} <= set(response.data["errors"][0])

    def test_detail_localisation_reads_accept_language(self):
        english = to_problem(NotFound(), instance="/x", request_id="r", accept_language="en")
        french = to_problem(
            NotFound(), instance="/x", request_id="r", accept_language="fr-CA,fr;q=0.9"
        )
        default = to_problem(NotFound(), instance="/x", request_id="r", accept_language=None)

        assert english[1]["detail"] != french[1]["detail"]
        assert default[1]["detail"] == english[1]["detail"]


@override_settings(ROOT_URLCONF=__name__, HUB_PROBLEM_BASE_URL=PROBLEM_BASE, DEBUG=False)
class MiddlewareAndHandlerTests(SimpleTestCase):
    def test_plain_view_domain_error_via_middleware(self):
        response = self.client.get("/api/v1/customers/7d9a5b3e-0000-7000-8000-000000000001/")

        assert response.status_code == 404
        assert response["Content-Type"].split(";")[0] == "application/problem+json"
        assert json.loads(response.content)["code"] == "customer_not_found"
        assert response["X-Request-Id"]

    def test_404_handler_is_problem_json(self):
        response = self.client.get("/no-such-path/")

        assert response.status_code == 404
        assert response["Content-Type"].split(";")[0] == "application/problem+json"
        body = json.loads(response.content)
        assert body["code"] == "not_found"
        assert body["instance"] == "/no-such-path/"
