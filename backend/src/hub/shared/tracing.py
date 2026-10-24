"""OpenTelemetry tracing (docs/03 section 7.4, docs/09 section 6).

The SDK is configured once per process from ``OTEL_EXPORTER_OTLP_ENDPOINT``; a caller may
pass its own exporter (tests use an in-memory one). Django, psycopg and redis are
instrumented, and the Django request hook records ``url.path`` only so a stream ticket in
the query string never reaches the collector (D-18). The balancer's W3C ``traceparent`` is
honoured automatically by the Django instrumentor, and :func:`add_trace_id` copies the
current span's id into every log line so one search joins logs and traces (TC-NFR-09-1).
"""

from __future__ import annotations

import os
from typing import Any

from django.http import HttpRequest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span
from structlog.typing import EventDict, WrappedLogger

__all__ = ["add_trace_id", "configure_tracing"]

_provider: TracerProvider | None = None

# Attributes the HTTP instrumentations fill from the request line; all of them are rewritten
# to the bare path by _request_hook.
_QUERY_BEARING = ("http.url", "url.full", "http.target", "url.query")


def _request_hook(span: Span, request: HttpRequest) -> None:
    """Record ``url.path`` only: no query string, therefore no stream ticket (D-18)."""
    # getattr because the API's Span type does not declare the attribute the SDK fills in.
    attributes = dict(getattr(span, "attributes", None) or {})
    span.set_attribute("url.path", request.path)
    for name in _QUERY_BEARING:
        value = attributes.get(name)
        if isinstance(value, str) and name.endswith("query"):
            span.set_attribute(name, "")
        elif isinstance(value, str):
            span.set_attribute(name, value.split("?", 1)[0])


def add_trace_id(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Copy the active span's id into the log record, 32 lowercase hex digits."""
    span_context = trace.get_current_span().get_span_context()
    event_dict["trace_id"] = (
        format(span_context.trace_id, "032x") if span_context.is_valid else None
    )
    return event_dict


def configure_tracing(service: str, *, exporter: SpanExporter | None = None) -> TracerProvider:
    """Build the process-wide tracer provider and instrument the stack.

    ``exporter`` overrides the OTLP exporter built from ``OTEL_EXPORTER_OTLP_ENDPOINT``;
    when neither is available the provider still assigns real trace ids, which is what the
    log correlation needs. A second call returns the provider already installed.
    """
    global _provider  # noqa: PLW0603 - one tracer provider per process, set once

    provider = _provider
    if provider is None:
        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        span_exporter = exporter
        if span_exporter is None:
            endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            if endpoint:
                span_exporter = OTLPSpanExporter(endpoint=endpoint)
        if span_exporter is not None:
            processor: Any = (
                SimpleSpanProcessor(span_exporter)
                if exporter is not None
                else BatchSpanProcessor(span_exporter)
            )
            provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        _provider = provider

        DjangoInstrumentor().instrument(
            tracer_provider=provider,
            request_hook=_request_hook,
            # RequestContextMiddleware stays first (docs/03 section 7.3).
            middleware_position=1,
        )
        PsycopgInstrumentor().instrument(tracer_provider=provider)
        RedisInstrumentor().instrument(tracer_provider=provider)

    return provider
