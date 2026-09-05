from __future__ import annotations

import json

import httpx
import pytest

from shiwei_ai.models import OpenAICompatibleGateway, ProviderConfig
from shiwei_ai.models import create_gateway, ModelGatewayError


def test_openai_compatible_embedding_keeps_provider_behind_gateway() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer secret-value"
        body = json.loads(request.content)
        assert body["model"] == "text-embedding-test"
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-test",
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                ],
            },
        )

    gateway = OpenAICompatibleGateway(
        ProviderConfig(
            base_url="https://models.example.com/v1",
            api_key="secret-value",
            chat_model="chat-test",
            embedding_model="text-embedding-test",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = gateway.embed(["第一段", "第二段"])
    gateway.close()

    assert result.vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert result.dimension == 3


def test_provider_requires_https() -> None:
    try:
        ProviderConfig(
            base_url="http://models.example.com/v1",
            api_key="secret",
            chat_model="chat",
            embedding_model="embed",
        )
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("insecure provider URL should be rejected")


@pytest.mark.parametrize("base_url", [
    "https://api.deepseek.com/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://api.siliconflow.cn/v1", "https://api.moonshot.cn/v1",
    "https://open.bigmodel.cn/api/paas/v4", "https://api.minimaxi.com/v1",
    "https://ark.cn-beijing.volces.com/api/v3", "https://qianfan.baidubce.com/v2",
    "https://api.hunyuan.cloud.tencent.com/v1", "https://api.openai.com/v1",
    "https://generativelanguage.googleapis.com/v1beta/openai",
])
def test_compatible_vendors_keep_base_path_and_support_streaming(base_url):
    def handler(request):
        assert str(request.url) == base_url + "/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        assert body["model"] == "selected-model"
        assert "temperature" not in body
        if body["stream"]:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    gateway = create_gateway(ProviderConfig(base_url=base_url, api_key="test-only", chat_model="selected-model", embedding_mode="none"), transport=httpx.MockTransport(handler))
    assert gateway.chat([{"role": "user", "content": "test"}]) == "OK"
    assert "".join(gateway.stream_chat([{"role": "user", "content": "test"}])) == "OK"
    assert gateway.test_connection()["embeddingEnabled"] is False
    gateway.close()


def test_claude_native_protocol_and_separate_embedding_credentials():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        body = json.loads(request.content) if request.method == "POST" else {}
        if request.url.host == "api.anthropic.com":
            assert request.headers["x-api-key"] == "claude-key"
            assert "authorization" not in request.headers
            assert request.headers["anthropic-version"] == "2023-06-01"
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "claude-model"}]})
            assert request.url.path == "/v1/messages"
            assert body["system"] == "trusted instructions"
            assert all(message["role"] != "system" for message in body["messages"])
            if body["stream"]:
                return httpx.Response(200, text='event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"回答"}}\n\n')
            return httpx.Response(200, json={"content": [{"type": "text", "text": "回答"}]})
        assert request.headers["authorization"] == "Bearer embed-key"
        assert "x-api-key" not in request.headers
        assert request.url.path == "/compatible-mode/v1/embeddings"
        return httpx.Response(200, json={"model": "resolved-dated-model", "data": [{"index": 0, "embedding": [1, 0]}]})
    gateway = create_gateway(ProviderConfig(base_url="https://api.anthropic.com/v1", api_key="claude-key", chat_model="claude-model", protocol="anthropic", embedding_mode="separate", embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", embedding_api_key="embed-key", embedding_model="embedding-alias"), transport=httpx.MockTransport(handler))
    messages = [{"role": "system", "content": "trusted instructions"}, {"role": "user", "content": "test"}]
    assert gateway.chat(messages) == "回答"
    assert "".join(gateway.stream_chat(messages)) == "回答"
    assert gateway.embed(["test"]).model == "embedding-alias"
    assert gateway.list_models()["models"] == ["claude-model"]
    assert len(calls) == 4
    gateway.close()


@pytest.mark.parametrize("status, message", [(401, "API Key"), (429, "额度"), (404, "模型或接口")])
def test_provider_errors_are_actionable_without_echoing_content(status, message):
    gateway = create_gateway(ProviderConfig(base_url="https://example.com/v1", api_key="private-key", chat_model="test", embedding_mode="none"), transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "private-key private-content"})))
    with pytest.raises(ModelGatewayError, match=message) as failure:
        gateway.test_connection()
    assert "private" not in str(failure.value)
    gateway.close()


def test_timeout_and_no_embedding_mode():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)
    gateway = create_gateway(ProviderConfig(base_url="https://example.com/v1", api_key="test", chat_model="test", embedding_mode="none"), transport=httpx.MockTransport(handler))
    with pytest.raises(ModelGatewayError, match="超时"):
        gateway.test_connection()
    with pytest.raises(ModelGatewayError, match="未启用"):
        gateway.embed(["must not upload"])
    gateway.close()
