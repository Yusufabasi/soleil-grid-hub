"""Request metrics and trace/log correlation (docs/09 section 5, TC-NFR-09-1).

The route label must be the URL *template* so metrics never explode on ids, and the log
line's trace id must equal the OTel span's trace id so one search joins them.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from hub.shared.tracing import add_trace_id, configure_tracing


def _item_view(request, item_id: int) -> HttpResponse:  # noqa: ARG001
    return HttpResponse(b"{}", content_type="application/json")


urlpatterns = [path("api/v1/items/<int:item_id>/", _item_view, name="item-detail")]


def _sample(service: str, route: str, code: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "http_requests_total", {"service": service, "route": route, "code": code}
        )
        or 0.0
    )


@override_settings(ROOT_URLCONF=__name__, SERVICE_NAME="hub-test")
class RequestMetricsTests(SimpleTestCase):
    def test_request_metrics_recorded(self):
        route = "api/v1/items/<int:item_id>/"
        before = _sample("hub-test", route, "200")

        response = self.client.get("/api/v1/items/42/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_sample("hub-test", route, "200"), before + 1)

        # The label set is exactly {service, route, code}: no raw id leaks into a label.
        found = False
        for metric in REGISTRY.collect():
            for sample in metric.samples:
                if sample.name != "http_requests_total":
                    continue
                found = True
                self.assertEqual(set(sample.labels), {"service", "route", "code"})
                self.assertNotIn("42", sample.labels["route"])
        assert found, "http_requests_total not found in the registry"


class TracingTests(SimpleTestCase):
    def test_span_trace_id_matches_log_trace_id(self):
        exporter = InMemorySpanExporter()
        provider = configure_tracing("hub-test", exporter=exporter)
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("health") as span:
            event = add_trace_id(None, "info", {})

        span_context = span.get_span_context()
        assert span_context is not None
        span_trace_id = format(span_context.trace_id, "032x")
        self.assertEqual(event["trace_id"], span_trace_id)

        spans = exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        exported_context = spans[0].get_span_context()
        assert exported_context is not None
        self.assertEqual(format(exported_context.trace_id, "032x"), span_trace_id)
