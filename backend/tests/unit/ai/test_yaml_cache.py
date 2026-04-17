"""Tests for YAML parse cache behavior."""

import hashlib
import pickle

import pytest

from backend.pipeline import cache as cache_module


class _FakeRedis:
    def __init__(self):
        self._data: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.delete_calls: list[str] = []

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self._data[key] = value
        self.set_calls.append((key, value, ex or 0))

    def delete(self, key: str):
        self._data.pop(key, None)
        self.delete_calls.append(key)


class TestYamlCacheKeys:
    def test_cache_key_is_sha256_of_raw_yaml(self):
        yaml_text = "pipeline:\n  name: test\n  steps: []"
        yaml_hash = hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
        assert f"{cache_module.YAML_CACHE_PREFIX}{yaml_hash}" == f"yaml:parsed:{yaml_hash}"

    def test_identical_yaml_produces_identical_hash(self):
        yaml_text = "pipeline:\n  name: test\n  steps: []"
        h1 = hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
        h2 = hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
        assert h1 == h2

    def test_different_yaml_produces_different_hash(self):
        h1 = hashlib.sha256(b"pipeline:\n  name: a\n  steps: []").hexdigest()
        h2 = hashlib.sha256(b"pipeline:\n  name: b\n  steps: []").hexdigest()
        assert h1 != h2


class TestYamlCacheModule:
    def test_yaml_cache_ttl_is_one_hour(self):
        assert cache_module.YAML_CACHE_TTL == 3600

    def test_cache_hit_skips_parser(self, monkeypatch):
        fake_redis = _FakeRedis()
        yaml_text = "pipeline:\n  name: cache_hit\n  steps: []"
        cache_key = (
            f"{cache_module.YAML_CACHE_PREFIX}"
            f"{hashlib.sha256(yaml_text.encode('utf-8')).hexdigest()}"
        )
        cached_pipeline = {"name": "from_cache"}
        fake_redis._data[cache_key] = pickle.dumps(cached_pipeline).decode("latin-1")

        monkeypatch.setattr(cache_module, "get_cache_redis", lambda: fake_redis)
        monkeypatch.setattr(
            cache_module._parser,
            "parse",
            lambda _: (_ for _ in ()).throw(AssertionError("parser should not run")),
        )

        result = cache_module.get_parsed_pipeline(yaml_text)
        assert result == cached_pipeline

    def test_cache_miss_calls_parser_and_writes_cache(self, monkeypatch):
        fake_redis = _FakeRedis()
        yaml_text = "pipeline:\n  name: cache_miss\n  steps: []"
        parsed_pipeline = {"name": "parsed"}

        monkeypatch.setattr(cache_module, "get_cache_redis", lambda: fake_redis)
        monkeypatch.setattr(cache_module._parser, "parse", lambda _: parsed_pipeline)

        result = cache_module.get_parsed_pipeline(yaml_text)
        assert result == parsed_pipeline
        assert len(fake_redis.set_calls) == 1
        _, _, ttl = fake_redis.set_calls[0]
        assert ttl == cache_module.YAML_CACHE_TTL

    def test_empty_yaml_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            cache_module.get_parsed_pipeline("")

    def test_whitespace_only_yaml_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            cache_module.get_parsed_pipeline(" \n\t ")

