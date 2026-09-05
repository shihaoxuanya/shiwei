import base64
from uuid import uuid4
import pytest
from fastapi import HTTPException
from shiwei_control.models import Action, Artifact, Draft
from shiwei_control.releases import Releases, bucket
from shiwei_control.storage import Store


class ValidStorage:
    def verify(self, artifact):
        pass  # Only injected in isolated tests; production always verifies GitHub bytes.


def artifact(v="0.3.1"):
    return Artifact(version=v, artifact_url=f"https://github.com/shihaoxuanya/shiwei-releases/releases/download/v{v}/Shiwei_{v}_x64-setup.exe",
                    signature=base64.b64encode(b"untrusted comment: test fixture").decode(), sha256="a" * 64, size=1024, release_notes="修复检索")


@pytest.fixture
def releases(tmp_path):
    return Releases(Store(tmp_path / "control.db"), ValidStorage())


def change(releases, row, action, **options):
    return releases.change(row["version"], Action(action=action, revision=row["revision"], confirm_version=row["version"], reason="隔离回归验证", **options), "admin-test")


def test_lifecycle(releases):
    row = releases.draft(Draft(version="0.3.1"), "admin-test")
    with pytest.raises(HTTPException):
        change(releases, row, "publish")
    row = releases.ready(artifact())
    assert row["status"] == "READY"
    assert releases.check("0.3.0", str(uuid4())) is None
    row = change(releases, row, "start", rollout_percentage=5)
    row = change(releases, row, "rollout", rollout_percentage=20)
    row = change(releases, row, "rollout", rollout_percentage=50)
    row = change(releases, row, "publish")
    assert releases.check("0.3.0", str(uuid4()))["version"] == "0.3.1"
    row = change(releases, row, "pause")
    assert releases.check("0.3.0", str(uuid4())) is None
    with pytest.raises(HTTPException):
        change(releases, row, "resume", rollout_percentage=5)
    row = change(releases, row, "resume")
    row = change(releases, row, "revoke")
    assert releases.ready(artifact())["status"] == "REVOKED"
    assert releases.check("0.3.0", str(uuid4())) is None
    with pytest.raises(HTTPException):
        change(releases, row, "publish")


def test_deterministic_rollout_and_increasing_cohort(releases):
    row = change(releases, releases.ready(artifact()), "start", rollout_percentage=50)
    ids = [str(uuid4()) for _ in range(100)]
    results = [bool(releases.check("0.3.0", i)) for i in ids]
    assert any(results) and not all(results)
    for _ in range(3):
        assert [bool(releases.check("0.3.0", i)) for i in ids] == results
    row = change(releases, row, "rollout", rollout_percentage=100)
    assert all(releases.check("0.3.0", i) for i in ids)
    change(releases, row, "pause")
    assert all(releases.check("0.3.0", i) is None for i in ids)


def test_conflict_confirmation_and_immutable_artifacts(releases):
    row = releases.ready(artifact())
    with pytest.raises(HTTPException):
        releases.change("0.3.1", Action(action="publish", revision=row["revision"], confirm_version="0.3.2", reason="test"), "admin")
    change(releases, row, "start")
    with pytest.raises(HTTPException):
        change(releases, row, "publish")
    with pytest.raises(HTTPException):
        releases.ready(artifact().model_copy(update={"sha256": "b" * 64}))


def test_semver_rollback_is_not_downgrade(releases):
    safe = change(releases, releases.ready(artifact("0.9.0")), "publish")
    latest = change(releases, releases.ready(artifact("0.10.0")), "publish")
    assert releases.check("0.9.0", str(uuid4()))["version"] == "0.10.0"
    lower = releases.ready(artifact("0.8.0"))
    with pytest.raises(HTTPException):
        change(releases, lower, "publish")
    change(releases, latest, "rollback", target_version=safe["version"])
    assert releases.check("0.10.0", str(uuid4())) is None
    assert releases.check("0.8.0", str(uuid4()))["version"] == "0.9.0"


def test_supported_range_and_mandatory(releases):
    row = releases.ready(artifact("0.5.0"))
    row = change(releases, row, "policy", minimum_supported_version="0.3.0", maximum_supported_version="0.4.8")
    change(releases, row, "publish")
    assert releases.check("0.2.1", str(uuid4()))["mandatory"] is True
    assert releases.check("0.4.8", str(uuid4()))["mandatory"] is False
    assert releases.check("0.4.9", str(uuid4())) is None


@pytest.mark.parametrize("value", ["abc", "0.3", "01.3.0", "0.3.0-beta.1", "0.3.0+build"])
def test_invalid_versions(value):
    with pytest.raises(ValueError):
        Draft(version=value)


@pytest.mark.parametrize("identifier", ["invalid", "00000000-0000-0000-0000-000000000000"])
def test_invalid_identifiers(identifier):
    with pytest.raises(ValueError):
        bucket(identifier, "0.3.1")


def test_storage_failure_cannot_create_ready(releases):
    def fail(data):
        raise ValueError("bad signature")
    releases.provider.verify = fail
    with pytest.raises(HTTPException):
        releases.ready(artifact())
    with releases.store.connection() as c:
        assert c.execute("SELECT count(*) FROM releases").fetchone()[0] == 0
