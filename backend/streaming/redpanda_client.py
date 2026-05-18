"""
Redpanda (Kafka-compatible) client wrapper for PipelineIQ.

All Kafka-compatible API calls work unchanged with Redpanda.
The broker address is configurable via REDPANDA_BROKERS env var.
"""
import logging
import os

from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic

logger = logging.getLogger(__name__)

def _get_brokers() -> str:
    return os.environ.get("REDPANDA_BROKERS", "redpanda:9092")

DEFAULT_PARTITIONS  = 8         # 8 partitions = 8 parallel consumers — fixes Bottleneck #15
DEFAULT_RETENTION_MS = "86400000"  # 24 hours
DEFAULT_SEGMENT_BYTES = "104857600"  # 100MB


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
        "enable.auto.commit":       True,
        "auto.commit.interval.ms":  5000,
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


_admin_client: RedpandaAdminClient | None = None

def get_admin_client() -> RedpandaAdminClient:
    global _admin_client
    if _admin_client is None:
        _admin_client = RedpandaAdminClient()
    return _admin_client
