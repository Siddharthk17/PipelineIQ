"""
Redpanda (Kafka-compatible) client wrapper for PipelineIQ.

All Kafka-compatible API calls work unchanged with Redpanda.
The broker address is configurable via REDPANDA_BROKERS env var.

Includes:
- Circuit Breaker pattern for broker outage resilience
- Redis fallback queue for zero data loss during outages
- Topic name validation to prevent invalid topic creation
"""
import orjson
import logging
import os
import re
import time
from enum import Enum

from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic

logger = logging.getLogger(__name__)

# Topic name validation: alphanumeric, hyphens, underscores, dots only
# Must not start with __ (internal topics reserved)
VALID_TOPIC_RE = re.compile(r'^[a-zA-Z0-9._-]+$')
INVALID_TOPIC_PREFIXES = ('__', '_')


def validate_topic_name(topic: str) -> str:
    """Validate topic name against Kafka naming rules.

    Returns cleaned topic name or raises ValueError.
    """
    if not topic or not topic.strip():
        raise ValueError("Topic name cannot be empty")
    topic = topic.strip()
    if len(topic) > 249:
        raise ValueError(f"Topic name too long ({len(topic)} chars, max 249)")
    if topic.startswith(INVALID_TOPIC_PREFIXES):
        raise ValueError(f"Topic name '{topic}' must not start with {INVALID_TOPIC_PREFIXES}")
    if not VALID_TOPIC_RE.match(topic):
        raise ValueError(
            f"Topic name '{topic}' contains invalid characters. "
            "Only alphanumeric, dots, hyphens, and underscores allowed."
        )
    return topic


def _get_brokers() -> str:
    return os.environ.get("REDPANDA_BROKERS", "redpanda:9092")

DEFAULT_PARTITIONS  = 8         # 8 partitions = 8 parallel consumers — fixes Bottleneck #15
DEFAULT_RETENTION_MS = "86400000"  # 24 hours
DEFAULT_SEGMENT_BYTES = "104857600"  # 100MB


# Circuit Breaker — prevents hammering a failing Redpanda broker

class CircuitState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Broker failing — reject immediately
    HALF_OPEN = "half_open" # Testing if broker recovered


class CircuitBreaker:
    """Circuit breaker for Redpanda producer/consumer operations.

    States:
    - CLOSED: Normal. Failures increment counter.
    - OPEN: After failure_threshold consecutive failures. All calls fail fast.
    - HALF_OPEN: After recovery_timeout seconds. One test call allowed.
                 If it succeeds → CLOSED. If it fails → OPEN again.

    Usage:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        if cb.is_open():
            # Use Redis fallback queue instead
            fallback_enqueue(payload)
        else:
            try:
                producer.produce(...)
                cb.record_success()
            except Exception:
                cb.record_failure()
                raise
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: int = 30,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._total_failures = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker → HALF_OPEN (testing recovery)")
        return self._state

    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    def record_success(self) -> None:
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            logger.info("Circuit breaker → CLOSED (recovered)")

    def record_failure(self) -> None:
        self._failure_count += 1
        self._total_failures += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            if self._state != CircuitState.OPEN:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker → OPEN (failures=%d, threshold=%d)",
                    self._failure_count, self._failure_threshold,
                )

    @property
    def total_failures(self) -> int:
        return self._total_failures

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0


# Redis Fallback Queue — zero data loss during broker outages

_FALLBACK_QUEUE_KEY = "redpanda:fallback_queue"
_FALLBACK_MAX_SIZE = 100_000  # Max messages in fallback before dropping oldest


def _get_redis_client():
    """Lazy Redis client for fallback queue. Returns None if Redis unavailable."""
    try:
        import redis
        from backend.db.redis_pools import get_pubsub_redis
        return get_pubsub_redis()
    except Exception:
        return None


def fallback_enqueue(topic: str, value: bytes, key: bytes | None = None) -> bool:
    """Store a message in Redis fallback queue when Redpanda is down.

    Returns True if enqueued, False if queue is full or Redis unavailable.
    """
    try:
        r = _get_redis_client()
        if r is None:
            logger.error("Redis unavailable — cannot enqueue fallback message")
            return False

        item = orjson.dumps({
            "topic": topic,
            "value": value.decode("utf-8", errors="replace"),
            "key": key.decode("utf-8", errors="replace") if key else None,
            "enqueued_at": time.time(),
        }).decode()

        # Trim oldest if queue exceeds max size
        r.lpush(_FALLBACK_QUEUE_KEY, item)
        r.ltrim(_FALLBACK_QUEUE_KEY, 0, _FALLBACK_MAX_SIZE - 1)
        size = r.llen(_FALLBACK_QUEUE_KEY)
        logger.warning("Fallback queue: %d messages (topic=%s)", size, topic)
        return True
    except Exception as exc:
        logger.error("Fallback enqueue failed: %s", exc)
        return False


def fallback_drain(producer: Producer, batch_size: int = 100) -> int:
    """Drain fallback queue back into Redpanda. Call after broker recovers.

    Returns number of messages successfully drained.
    """
    try:
        r = _get_redis_client()
        if r is None:
            return 0

        drained = 0
        while True:
            items = r.rpop(_FALLBACK_QUEUE_KEY, count=min(batch_size, 50))
            if not items:
                break
            for raw in items:
                try:
                    item = orjson.loads(raw)
                    producer.produce(
                        topic=item["topic"],
                        value=item["value"].encode(),
                        key=item.get("key", "").encode() if item.get("key") else None,
                    )
                    drained += 1
                except Exception as exc:
                    logger.error("Failed to drain fallback message: %s", exc)
            producer.poll(0)

        producer.flush(timeout=10)
        if drained > 0:
            logger.info("Drained %d messages from fallback queue", drained)
        return drained
    except Exception as exc:
        logger.error("Fallback drain failed: %s", exc)
        return 0


def fallback_queue_size() -> int:
    """Return current fallback queue size."""
    try:
        r = _get_redis_client()
        if r is None:
            return 0
        return r.llen(_FALLBACK_QUEUE_KEY)
    except Exception:
        return 0


# Admin Client

class RedpandaAdminClient:
    """Manages Redpanda topics — create, list, delete."""

    def __init__(self, brokers: str | None = None):
        self._brokers = brokers or _get_brokers()
        self._client  = AdminClient({
            "bootstrap.servers": self._brokers,
            "socket.timeout.ms": 10_000,
            "request.timeout.ms": 15_000,
        })

    def create_topic(
        self,
        topic: str,
        partitions: int = DEFAULT_PARTITIONS,
        retention_ms: str = DEFAULT_RETENTION_MS,
    ) -> bool:
        """
        Create topic with given partitions + corresponding DLQ topic ({topic}.dlq).

        Default 8 partitions directly fixes Bottleneck #15 — single consumer limit.
        DLQ uses 1 partition (ordered for debugging), 7-day retention.
        Returns True if created, False if already existed.
        """
        # Validate topic name before attempting creation
        topic = validate_topic_name(topic)

        topics_to_create = [
            NewTopic(
                topic=topic,
                num_partitions=partitions,
                replication_factor=1,
                config={
                    "retention.ms":   retention_ms,
                    "segment.bytes":  DEFAULT_SEGMENT_BYTES,
                    "cleanup.policy": "delete",
                },
            ),
            NewTopic(
                topic=f"{topic}.dlq",
                num_partitions=1,
                replication_factor=1,
                config={"retention.ms": "604800000"},  # 7 days for DLQ
            ),
        ]

        futures = self._client.create_topics(topics_to_create)
        created = True
        for t_name, future in futures.items():
            try:
                future.result()
                logger.info(f"Created topic: {t_name}")
            except Exception as e:
                if "already_exists" in str(e).lower() or "already exists" in str(e).lower():
                    created = False
                else:
                    raise
        return created

    def list_topics(self) -> list[dict]:
        meta = self._client.list_topics(timeout=10)
        topics = []
        for name, topic_meta in meta.topics.items():
            if name.startswith("_"):
                continue
            topics.append({
                "name":       name,
                "partitions": len(topic_meta.partitions),
                "is_dlq":     name.endswith(".dlq"),
            })
        return sorted(topics, key=lambda t: t["name"])

    def delete_topic(self, topic: str) -> None:
        to_delete = [topic, f"{topic}.dlq"]
        futures = self._client.delete_topics(to_delete)
        for t, future in futures.items():
            try:
                future.result()
                logger.info(f"Deleted topic: {t}")
            except Exception as e:
                if "unknown_topic" not in str(e).lower():
                    logger.warning(f"Delete topic {t}: {e}")

    def topic_exists(self, topic: str) -> bool:
        meta = self._client.list_topics(timeout=5)
        return topic in meta.topics

    def ensure_topic(self, topic: str, partitions: int = DEFAULT_PARTITIONS) -> None:
        if not self.topic_exists(topic):
            self.create_topic(topic, partitions=partitions)


def make_consumer(consumer_group: str, auto_offset_reset: str = "latest") -> Consumer:
    """
    Create a consumer with PipelineIQ defaults.
    'latest' = process only new messages (production mode).
    'earliest' = replay all messages from start (backfill mode).
    """
    return Consumer({
        "bootstrap.servers":        _get_brokers(),
        "group.id":                 consumer_group,
        "auto.offset.reset":        auto_offset_reset,
        "enable.auto.commit":       False,
        "session.timeout.ms":       30_000,
        "heartbeat.interval.ms":    10_000,
        "max.poll.interval.ms":     300_000,
        "fetch.min.bytes":          1,
        "fetch.wait.max.ms":        500,
    })


def make_producer() -> Producer:
    return Producer({
        "bootstrap.servers":  _get_brokers(),
        "acks":               1,
        "linger.ms":          5,
        "batch.size":         16_384,
        "compression.type":   "snappy",
        "retries":            3,
        "retry.backoff.ms":   100,
    })


def make_dlq_producer() -> Producer:
    """DLQ producer uses strong delivery guarantees (acks='all')."""
    return Producer({
        "bootstrap.servers":  _get_brokers(),
        "acks":               "all",  # all replicas must acknowledge
        "retries":            10,
        "linger.ms":          0,      # immediate send, no batching
    })


# Global circuit breaker instance — shared across all producer calls

_producer_cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)


def get_producer_circuit_breaker() -> CircuitBreaker:
    """Return the global producer circuit breaker."""
    return _producer_cb


def produce_with_fallback(
    producer: Producer,
    topic: str,
    value: bytes,
    key: bytes | None = None,
    headers: list | None = None,
) -> bool:
    """Produce a message to Redpanda with circuit breaker + fallback.

    Returns True if message was sent to Redpanda, False if routed to fallback queue.
    """
    cb = get_producer_circuit_breaker()

    if cb.is_open():
        # Circuit open — use fallback queue
        logger.warning(
            "Circuit breaker OPEN — routing message to fallback queue (topic=%s)",
            topic,
        )
        return fallback_enqueue(topic, value, key)

    try:
        producer.produce(topic=topic, value=value, key=key, headers=headers)
        producer.poll(0)
        cb.record_success()
        return True
    except Exception as exc:
        cb.record_failure()
        logger.error("Producer failed (failures=%d): %s", cb.total_failures, exc)
        # Try fallback
        if fallback_enqueue(topic, value, key):
            return False
        # Fallback also failed — re-raise
        raise


def try_drain_fallback(producer: Producer | None = None) -> int:
    """Attempt to drain fallback queue if circuit breaker recovered.

    Call this periodically in the streaming task main loop.
    Returns number of messages drained.
    """
    cb = get_producer_circuit_breaker()
    if cb.state == CircuitState.CLOSED and fallback_queue_size() > 0:
        if producer is None:
            producer = make_producer()
        return fallback_drain(producer)
    return 0


_admin_client: RedpandaAdminClient | None = None

def get_admin_client() -> RedpandaAdminClient:
    global _admin_client
    if _admin_client is None:
        _admin_client = RedpandaAdminClient()
    return _admin_client
