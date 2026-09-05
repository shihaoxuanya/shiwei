import type { ProviderInput } from "./provider";

export type ProviderPreset = {
  id: string;
  name: string;
  protocol: ProviderInput["protocol"];
  endpoints: { name: string; url: string }[];
  chatModels: string[];
  embeddingModels: string[];
  note?: string;
};

// Presets are suggestions, not an account entitlement list. /models and custom IDs
// keep this usable when vendors add models or require a deployment ID.
export const providerPresets: ProviderPreset[] = [
  { id: "deepseek", name: "DeepSeek", protocol: "openai_compatible", endpoints: [{ name: "官方服务", url: "https://api.deepseek.com/v1" }], chatModels: ["deepseek-v4-flash", "deepseek-v4-pro"], embeddingModels: [], note: "对话服务不提供 Embedding，可单独选择其他厂商提供语义检索。" },
  { id: "qwen", name: "阿里云百炼 · 通义千问", protocol: "openai_compatible", endpoints: [{ name: "中国内地 · 北京", url: "https://dashscope.aliyuncs.com/compatible-mode/v1" }, { name: "国际 · 新加坡", url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" }, { name: "美国", url: "https://dashscope-us.aliyuncs.com/compatible-mode/v1" }], chatModels: ["qwen-plus", "qwen-turbo", "qwen-max"], embeddingModels: ["text-embedding-v4", "text-embedding-v3"], note: "API Key 必须与所选地域一致；专属工作空间地址可在高级设置中修改。" },
  { id: "siliconflow", name: "硅基流动", protocol: "openai_compatible", endpoints: [{ name: "中国服务", url: "https://api.siliconflow.cn/v1" }], chatModels: ["deepseek-ai/DeepSeek-V4-Flash"], embeddingModels: ["BAAI/bge-m3", "Qwen/Qwen3-Embedding-0.6B"] },
  { id: "kimi", name: "月之暗面 · Kimi", protocol: "openai_compatible", endpoints: [{ name: "中国服务", url: "https://api.moonshot.cn/v1" }, { name: "国际服务", url: "https://api.moonshot.ai/v1" }], chatModels: ["kimi-k2.6"], embeddingModels: [] },
  { id: "zhipu", name: "智谱 · GLM", protocol: "openai_compatible", endpoints: [{ name: "开放平台", url: "https://open.bigmodel.cn/api/paas/v4" }], chatModels: ["glm-5.3"], embeddingModels: ["embedding-3"] },
  { id: "minimax", name: "MiniMax", protocol: "openai_compatible", endpoints: [{ name: "中国服务", url: "https://api.minimaxi.com/v1" }, { name: "国际服务", url: "https://api.minimax.io/v1" }], chatModels: ["MiniMax-M2.7", "MiniMax-M2.7-highspeed"], embeddingModels: [] },
  { id: "volcengine", name: "火山方舟 · 豆包", protocol: "openai_compatible", endpoints: [{ name: "北京", url: "https://ark.cn-beijing.volces.com/api/v3" }], chatModels: ["doubao-seed-2-0-lite-260215"], embeddingModels: [], note: "部分账户需要控制台中的推理接入点 ID（ep- 开头），请选择自定义模型 ID。" },
  { id: "baidu", name: "百度千帆 · 文心", protocol: "openai_compatible", endpoints: [{ name: "千帆 v2", url: "https://qianfan.baidubce.com/v2" }], chatModels: ["ernie-4.5-turbo-20260402"], embeddingModels: [] },
  { id: "hunyuan", name: "腾讯混元（已有服务）", protocol: "openai_compatible", endpoints: [{ name: "混元兼容接口", url: "https://api.hunyuan.cloud.tencent.com/v1" }], chatModels: [], embeddingModels: [], note: "适用于已有混元 API 服务。TokenHub 等不同服务请用自定义地址与对应模型 ID。" },
  { id: "openai", name: "OpenAI", protocol: "openai_compatible", endpoints: [{ name: "官方服务", url: "https://api.openai.com/v1" }], chatModels: ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"], embeddingModels: ["text-embedding-3-small", "text-embedding-3-large"] },
  { id: "anthropic", name: "Anthropic · Claude", protocol: "anthropic", endpoints: [{ name: "官方服务", url: "https://api.anthropic.com/v1" }], chatModels: ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"], embeddingModels: [], note: "使用 Claude 原生 Messages 协议；语义检索需配置其他厂商。" },
  { id: "gemini", name: "Google · Gemini", protocol: "openai_compatible", endpoints: [{ name: "Gemini API", url: "https://generativelanguage.googleapis.com/v1beta/openai" }], chatModels: ["gemini-3.8-flash"], embeddingModels: ["gemini-embedding-001"], note: "使用 Gemini 官方 OpenAI 兼容接口，不是 Vertex AI。" },
  { id: "custom", name: "自定义 / 兼容服务", protocol: "openai_compatible", endpoints: [], chatModels: [], embeddingModels: [], note: "支持 HTTPS OpenAI 兼容接口或 Claude Messages 接口。云平台专有签名认证暂不支持。" },
];

export const presetFor = (id: string) => providerPresets.find((preset) => preset.id === id) ?? providerPresets.at(-1)!;
export const inferProvider = (url: string) => providerPresets.find((preset) => preset.endpoints.some((endpoint) => endpoint.url.replace(/\/$/, "") === url.replace(/\/$/, "")))?.id ?? "custom";
export const defaultProvider: ProviderInput = {
  providerId: "deepseek", protocol: "openai_compatible", baseUrl: presetFor("deepseek").endpoints[0].url,
  apiKey: "", chatModel: presetFor("deepseek").chatModels[0], embeddingMode: "none",
  embeddingProviderId: "qwen", embeddingBaseUrl: presetFor("qwen").endpoints[0].url,
  embeddingApiKey: "", embeddingModel: presetFor("qwen").embeddingModels[0],
};

export function changeChatProvider(current: ProviderInput, id: string): ProviderInput {
  const preset = presetFor(id);
  const same = current.embeddingMode === "same";
  return { ...current, providerId: id, protocol: preset.protocol, baseUrl: preset.endpoints[0]?.url ?? "", apiKey: "", chatModel: preset.chatModels[0] ?? "",
    embeddingMode: same && (preset.protocol === "anthropic" || (!preset.embeddingModels.length && id !== "custom")) ? "none" : current.embeddingMode,
    embeddingModel: same ? preset.embeddingModels[0] ?? "" : current.embeddingModel,
  };
}
