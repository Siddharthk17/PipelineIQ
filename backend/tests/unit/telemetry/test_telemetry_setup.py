"""Unit tests for the OpenTelemetry setup module.

Tests ``backend.telemetry`` in isolation — configuration helpers, tracer
initialisation, subprocess instrumentation wrappers, and format utilities.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from backend.telemetry import (
    OTEL_SERVICE_NAME,
    _get_otel_sample_rate,
    _get_otel_endpoint,
    _is_otel_enabled,
    format_trace_id,
    format_span_id,
    current_span_context,
    setup_telemetry,
    get_tracer,
)
from backend.config import settings


class TestOTelConfigHelpers:
    """Configuration helper functions."""

    def test_get_otel_sample_rate_default(self):
        rate = _get_otel_sample_rate()
        assert 0.0 <= rate <= 1.0

    def test_get_otel_sample_rate_from_settings(self):
        assert _get_otel_sample_rate() == settings.OTEL_SAMPLE_RATE

    def test_get_otel_endpoint_default(self):
        endpoint = _get_otel_endpoint()
        assert endpoint.startswith("http")
        assert ":" in endpoint

    def test_get_otel_endpoint_from_settings(self):
        assert _get_otel_endpoint() == settings.OTEL_EXPORTER_OTLP_ENDPOINT

    def test_is_otel_enabled_default(self):
        assert _is_otel_enabled() is True

    def test_otel_service_name_from_settings(self):
        assert OTEL_SERVICE_NAME == settings.OTEL_SERVICE_NAME


class TestFormatUtilities:
    """trace/span ID formatting."""

    def test_format_trace_id(self):
        tid = 0xabcdef1234567890
        result = format_trace_id(tid)
        assert isinstance(result, str)
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)
        assert result.endswith("abcdef1234567890")

    def test_format_span_id(self):
        sid = 0x1234567890abcdef
        result = format_span_id(sid)
        assert isinstance(result, str)
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_format_trace_id_round_trip(self):
        tid = 12345678901234567890
        as_str = format_trace_id(tid)
        parsed = int(as_str, 16)
        assert parsed == tid

    def test_current_span_context_no_span(self):
        ctx = current_span_context()
        assert ctx == {}


class TestGetTracer:
    """get_tracer() behaviour."""

    def test_get_tracer_returns_tracer(self):
        tracer = get_tracer()
        assert tracer is not None


class TestSetupTelemetryIdempotency:
    """setup_telemetry can be called multiple times safely."""

    def test_setup_telemetry_is_idempotent(self):
        setup_telemetry()
        setup_telemetry()
        setup_telemetry()
        tracer = get_tracer()
        assert tracer is not None
