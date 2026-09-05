# 拾微模型接入（0.1.1）

设置页采用厂商、地域、模型和检索方式选择。地址自动填入；自定义服务地址与模型 ID 保留在高级入口。对话和语义检索可以独立使用不同厂商及密钥。

## 支持范围

| 厂商 | 对话协议 | 内置向量模型预设 | 官方文档 |
| --- | --- | --- | --- |
| DeepSeek | OpenAI 兼容 | 无，使用独立服务 | [API](https://api-docs.deepseek.com/) |
| 阿里云百炼 | OpenAI 兼容 | text-embedding-v4 / v3 | [地域与地址](https://help.aliyun.com/en/model-studio/base-url)、[向量](https://www.alibabacloud.com/help/en/model-studio/embedding) |
| 硅基流动 | OpenAI 兼容 | BAAI/bge-m3、Qwen3-Embedding-0.6B | [向量接口](https://docs.siliconflow.cn/cn/api-reference/embeddings/create-embeddings) |
| Kimi | OpenAI 兼容 | 无预设 | [对话接口](https://platform.kimi.com/docs/api/chat) |
| 智谱 GLM | OpenAI 兼容 | embedding-3 | [兼容接口](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)、[向量](https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3) |
| MiniMax | OpenAI 兼容 | 无预设 | [兼容接口](https://platform.minimaxi.com/docs/api-reference/text-chat-openai) |
| 火山方舟 | OpenAI 兼容 | 无预设 | [API](https://www.volcengine.com/docs/82379/1795150) |
| 百度千帆 | OpenAI 兼容 | 无预设 | [API](https://cloud.baidu.com/doc/qianfan/s/Smoghsq3g) |
| 腾讯混元（已有服务） | OpenAI 兼容 | 无预设 | [兼容接口](https://cloud.tencent.com/document/product/1729/111007) |
| OpenAI | Chat Completions | text-embedding-3-small / large | [模型](https://developers.openai.com/api/docs/models/all)、[模型列表](https://developers.openai.com/api/reference/resources/models/methods/list) |
| Anthropic Claude | 原生 Messages，含流式回答 | 无，使用独立服务 | [流式接口](https://platform.claude.com/docs/en/build-with-claude/streaming) |
| Google Gemini | 官方 OpenAI 兼容 | gemini-embedding-001 | [兼容接口](https://ai.google.dev/gemini-api/docs/openai) |
| 自定义 | OpenAI 兼容或 Anthropic Messages | 自定义 OpenAI 兼容向量接口 | HTTPS + API Key |

预设不是厂商当前全部模型，也不保证用户账户已获权限。支持从 `/models` 刷新；厂商没有列表接口时可使用预设或自定义 ID。不同地域的 Key 可能不能互换。火山方舟部分账户需使用 `ep-` 接入点。腾讯 TokenHub、Vertex AI、Bedrock 的专有认证不属于本次原生适配范围，可使用符合上述协议的自有代理。

## 数据与凭据

- Rust 将两个 API Key 分别保存在 Windows 凭据管理器，不回传已保存密钥给界面。
- 服务地址或协议改变时不能复用原对话密钥；向量地址改变也必须重新提供密钥。
- PDF 解析、OCR、全文索引均在本机运行，无需云模型与网络下载。
- 开启远程语义检索后，建立索引会向向量服务发送资料文本片段；回答时向对话服务发送检索到的相关片段。界面明确提示该区别。
- 更换对话模型不重建知识库。向量服务地址或模型改变时，旧向量不会混用；重建前回退到全文检索。

## 验证边界

厂商地址、认证、请求结构、流式解析、模型列表、错误处理及独立密钥隔离已用 MockTransport 做协议回归；这些测试没有向厂商发送请求。未提供各厂商真实 API Key，不能声称已逐个账户完成线上验证。桌面“测试连接”会真实测试对话及已启用的向量模型，只发送少量测试文本。
