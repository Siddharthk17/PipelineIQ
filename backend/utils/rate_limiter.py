"""Rate limiting configuration for PipelineIQ API.

Uses slowapi with Redis backend for distributed rate limiting.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
