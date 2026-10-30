"""Catalog pipeline descriptions: fallback text and enqueue-once guard."""

from backend.routers import catalog_pipelines as cp


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        # Mirror the real cache client (decode_responses=True).
        value = self.store.get(key)
        return value.decode() if isinstance(value, bytes) else value

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value if isinstance(value, bytes) else str(value).encode()
        return True


SAMPLE_YAML = """pipeline:
  name: demo_pipe
  steps:
    - name: load_data
      type: load
      file_id: file-1
    - name: save_data
      type: save
"""


class TestResolveDescription:
    def test_fallback_when_nothing_cached(self):
        redis = FakeRedis()
        desc, prompt = cp._resolve_description(redis, "user-1", "demo_pipe", SAMPLE_YAML)
        assert desc == "A pipeline named 'demo_pipe' with 2 processing steps."
        assert prompt is not None
        assert "Pipeline YAML:" in prompt

    def test_cached_description_wins(self):
        redis = FakeRedis()
        redis.store[cp._cache_key("user-1", "demo_pipe")] = b"AI magic summary"
        desc, prompt = cp._resolve_description(redis, "user-1", "demo_pipe", SAMPLE_YAML)
        assert desc == "AI magic summary"
        assert prompt is None

    def test_missing_yaml_reports_no_runs(self):
        redis = FakeRedis()
        desc, prompt = cp._resolve_description(redis, "user-1", "ghost", None)
        assert "No runs found" in desc
        assert prompt is None

    def test_empty_yaml_counts_zero_steps(self):
        redis = FakeRedis()
        desc, prompt = cp._resolve_description(redis, "user-1", "empty_pipe", "")
        assert "with 0 processing steps" in desc
        assert prompt is None


class TestEnqueueGuard:
    def test_skips_without_api_key(self, monkeypatch):
        import backend.tasks.gemini_tasks as gemini_tasks

        monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "")
        calls = []
        monkeypatch.setattr(
            gemini_tasks.generate_pipeline_description_task,
            "apply_async",
            lambda *a, **k: calls.append((a, k)),
        )
        cp._enqueue_description(FakeRedis(), "k", "prompt", "tenant-1")
        assert calls == []

    def test_enqueues_once_per_cache_key(self, monkeypatch):
        import backend.tasks.gemini_tasks as gemini_tasks

        monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "test-key")
        calls = []

        def spy(*args, **kwargs):
            calls.append((args, kwargs))

            class _Dummy:
                id = "test-task-id"

            return _Dummy()

        monkeypatch.setattr(
            gemini_tasks.generate_pipeline_description_task, "apply_async", spy
        )
        redis = FakeRedis()
        cp._enqueue_description(redis, "k", "prompt", "tenant-1")
        cp._enqueue_description(redis, "k", "prompt", "tenant-1")
        cp._enqueue_description(redis, "k", "prompt", "tenant-1")
        assert len(calls) == 1, f"expected exactly one enqueue, got {len(calls)}"
