from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "1.0"


class RpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    id: str = Field(min_length=1, max_length=128)
    method: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)


class RpcError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class RpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    id: str | None
    result: dict[str, Any] | None = None
    error: RpcError | None = None

    @classmethod
    def success(cls, request_id: str, result: dict[str, Any]) -> "RpcResponse":
        return cls(id=request_id, result=result)

    @classmethod
    def failure(
        cls,
        request_id: str | None,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> "RpcResponse":
        return cls(id=request_id, error=RpcError(code=code, message=message, details=details))
