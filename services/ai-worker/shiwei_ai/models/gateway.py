from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr, field_validator


class ModelGatewayError(RuntimeError):
    pass


class ProviderConfig(BaseModel):
    base_url: str = Field(min_length=8)
    api_key: SecretStr
    chat_model: str = ""
    embedding_model: str = ""
    protocol: Literal["openai_compatible", "anthropic"] = "openai_compatible"
    provider_id: str = "custom"
    embedding_mode: Literal["same", "separate", "none"] = "same"
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_provider_id: str = "custom"
    timeout_seconds: float = Field(default=60, ge=5, le=300)

    @field_validator("base_url", "embedding_base_url")
    @classmethod
    def validate_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型 Base URL 必须使用 HTTPS")
        return normalized


class EmbeddingResult(BaseModel):
    vectors: list[list[float]]
    model: str
    dimension: int


class ModelGateway(ABC):
    @abstractmethod
    def chat(self, messages: Sequence[dict[str, str]], **options: Any) -> str:
        raise NotImplementedError

    @abstractmethod
    def stream_chat(
        self, messages: Sequence[dict[str, str]], **options: Any
    ) -> Iterator[str]:
        raise NotImplementedError

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        raise NotImplementedError

    def rerank(self, query: str, documents: Sequence[str]) -> list[int]:
        del query, documents
        raise NotImplementedError("当前 Provider 不支持 rerank")


class OpenAICompatibleGateway(ModelGateway):
    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        if config.embedding_mode == "separate" and (not config.embedding_base_url or not config.embedding_api_key):
            raise ModelGatewayError("请配置独立的语义检索服务地址和 API Key")
        self.client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout_seconds,
            transport=transport,
        )
        self.embedding_client = self.client
        if config.embedding_mode == "separate":
            if not config.embedding_base_url or not config.embedding_api_key:
                raise ModelGatewayError("请配置独立的语义检索服务地址和 API Key")
            self.embedding_client = httpx.Client(
                base_url=config.embedding_base_url,
                headers={"Authorization": f"Bearer {config.embedding_api_key.get_secret_value()}", "Content-Type": "application/json"},
                timeout=config.timeout_seconds,
                transport=transport,
            )

    def close(self) -> None:
        self.client.close()
        if self.embedding_client is not self.client:
            self.embedding_client.close()

    def chat(self, messages: Sequence[dict[str, str]], **options: Any) -> str:
        payload = {
            "model": self.config.chat_model,
            "messages": list(messages),
            "stream": False,
            **options,
        }
        data = self._post_json("/chat/completions", payload)
        try:
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ModelGatewayError("模型没有返回文本内容，请检查所选模型是否支持对话")
            return content
        except (KeyError, IndexError, TypeError) as error:
            raise ModelGatewayError("模型响应缺少回答内容") from error

    def stream_chat(
        self, messages: Sequence[dict[str, str]], **options: Any
    ) -> Iterator[str]:
        payload = {
            "model": self.config.chat_model,
            "messages": list(messages),
            "stream": True,
            **options,
        }
        try:
            with self.client.stream("POST", "/chat/completions", json=payload) as response:
                self._raise_for_status(response)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        if "error" in chunk:
                            raise ModelGatewayError("AI 服务在生成过程中返回错误，请稍后重试")
                        content = chunk["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if content:
                        yield str(content)
        except httpx.TimeoutException as error:
            raise ModelGatewayError("AI 服务响应超时") from error
        except httpx.HTTPError as error:
            raise ModelGatewayError("无法连接 AI 服务") from error

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if self.config.embedding_mode == "none":
            raise ModelGatewayError("语义检索未启用，仍可使用本地全文搜索")
        if not texts:
            raise ValueError("Embedding 输入不能为空")
        payload = {"model": self.config.embedding_model, "input": list(texts)}
        data = self._post_json("/embeddings", payload, client=self.embedding_client)
        try:
            ordered = sorted(data["data"], key=lambda item: item["index"])
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as error:
            raise ModelGatewayError("Embedding 响应格式不正确") from error
        if len(vectors) != len(texts) or not vectors or not vectors[0]:
            raise ModelGatewayError("Embedding 响应数量或维度不正确")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise ModelGatewayError("Embedding 返回了不一致的向量维度")
        return EmbeddingResult(
            vectors=vectors,
            # Providers can resolve an alias to a dated model ID. Persist the same
            # identity used for subsequent requests so a valid index stays usable.
            model=self.config.embedding_model,
            dimension=dimension,
        )

    def test_connection(self) -> dict[str, Any]:
        if not self.config.chat_model or (self.config.embedding_mode != "none" and not self.config.embedding_model):
            raise ModelGatewayError("请选择模型后测试连接")
        self.chat([{"role": "user", "content": "请回复 OK。这是拾微连接测试。"}])
        result: dict[str, Any] = {"ok": True, "chatOk": True, "embeddingEnabled": self.config.embedding_mode != "none"}
        if self.config.embedding_mode != "none":
            embedding = self.embed(["拾微连接测试"])
            result.update(embeddingModel=embedding.model, embeddingDimension=embedding.dimension)
        return result

    def list_models(self, target: str = "chat") -> dict[str, Any]:
        client = self.embedding_client if target == "embedding" else self.client
        try:
            response = client.get("/models")
            self._raise_for_status(response)
            data = response.json()
            if not isinstance(data, dict):
                raise ModelGatewayError("厂商模型列表格式不正确，请使用预设或自定义模型 ID")
            models = sorted({str(item["id"]) for item in data.get("data", []) if isinstance(item, dict) and item.get("id")})
            if not models:
                raise ModelGatewayError("厂商没有返回模型列表，请使用预设或自定义模型 ID")
            return {"models": models}
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ModelGatewayError("无法读取厂商模型列表，请使用预设或自定义模型 ID") from error

    def _post_json(self, path: str, payload: dict[str, Any], *, client: httpx.Client | None = None) -> dict[str, Any]:
        try:
            response = (client or self.client).post(path, json=payload)
            self._raise_for_status(response)
            data = response.json()
        except httpx.TimeoutException as error:
            raise ModelGatewayError("AI 服务响应超时") from error
        except httpx.HTTPError as error:
            raise ModelGatewayError("无法连接 AI 服务") from error
        except json.JSONDecodeError as error:
            raise ModelGatewayError("AI 服务返回了无效 JSON") from error
        if not isinstance(data, dict):
            raise ModelGatewayError("AI 服务响应格式不正确")
        return data

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise ModelGatewayError("API Key 无效或没有权限")
        if response.status_code == 429:
            raise ModelGatewayError("AI 服务额度不足或请求过于频繁")
        if response.status_code in {400, 404, 422}:
            raise ModelGatewayError(f"模型或接口不可用（{response.status_code}），请检查模型 ID、服务地址和账户权限")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ModelGatewayError(f"AI 服务返回错误（{response.status_code}）") from error


class AnthropicGateway(OpenAICompatibleGateway):
    """Native Messages API for Claude; embeddings may use another provider."""

    def __init__(self, config: ProviderConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        if config.embedding_mode == "same":
            raise ModelGatewayError("Anthropic 不提供同接口 Embedding，请选择独立厂商或暂不启用")
        super().__init__(config, transport=transport)
        self.client.headers.pop("Authorization", None)
        self.client.headers.update({"x-api-key": config.api_key.get_secret_value(), "anthropic-version": "2023-06-01"})

    def _payload(self, messages: Sequence[dict[str, str]], stream: bool) -> dict[str, Any]:
        return {
            "model": self.config.chat_model,
            "system": "\n\n".join(item["content"] for item in messages if item["role"] == "system"),
            "messages": [item for item in messages if item["role"] != "system"],
            "max_tokens": 4096,
            "stream": stream,
        }

    def chat(self, messages: Sequence[dict[str, str]], **options: Any) -> str:
        data = self._post_json("/messages", self._payload(messages, False))
        text = "".join(item.get("text", "") for item in data.get("content", []) if item.get("type") == "text")
        if not text:
            raise ModelGatewayError("模型没有返回文本内容")
        return text

    def stream_chat(self, messages: Sequence[dict[str, str]], **options: Any) -> Iterator[str]:
        try:
            with self.client.stream("POST", "/messages", json=self._payload(messages, True)) as response:
                self._raise_for_status(response)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "error":
                        raise ModelGatewayError("Claude 流式请求中断，请稍后重试")
                    if data.get("type") == "content_block_delta" and data.get("delta", {}).get("type") == "text_delta":
                        yield data["delta"]["text"]
        except httpx.TimeoutException as error:
            raise ModelGatewayError("AI 服务响应超时") from error
        except httpx.HTTPError as error:
            raise ModelGatewayError("无法连接 AI 服务") from error


def create_gateway(config: ProviderConfig, *, transport: httpx.BaseTransport | None = None) -> OpenAICompatibleGateway:
    cls = AnthropicGateway if config.protocol == "anthropic" else OpenAICompatibleGateway
    return cls(config, transport=transport)
