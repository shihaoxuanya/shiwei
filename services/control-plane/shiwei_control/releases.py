import hashlib
from uuid import UUID, uuid4
from fastapi import HTTPException
from .models import Action, Artifact, Draft, version
from .storage import now


def bucket(installation_id: str, release_version: str) -> int:
    identifier = UUID(installation_id)
    if identifier.version != 4:
        raise ValueError("installation_id 必须为 UUID v4")
    value = hashlib.sha256(f"{identifier}:{release_version}".encode()).digest()
    return int.from_bytes(value[:8], "big") % 100


def find(c, v):
    row = c.execute("SELECT * FROM releases WHERE version=? AND channel='stable'", (v,)).fetchone()
    if row is None:
        raise HTTPException(404, "版本不存在")
    return dict(row)


def policy(row):
    keys = ("version", "status", "rollout_percentage", "mandatory", "minimum_supported_version", "maximum_supported_version", "revision")
    return {key: row[key] for key in keys}


class Releases:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider

    def draft(self, data: Draft, actor):
        with self.store.connection(write=True) as c:
            if c.execute("SELECT 1 FROM releases WHERE version=? AND channel=?", (data.version, data.channel)).fetchone():
                raise HTTPException(409, "版本已存在")
            stamp = now()
            c.execute("INSERT INTO releases(id,version,channel,status,release_notes,created_at,updated_at,created_by) VALUES(?,?,?,'DRAFT',?,?,?,?)", (str(uuid4()), data.version, data.channel, data.release_notes, stamp, stamp, actor))
            self.store.audit(c, actor, "draft", data.version, None, {"status": "DRAFT"}, "创建待构建版本")
            return find(c, data.version)

    def ready(self, data: Artifact):
        expected = {"artifact_url": data.artifact_url, "signature": data.signature, "artifact_sha256": data.sha256, "artifact_size": data.size, "release_notes": data.release_notes}
        with self.store.connection() as c:
            existing = c.execute("SELECT * FROM releases WHERE version=? AND channel=?", (data.version, data.channel)).fetchone()
            if existing and existing["status"] != "DRAFT":
                if all(existing[k] == v for k, v in expected.items()):
                    return dict(existing)  # Retry never reopens a paused/revoked version.
                raise HTTPException(409, "已接收版本的 Artifact 不可覆盖，请使用新版本号")
        try:
            self.provider.verify(data)
        except Exception:
            raise HTTPException(422, "Artifact 校验失败：请核对下载地址、大小、SHA256 与官方签名") from None
        with self.store.connection(write=True) as c:
            existing = c.execute("SELECT * FROM releases WHERE version=? AND channel=?", (data.version, data.channel)).fetchone()
            if existing and existing["status"] != "DRAFT":
                if all(existing[k] == v for k, v in expected.items()):
                    return dict(existing)
                raise HTTPException(409, "并发提交的版本内容不同")
            stamp = now()
            if not existing:
                c.execute("INSERT INTO releases(id,version,channel,status,release_notes,created_at,updated_at,created_by) VALUES(?,?,?,'DRAFT',?,?,?,'ci')", (str(uuid4()), data.version, data.channel, data.release_notes, stamp, stamp))
            c.execute("UPDATE releases SET status='READY',artifact_url=?,signature=?,artifact_sha256=?,artifact_size=?,release_notes=?,verified_at=?,updated_at=?,revision=revision+1 WHERE version=? AND channel=?", (data.artifact_url, data.signature, data.sha256, data.size, data.release_notes, stamp, stamp, data.version, data.channel))
            self.store.audit(c, "ci", "artifact_ready", data.version, {"status": "DRAFT"}, {"status": "READY", "sha256": data.sha256}, "已验证签名资源")
            return find(c, data.version)

    def change(self, v: str, data: Action, actor: str):
        with self.store.connection(write=True) as c:
            row = find(c, v)
            if data.confirm_version != v or data.revision != row["revision"]:
                raise HTTPException(409, "版本确认或修订号不匹配，请刷新后重试")
            status, action = row["status"], data.action
            allowed = {"start": {"READY"}, "rollout": {"ROLLOUT"}, "publish": {"READY", "ROLLOUT"}, "pause": {"ROLLOUT", "PUBLISHED"}, "resume": {"PAUSED"}, "revoke": {"READY", "ROLLOUT", "PUBLISHED", "PAUSED"}, "rollback": {"ROLLOUT", "PUBLISHED", "PAUSED", "REVOKED"}, "policy": {"READY", "ROLLOUT", "PUBLISHED", "PAUSED"}}
            if status not in allowed[action]:
                raise HTTPException(409, "当前状态不允许此操作")
            before = policy(row)
            target = c.execute("SELECT target_id FROM channel_targets WHERE channel='stable'").fetchone()
            if action in {"start", "publish", "resume"}:
                highest = [version(r[0]) for r in c.execute("SELECT version FROM releases WHERE published_at IS NOT NULL AND version<>?", (v,))]
                if highest and version(v) < max(highest):
                    raise HTTPException(409, "普通发布不能降低 Stable 版本，请使用回滚发布目标")
                if target and target[0] != row["id"]:
                    previous = c.execute("SELECT status FROM releases WHERE id=?", (target[0],)).fetchone()
                    if previous and previous[0] == "ROLLOUT":
                        raise HTTPException(409, "请先暂停或结束当前灰度版本")
                if not row["verified_at"]:
                    raise HTTPException(409, "Artifact 尚未校验")
                percent = 100 if action == "publish" else (data.rollout_percentage or row["rollout_percentage"] or 5)
                if action == "resume" and percent < row["rollout_percentage"]:
                    raise HTTPException(409, "恢复发布不能缩小已确认的灰度范围")
                row.update(status="PUBLISHED" if percent == 100 else "ROLLOUT", rollout_percentage=percent, published_at=row["published_at"] or now())
                c.execute("INSERT INTO channel_targets VALUES('stable',?) ON CONFLICT(channel) DO UPDATE SET target_id=excluded.target_id", (row["id"],))
            elif action == "rollout":
                if not target or target[0] != row["id"] or data.rollout_percentage is None or data.rollout_percentage < row["rollout_percentage"]:
                    raise HTTPException(409, "灰度比例只能扩大；需停止时请暂停")
                row["rollout_percentage"] = data.rollout_percentage
                row["status"] = "PUBLISHED" if data.rollout_percentage == 100 else "ROLLOUT"
            elif action == "pause":
                row["status"] = "PAUSED"
            elif action == "revoke":
                row["status"] = "REVOKED"
            elif action == "rollback":
                if not data.target_version or not target or target[0] != row["id"]:
                    raise HTTPException(409, "只能回滚当前发布目标，且必须指定安全版本")
                safe = find(c, data.target_version)
                if safe["id"] == row["id"] or safe["status"] != "PUBLISHED" or not safe["verified_at"] or version(safe["version"]) >= version(v):
                    raise HTTPException(409, "回滚目标必须是较低的已全量发布且未撤销的安全版本")
                row["status"] = "REVOKED"
                c.execute("UPDATE channel_targets SET target_id=? WHERE channel='stable'", (safe["id"],))
            elif action == "policy":
                for bound in (data.minimum_supported_version, data.maximum_supported_version):
                    if bound and version(bound) >= version(v):
                        raise HTTPException(422, "兼容版本边界必须低于目标版本")
                row.update(mandatory=int(data.mandatory), minimum_supported_version=data.minimum_supported_version, maximum_supported_version=data.maximum_supported_version)
            c.execute("UPDATE releases SET status=?,rollout_percentage=?,published_at=?,mandatory=?,minimum_supported_version=?,maximum_supported_version=?,revision=revision+1,updated_at=? WHERE id=?", (row["status"], row["rollout_percentage"], row["published_at"], row["mandatory"], row["minimum_supported_version"], row["maximum_supported_version"], now(), row["id"]))
            after = find(c, v)
            self.store.audit(c, actor, action, v, before, {**policy(after), "target_version": data.target_version}, data.reason)
            return after

    def check(self, current: str, installation: str):
        client = version(current)
        bucket(installation, current)  # Validate even when no target exists.
        with self.store.connection() as c:
            row = c.execute("SELECT r.* FROM releases r JOIN channel_targets t ON t.target_id=r.id WHERE t.channel='stable'").fetchone()
            if not row or row["status"] not in ("ROLLOUT", "PUBLISHED") or not row["verified_at"] or version(row["version"]) <= client:
                return None
            if row["maximum_supported_version"] and client > version(row["maximum_supported_version"]):
                return None
            if bucket(installation, row["version"]) >= row["rollout_percentage"]:
                return None
            mandatory = bool(row["mandatory"]) or bool(row["minimum_supported_version"] and client < version(row["minimum_supported_version"]))
            # Official Tauri dynamic manifest + additive release policy; 204 means no update.
            return {"version": row["version"], "notes": row["release_notes"], "pub_date": row["published_at"], "url": row["artifact_url"], "signature": row["signature"], "mandatory": mandatory, "sha256": row["artifact_sha256"]}
