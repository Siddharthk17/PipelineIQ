"""OpenTelemetry distributed tracing for PipelineIQ.

Initializes OTel SDK with OTLP exporter (Jaeger), provides FastAPI
middleware and Celery signal hooks. Exposes get_tracer() for manual
spans in pipeline steps.
"""

from __future__ import annotations

import logging
from typing import Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION, DEPLOYMENT_ENVIRONMENT
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

from backend.config import settings

logger = logging.getLogger(__name__)

_TRACER_PROVIDER: Optional[TracerProvider] = None
_TRACER: Optional[trace.Tracer] = None
_fastapi_instrumented: bool = False
_sqlalchemy_instrumented: bool = False
_redis_instrumented: bool = False


def _get_otel_sample_rate() -> float:
    return getattr(settings, "OTEL_SAMPLE_RATE", 0.1)


def _get_otel_endpoint() -> str:
    return getattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")


def _is_otel_enabled() -> bool:
    return bool(getattr(settings, "OTEL_ENABLED", True))


def _get_otel_service_name() -> str:
    return getattr(settings, "OTEL_SERVICE_NAME", "pipelineiq")


OTEL_SERVICE_NAME = _get_otel_service_name()


def setup_telemetry() -> None:
    global _TRACER_PROVIDER, _TRACER

    if _TRACER_PROVIDER is not None:
        return

    if not _is_otel_enabled():
        logger.info("OTel: disabled via OTEL_ENABLED")
        return

    sample_rate = _get_otel_sample_rate()
    endpoint = _get_otel_endpoint()

    resource = Resource.create({
        SERVICE_NAME: _get_otel_service_name(),
        SERVICE_VERSION: settings.APP_VERSION,
        DEPLOYMENT_ENVIRONMENT: settings.ENVIRONMENT,
    })

    provider = TracerProvider(
        resource=resource,
        sampler=ParentBasedTraceIdRatio(sample_rate),
    )

    is_test = settings.ENVIRONMENT == "test" or "PYTEST_CURRENT_TEST" in __import__("os").environ
    if is_test:
        logger.info("OTel: test environment detected, traces dropped (no exporter)")
    else:
        try:
            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                insecure=True,
                timeout=2,
            )
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)
            logger.info("OTel initialized: endpoint=%s sample_rate=%s", endpoint, sample_rate)
        except Exception as exc:
            logger.warning("OTel exporter unavailable (telemetry will use no-op): %s", exc)

    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider
    _TRACER = provider.get_tracer("pipelineiq")


_celery_instrumented: bool = False


def setup_celery_telemetry() -> None:
    global _celery_instrumented
    if _celery_instrumented:
        return
    if _TRACER_PROVIDER is None:
        setup_telemetry()
    try:
        CeleryInstrumentor().instrument()
        logger.info("OTel: Celery instrumented")
    except Exception as exc:
        logger.warning("OTel: Celery instrumentation failed: %s", exc)
    _celery_instrumented = True


def instrument_fastapi(app):
    global _fastapi_instrumented
    if _fastapi_instrumented:
        return
    if _TRACER_PROVIDER is None:
        setup_telemetry()
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_TRACER_PROVIDER,
        excluded_urls="health,livez,readyz,metrics",
        server_request_hook=_enrich_span_with_request_context,
    )
    _fastapi_instrumented = True


def instrument_sqlalchemy(engine) -> None:
    global _sqlalchemy_instrumented
    if _sqlalchemy_instrumented:
        return
    if _TRACER_PROVIDER is None:
        setup_telemetry()
    try:
        SQLAlchemyInstrumentor().instrument(
            engine=engine,
            tracer_provider=_TRACER_PROVIDER,
        )
        logger.info("OTel: SQLAlchemy instrumented")
    except Exception as exc:
        logger.warning("OTel: SQLAlchemy instrumentation failed: %s", exc)
    _sqlalchemy_instrumented = True


def instrument_redis() -> None:
    global _redis_instrumented
    if _redis_instrumented:
        return
    if _TRACER_PROVIDER is None:
        setup_telemetry()
    try:
        RedisInstrumentor().instrument(
            tracer_provider=_TRACER_PROVIDER,
        )
        logger.info("OTel: Redis instrumented")
    except Exception as exc:
        logger.warning("OTel: Redis instrumentation failed: %s", exc)
    _redis_instrumented = True


def instrument_all(
    app,
    db_engine=None,
) -> None:
    setup_telemetry()
    instrument_fastapi(app)
    if db_engine is not None:
        instrument_sqlalchemy(db_engine)
    instrument_redis()


def _enrich_span_with_request_context(span, body):
    if span is None:
        return
    from fastapi import Request
    request: Request = body
    span.set_attribute("http.request_id", getattr(request.state, "request_id", ""))


def get_tracer() -> trace.Tracer:
    if _TRACER is None:
        setup_telemetry()
    return _TRACER or trace.get_tracer("pipelineiq")


def current_span_context() -> dict:
    span = trace.get_current_span()
    if span is None:
        return {}
    ctx = span.get_span_context()
    if ctx is None or ctx.trace_id == 0:
        return {}
    return {
        "trace_id": format_trace_id(ctx.trace_id),
        "span_id": format_span_id(ctx.span_id),
    }


def format_trace_id(tid: int) -> str:
    return format(tid, "032x")


def format_span_id(sid: int) -> str:
    return format(sid, "016x")


def force_flush() -> None:
    if _TRACER_PROVIDER is not None:
        _TRACER_PROVIDER.force_flush()


def reset_telemetry() -> None:
    """Reset telemetry state so worker children get fresh SDK post-fork."""
    global _TRACER_PROVIDER, _TRACER, _fastapi_instrumented
    global _sqlalchemy_instrumented, _redis_instrumented, _celery_instrumented
    _TRACER_PROVIDER = None
    _TRACER = None
    _fastapi_instrumented = False
    _sqlalchemy_instrumented = False
    _redis_instrumented = False
    _celery_instrumented = False
