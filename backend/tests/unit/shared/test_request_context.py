"""The inbound request id is untrusted input: replace it unless the load balancer is trusted.

docs/03 section 7.3 and D-13: only the balancer may set X-Request-Id, and only when it
matches the safe pattern; every response carries an id and the contextvar is cleared after.
"""

from __future__ import annotations

import uuid

from django.test import SimpleTestCase, override_settings

from hub.shared.context import request_id_var


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@override_settings(HUB_TRUST_LB_REQUEST_ID=False)
class UntrustedRequestIdTests(SimpleTestCase):
    def test_untrusted_request_id_is_replaced(self):
        injected = self.client.get("/", HTTP_X_REQUEST_ID="evil\r\nX-A: b")
        self.assertTrue(_is_uuid(injected["X-Request-Id"]))
        self.assertNotIn("evil", injected["X-Request-Id"])

        overlong = self.client.get("/", HTTP_X_REQUEST_ID="a" * 200)
        self.assertTrue(_is_uuid(overlong["X-Request-Id"]))

    def test_response_always_carries_request_id(self):
        response = self.client.get("/")

        self.assertIn("X-Request-Id", response)
        self.assertTrue(_is_uuid(response["X-Request-Id"]))

    def test_context_cleared_after_request(self):
        self.client.get("/")

        self.assertIsNone(request_id_var.get())


@override_settings(HUB_TRUST_LB_REQUEST_ID=True)
class TrustedRequestIdTests(SimpleTestCase):
    def test_valid_inbound_id_is_echoed(self):
        response = self.client.get("/", HTTP_X_REQUEST_ID="abc-12345678")

        self.assertEqual(response["X-Request-Id"], "abc-12345678")

    def test_invalid_inbound_id_is_replaced_even_when_trusted(self):
        response = self.client.get("/", HTTP_X_REQUEST_ID="not a valid id!")

        self.assertNotEqual(response["X-Request-Id"], "not a valid id!")
        self.assertTrue(_is_uuid(response["X-Request-Id"]))
