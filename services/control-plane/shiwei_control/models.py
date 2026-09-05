import base64
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from semver import Version


def version(value: str) -> Version:
    parsed = Version.parse(value)
    if parsed.prerelease or parsed.build:
        raise ValueError("当前只支持 MAJOR.MINOR.PATCH 稳定版本")
    return parsed


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Draft(Input):
    version: str = Field(max_length=64)
    channel: Literal["stable"] = "stable"
    release_notes: str = Field(default="", max_length=8000)

    @field_validator("version")
    @classmethod
    def valid_version(cls, value):
        version(value)
        return value


class Artifact(Draft):
    artifact_url: str = Field(max_length=2048)
    signature: str = Field(max_length=2048)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=1024, le=1_073_741_824)

    @field_validator("signature")
    @classmethod
    def official_signature(cls, value):
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
            if not decoded.startswith("untrusted comment:"):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValueError("需要官方 Updater signature") from None
        return value


class Action(Input):
    action: Literal["start", "rollout", "publish", "pause", "resume", "revoke", "rollback", "policy"]
    revision: int = Field(ge=0)
    confirm_version: str
    reason: str = Field(min_length=1, max_length=500)
    rollout_percentage: int | None = Field(default=None, ge=1, le=100, strict=True)
    mandatory: bool = False
    minimum_supported_version: str | None = Field(default=None, max_length=64)
    maximum_supported_version: str | None = Field(default=None, max_length=64)
    target_version: str | None = Field(default=None, max_length=64)

    @field_validator("minimum_supported_version", "maximum_supported_version", "target_version")
    @classmethod
    def valid_optional(cls, value):
        if value is not None:
            version(value)
        return value

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum_supported_version and self.maximum_supported_version and version(self.minimum_supported_version) > version(self.maximum_supported_version):
            raise ValueError("最低支持版本不能高于最高适用版本")
        return self


class Login(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式不正确")
        return value.lower()
