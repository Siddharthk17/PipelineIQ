"""Topic provisioning + offset-choice tests (w1-daemon, M-006/M-131).

Covers the two gaps left by the first pass: the ownership gate reads
topic DataAssets that nothing wrote (fail-open), and the offset reset
was hard-coded with no per-pipeline choice.
"""


class TestM006TopicProvisioning:
    def _owner_run(self, test_db, topic="orders"):
        import uuid as uuid_module

        from backend.models import PipelineRun, PipelineStatus, User

        owner = User(
            id=uuid_module.uuid4(),
            email="topic.owner@t.local",
            username="topic_owner",
            hashed_password="x",
            role="user",
        )
        test_db.add(owner)
        run = PipelineRun(
            id=uuid_module.uuid4(),
            name="stream-run",
            status=PipelineStatus.PENDING,
            yaml_config="pipeline:\n  name: s\n  steps: []\n",
            user_id=owner.id,
        )
        test_db.add(run)
        test_db.commit()
        return owner, run

    def test_unclaimed_topic_allowed_then_registered(self, test_db):
        from backend.tasks.streaming_pipeline import (
            _register_topic_assets,
            check_topic_access,
        )

        owner, _ = self._owner_run(test_db)
        allowed, _ = check_topic_access(test_db, "orders", owner.id)
        assert allowed is True
        _register_topic_assets(test_db, owner.id, ["orders"])
        test_db.commit()
        from backend.models import DataAsset

        rows = (
            test_db.query(DataAsset)
            .filter(DataAsset.asset_type == "topic", DataAsset.name == "orders")
            .all()
        )
        assert len(rows) == 1
        assert str(rows[0].owner_id) == str(owner.id)

    def test_other_owner_denied(self, test_db):
        import uuid as uuid_module

        from backend.models import DataAsset, User
        from backend.tasks.streaming_pipeline import check_topic_access

        owner, _ = self._owner_run(test_db)
        intruder = User(
            id=uuid_module.uuid4(),
            email="intruder@t.local",
            username="intruder",
            hashed_password="x",
            role="user",
        )
        test_db.add(intruder)
        test_db.add(
            DataAsset(
                asset_type="topic",
                name="victim-events",
                namespace="redpanda://localhost:9092",
                owner_id=owner.id,
            )
        )
        test_db.commit()
        allowed, reason = check_topic_access(test_db, "victim-events", intruder.id)
        assert allowed is False
        assert reason

    def test_owner_reentry_allowed(self, test_db):
        from backend.tasks.streaming_pipeline import (
            _register_topic_assets,
            check_topic_access,
        )

        owner, _ = self._owner_run(test_db)
        _register_topic_assets(test_db, owner.id, ["orders"])
        test_db.commit()
        allowed, _ = check_topic_access(test_db, "orders", owner.id)
        assert allowed is True

    def test_registration_idempotent(self, test_db):
        from backend.models import DataAsset
        from backend.tasks.streaming_pipeline import _register_topic_assets

        owner, _ = self._owner_run(test_db)
        _register_topic_assets(test_db, owner.id, ["orders"])
        test_db.commit()
        _register_topic_assets(test_db, owner.id, ["orders"])
        test_db.commit()
        assert (
            test_db.query(DataAsset)
            .filter(DataAsset.asset_type == "topic", DataAsset.name == "orders")
            .count()
            == 1
        )


class TestM131OffsetChoice:
    def test_default_is_earliest(self):
        from unittest.mock import patch

        from backend.tasks import streaming_pipeline as sp

        seen = {}

        def fake_make_consumer(group, **kwargs):
            seen.update(kwargs)
            return object()

        with patch.object(sp, "make_consumer", side_effect=fake_make_consumer):
            sp._daemon_consumer("piq-fresh-group")
        assert seen.get("auto_offset_reset") == "earliest"

    def test_explicit_latest_honored(self):
        from unittest.mock import patch

        from backend.tasks import streaming_pipeline as sp

        seen = {}

        def fake_make_consumer(group, **kwargs):
            seen.update(kwargs)
            return object()

        with patch.object(sp, "make_consumer", side_effect=fake_make_consumer):
            sp._daemon_consumer("piq-g", auto_offset_reset="latest")
        assert seen.get("auto_offset_reset") == "latest"

    def test_invalid_reset_falls_back_to_earliest(self):
        from unittest.mock import patch

        from backend.tasks import streaming_pipeline as sp

        seen = {}

        def fake_make_consumer(group, **kwargs):
            seen.update(kwargs)
            return object()

        with patch.object(sp, "make_consumer", side_effect=fake_make_consumer):
            sp._daemon_consumer("piq-g", auto_offset_reset="sometimes")
        assert seen.get("auto_offset_reset") == "earliest"

    def test_group_id_uses_full_run_id(self):
        from backend.tasks.streaming_pipeline import _daemon_group_id

        run_id = "abcdef12-3456-7890-abcd-ef1234567890"
        assert _daemon_group_id(run_id, None) == f"piq-{run_id}"
        assert _daemon_group_id(run_id, "custom") == "custom"
