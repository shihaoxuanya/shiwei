import { useEffect, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Database, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import { Button } from "./ui/button";
import { ReleaseSettings } from "./ReleaseInfrastructure";
import { cn } from "../lib/cn";
import { changeChatProvider, defaultProvider, inferProvider, presetFor, providerPresets, type ProviderPreset } from "../lib/provider-presets";
import { fetchProviderModels, getIndexStatus, getProviderStatus, rebuildEmbeddings, saveProvider, testProvider, type IndexStatus, type ProviderInput, type ProviderStatus } from "../lib/provider";

const field = "mt-2 w-full min-w-0 rounded-lg border border-line bg-white px-3 py-2.5 text-sm text-ink outline-none focus:border-indigo focus:ring-2 focus:ring-indigo/15 disabled:opacity-50";

function ModelSelect({ label, value, models, onChange, onRefresh, busy }: { label: string; value: string; models: string[]; onChange: (value: string) => void; onRefresh: () => void; busy: boolean }) {
  const [custom, setCustom] = useState(false);
  const choices = [...new Set(models)];
  const isCustom = custom || (!!value && !choices.includes(value));
  return <div className="min-w-0">
    <div className="flex items-center justify-between gap-3">
      <label htmlFor={`${label}-select`} className="text-sm text-ink">{label}</label>
      <button type="button" className="flex items-center gap-1.5 text-xs text-indigo hover:underline disabled:opacity-50" onClick={onRefresh} disabled={busy} aria-label={`刷新${label}列表`}><RefreshCw className="size-3" />从服务商刷新</button>
    </div>
    <select id={`${label}-select`} className={field} value={isCustom ? "__custom__" : value} onChange={(event) => { const next = event.target.value; setCustom(next === "__custom__"); onChange(next === "__custom__" ? "" : next); }}>
      <option value="" disabled>请选择模型</option>
      {choices.map((model) => <option key={model} value={model}>{model}</option>)}
      <option value="__custom__">自定义模型 ID…</option>
    </select>
    {isCustom && <input aria-label={`自定义${label} ID`} className={field} value={value} onChange={(event) => onChange(event.target.value)} placeholder="粘贴服务商控制台中的模型或接入点 ID" />}
  </div>;
}

function Endpoint({ preset, value, onChange }: { preset: ProviderPreset; value: string; onChange: (value: string) => void }) {
  return <>
    {preset.endpoints.length > 1 && <label className="block text-sm text-ink">服务地域
      <select className={field} value={preset.endpoints.some((endpoint) => endpoint.url === value) ? value : "custom"} onChange={(event) => { if (event.target.value !== "custom") onChange(event.target.value); }}>
        {preset.endpoints.map((endpoint) => <option key={endpoint.url} value={endpoint.url}>{endpoint.name}</option>)}
        {!preset.endpoints.some((endpoint) => endpoint.url === value) && <option value="custom">自定义地址</option>}
      </select>
    </label>}
    <details className="text-xs text-muted" open={preset.id === "custom" ? true : undefined}>
      <summary className="cursor-pointer py-1 hover:text-indigo">高级设置 · 服务地址</summary>
      <label className="mt-2 block">HTTPS 服务地址
        <input className={field} type="url" value={value} onChange={(event) => onChange(event.target.value.trim())} placeholder="https://api.example.com/v1" spellCheck={false} />
      </label>
      <p className="mt-2 leading-5">地址自动填入；只有使用代理、专属工作空间或不同接入点时才需要修改。切换地址后需重新填写密钥。</p>
    </details>
  </>;
}

export function SettingsPage() {
  const [provider, setProvider] = useState<ProviderInput>(defaultProvider);
  const [saved, setSaved] = useState<ProviderStatus | null>(null);
  const [indexInfo, setIndexInfo] = useState<IndexStatus>({ chunkCount: 0 });
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [remoteModels, setRemoteModels] = useState<{ chat: string[]; embedding: string[] }>({ chat: [], embedding: [] });
  const desktop = isTauri();
  const chatPreset = presetFor(provider.providerId);
  const embeddingPreset = provider.embeddingMode === "same" ? chatPreset : presetFor(provider.embeddingProviderId);
  const canReuseChatKey = saved?.hasApiKey && saved.baseUrl === provider.baseUrl && (saved.protocol ?? "openai_compatible") === provider.protocol;
  const canReuseEmbeddingKey = saved?.hasEmbeddingApiKey && saved.embeddingBaseUrl === provider.embeddingBaseUrl;
  const ready = !!provider.chatModel.trim() && !!provider.baseUrl && !!(provider.apiKey?.trim() || canReuseChatKey)
    && (provider.embeddingMode === "none" || (!!provider.embeddingModel.trim() && (provider.embeddingMode === "same" || (!!provider.embeddingBaseUrl && !!(provider.embeddingApiKey?.trim() || canReuseEmbeddingKey)))));

  useEffect(() => {
    void Promise.all([getProviderStatus(), getIndexStatus()]).then(([status, index]) => {
      setSaved(status); setIndexInfo(index);
      if (status.configured) setProvider({ ...defaultProvider, ...status, providerId: status.providerId === "custom" || !status.providerId ? inferProvider(status.baseUrl ?? "") : status.providerId,
        embeddingMode: status.embeddingMode ?? "same", apiKey: "", embeddingApiKey: "" });
    }).catch((error: unknown) => setNotice({ kind: "error", text: String(error) }));
  }, []);

  const update = (values: Partial<ProviderInput>) => { setProvider((current) => ({ ...current, ...values })); setNotice(null); };
  const run = async (action: "test" | "save" | "index") => {
    setBusy(action); setNotice(null);
    try {
      if (action === "test") {
        const result = await testProvider(provider);
        setNotice({ kind: "success", text: result.embeddingEnabled ? `对话与语义检索连接成功，向量维度 ${result.embeddingDimension}` : "对话连接成功；当前使用本地全文搜索" });
      } else {
        // Always persist the form before indexing, including model-only changes.
        await saveProvider(provider);
        setSaved(await getProviderStatus());
        setProvider((current) => ({ ...current, apiKey: "", embeddingApiKey: "" }));
        if (action === "index") {
          const result = await rebuildEmbeddings();
          setIndexInfo(await getIndexStatus());
          setNotice({ kind: "success", text: `语义索引已建立，共处理 ${result.indexed} 个知识片段` });
        } else setNotice({ kind: "success", text: "模型配置已安全保存。更换对话模型不会重建知识库。" });
      }
    } catch (error) { setNotice({ kind: "error", text: error instanceof Error ? error.message : String(error) }); }
    finally { setBusy(null); }
  };

  const refresh = async (target: "chat" | "embedding") => {
    if (!desktop) { setNotice({ kind: "error", text: "浏览器仅用于预览，请在拾微桌面应用中连接模型服务。" }); return; }
    setBusy(`models-${target}`); setNotice(null);
    try {
      // Discover chat models without requiring an unrelated embedding key.
      const input = target === "chat" ? { ...provider, embeddingMode: "none" as const } : provider;
      const models = await fetchProviderModels(input, target);
      setRemoteModels((current) => ({ ...current, [target]: models }));
      setNotice({ kind: "success", text: models.length ? `已读取 ${models.length} 个可见模型。请按用途选择，列表可能同时包含对话、向量等模型。` : "服务商未返回模型列表，可继续选择预设或填写自定义 ID。" });
    } catch (error) { setNotice({ kind: "error", text: `${String(error)}。仍可选择预设或填写自定义模型 ID。` }); }
    finally { setBusy(null); }
  };

  return <section className="mx-auto max-w-4xl px-6 py-10 lg:px-10 lg:py-12">
    <p className="mb-3 text-xs font-semibold tracking-[0.2em] text-indigo">本地与模型</p>
    <div className="flex items-center justify-between gap-4"><h1 className="font-serif text-4xl font-semibold text-ink">设置</h1><span className={cn("rounded-full px-3 py-1 text-xs", saved?.configured ? "bg-emerald-50 text-emerald-700" : "bg-[#efedf6] text-muted")}>{saved?.configured ? "配置已保存" : "未配置模型"}</span></div>
    <p className="mt-3 text-sm leading-6 text-muted">选择你已有的模型服务。地址自动填好，只需选择模型并填写 API Key。</p>
    {!desktop && <p className="mt-4 rounded-lg bg-[#efeff7] px-4 py-3 text-xs leading-5 text-indigo">当前为浏览器预览，表单可体验；连接、保存与索引在桌面应用中执行。</p>}

    <form className="mt-7 space-y-5" onSubmit={(event) => event.preventDefault()}>
      <fieldset disabled={busy !== null} className="min-w-0 rounded-2xl border border-line bg-panel p-6 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold"><KeyRound className="size-4 text-indigo" />对话模型</div>
        <p className="mt-2 text-xs leading-5 text-muted">用来理解问题、根据找到的记录组织回答。</p>
        <div className="mt-5 grid gap-5 md:grid-cols-2">
          <label className="text-sm">模型厂商<select className={field} value={provider.providerId} onChange={(event) => { setProvider((current) => changeChatProvider(current, event.target.value)); setRemoteModels({ chat: [], embedding: [] }); setNotice(null); }}>{providerPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
          <ModelSelect key={`chat-${provider.providerId}`} label="对话模型" value={provider.chatModel} models={[...chatPreset.chatModels, ...remoteModels.chat]} onChange={(chatModel) => update({ chatModel })} onRefresh={() => void refresh("chat")} busy={!!busy} />
          <label className="text-sm md:col-span-2">对话 API Key<input type="password" autoComplete="off" spellCheck={false} className={field} value={provider.apiKey ?? ""} onChange={(event) => update({ apiKey: event.target.value })} placeholder={canReuseChatKey ? "已安全保存；留空则继续使用" : "粘贴所选厂商的 API Key"} /></label>
          {provider.providerId === "custom" && <label className="text-sm md:col-span-2">接口协议<select className={field} value={provider.protocol} onChange={(event) => update({ protocol: event.target.value as ProviderInput["protocol"], apiKey: "", embeddingMode: event.target.value === "anthropic" && provider.embeddingMode === "same" ? "none" : provider.embeddingMode })}><option value="openai_compatible">OpenAI 兼容</option><option value="anthropic">Anthropic Messages</option></select></label>}
          <div className="space-y-3 md:col-span-2"><Endpoint preset={chatPreset} value={provider.baseUrl} onChange={(baseUrl) => { update({ baseUrl, apiKey: "" }); setRemoteModels((current) => ({ ...current, chat: [] })); }} />{chatPreset.note && <p className="text-xs leading-5 text-muted">{chatPreset.note}</p>}</div>
        </div>
      </fieldset>

      <fieldset disabled={busy !== null} className="min-w-0 rounded-2xl border border-line bg-panel p-6 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold"><Database className="size-4 text-indigo" />语义检索 <span className="font-normal text-muted">· 可选</span></div>
        <p className="mt-2 text-xs leading-5 text-muted">让不同表达也能找到相近内容。未启用时仍能导入资料、全文搜索和基于记录问答。</p>
        <label className="mt-5 block text-sm">检索方式<select className={field} value={provider.embeddingMode} onChange={(event) => {
          const mode = event.target.value as ProviderInput["embeddingMode"];
          update({ embeddingMode: mode, embeddingModel: mode === "same" ? chatPreset.embeddingModels[0] ?? "" : presetFor(provider.embeddingProviderId).embeddingModels[0] ?? "" });
          setRemoteModels((current) => ({ ...current, embedding: [] }));
        }}><option value="none">仅本地全文搜索（无需额外配置）</option>{provider.protocol !== "anthropic" && <option value="same">与对话使用同一服务</option>}<option value="separate">单独选择语义检索服务</option></select></label>
        {provider.embeddingMode !== "none" && <div className="mt-5 grid gap-5 md:grid-cols-2">
          {provider.embeddingMode === "separate" && <label className="text-sm">语义检索厂商<select className={field} value={provider.embeddingProviderId} onChange={(event) => { const preset = presetFor(event.target.value); update({ embeddingProviderId: preset.id, embeddingBaseUrl: preset.endpoints[0]?.url ?? "", embeddingModel: preset.embeddingModels[0] ?? "", embeddingApiKey: "" }); setRemoteModels((current) => ({ ...current, embedding: [] })); }}>{providerPresets.filter((preset) => preset.embeddingModels.length > 0 || preset.id === "custom" || preset.id === provider.embeddingProviderId).map((preset) => <option key={preset.id} value={preset.id}>{preset.name}{!preset.embeddingModels.length && preset.id !== "custom" ? "（需确认向量接口）" : ""}</option>)}</select></label>}
          <ModelSelect key={`embedding-${embeddingPreset.id}-${provider.embeddingMode}`} label="向量模型" value={provider.embeddingModel} models={[...embeddingPreset.embeddingModels, ...remoteModels.embedding]} onChange={(embeddingModel) => update({ embeddingModel })} onRefresh={() => void refresh("embedding")} busy={!!busy} />
          {provider.embeddingMode === "separate" && <><label className="text-sm md:col-span-2">语义检索 API Key<input type="password" autoComplete="off" spellCheck={false} className={field} value={provider.embeddingApiKey ?? ""} onChange={(event) => update({ embeddingApiKey: event.target.value })} placeholder={canReuseEmbeddingKey ? "已安全保存；留空则继续使用" : "粘贴语义检索厂商的 API Key"} /></label><div className="space-y-3 md:col-span-2"><Endpoint preset={embeddingPreset} value={provider.embeddingBaseUrl} onChange={(embeddingBaseUrl) => { update({ embeddingBaseUrl, embeddingApiKey: "" }); setRemoteModels((current) => ({ ...current, embedding: [] })); }} /></div></>}
          <p className="text-xs leading-5 text-muted md:col-span-2">启用后，建立索引时会把资料的文本片段发送给所选向量服务。更换向量模型或服务地址后需重建语义索引；原始文件不会改变。</p>
        </div>}
      </fieldset>

      {notice && <p role={notice.kind === "error" ? "alert" : "status"} className={cn("rounded-lg px-4 py-3 text-sm leading-6", notice.kind === "success" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700")}>{notice.text}</p>}
      <div className="sticky bottom-0 z-10 -mx-2 flex flex-wrap gap-3 border-t border-line bg-paper px-2 py-4">
        <Button disabled={!!busy || !ready || !desktop} onClick={() => void run("save")}>{busy === "save" ? "正在保存…" : "保存配置"}</Button>
        <Button variant="secondary" disabled={!!busy || !ready || !desktop} onClick={() => void run("test")}>{busy === "test" ? "正在测试…" : "测试连接"}</Button>
        {provider.embeddingMode !== "none" && <Button variant="ghost" disabled={!!busy || !ready || !desktop} onClick={() => void run("index")}>{busy === "index" ? "正在建立索引…" : "保存并重建语义索引"}</Button>}
      </div>
      <p className="text-xs leading-5 text-muted">预设不代表账户已开通该模型，可从服务商刷新或选择自定义 ID。测试连接会发送极少量测试文本，可能消耗少量额度。</p>
      <div className="flex items-start gap-2 border-t border-line pt-5 text-xs leading-6 text-muted"><ShieldCheck className="mt-1 size-4 shrink-0 text-indigo" /><span>密钥保存在 Windows 凭据管理器中，不写入配置文件。拾微没有云账号；模型请求直接发往你选择的服务商。</span></div>
      <details className="text-xs text-muted"><summary className="cursor-pointer">开发者信息</summary><div className="mt-3 grid grid-cols-2 gap-4 pb-2"><p>本地知识片段 <span className="ml-2 text-ink">{indexInfo.chunkCount}</span></p><p className="break-all">当前语义索引 <span className="ml-2 text-ink">{indexInfo.needsRebuild ? "需更新，当前使用本地全文检索" : indexInfo.model ?? "尚未建立"}</span></p></div></details>
    </form>
    <ReleaseSettings />
  </section>;
}
