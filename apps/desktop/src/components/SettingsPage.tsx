import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { isTauri } from "@tauri-apps/api/core";
import { Database, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import { Button } from "./ui/button";
import { ReleaseSettings } from "./ReleaseInfrastructure";
import { cn } from "../lib/cn";
import { changeChatProvider, defaultProvider, inferProvider, presetFor, providerPresets, type ProviderPreset } from "../lib/provider-presets";
import { clearProviderKey, fetchProviderModels, getIndexStatus, getProviderStatus, rebuildEmbeddings, saveProvider, testProvider, type IndexStatus, type ProviderInput, type ProviderStatus } from "../lib/provider";
import { getWorkerInfo } from "../lib/worker";
import { useWorkerStore } from "../stores/worker-store";
import { openProviderHelp, providerHelpUrl } from "../lib/provider-help";
import { beginLibraryActivity, chooseLibraryParent, isLibraryRelocating, libraryBusyReason, relocateLibrary, useLibraryLocationStore } from "../lib/library-location";

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

type SettingsSection = "ai" | "privacy" | "about";
type TestState = { phase: "untested" | "testing" | "success" | "error"; message?: string };
const untested: TestState = { phase: "untested" };
const embeddingSignature = (input: Partial<ProviderInput> | null) => !input || input.embeddingMode === "none" ? "none" : JSON.stringify([input.embeddingMode === "separate" ? input.embeddingBaseUrl : input.baseUrl, input.embeddingModel]);

export function SettingsPage({ initialSection = "ai", sectionRequestKey, flushNotes = async () => {} }: { initialSection?: SettingsSection; sectionRequestKey?: string; flushNotes?: () => Promise<void> }) {
  const [section, setSection] = useState<SettingsSection>(initialSection);
  const [provider, setProvider] = useState<ProviderInput>(defaultProvider);
  const [saved, setSaved] = useState<ProviderStatus | null>(null);
  const [indexInfo, setIndexInfo] = useState<IndexStatus>({ chunkCount: 0 });
  const [workerInfo, setWorkerInfo] = useState<Awaited<ReturnType<typeof getWorkerInfo>> | null>(null);
  const [infoError, setInfoError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [chatTest, setChatTest] = useState<TestState>(untested);
  const [embeddingTest, setEmbeddingTest] = useState<TestState>(untested);
  const [dirty, setDirty] = useState(false);
  const [rebuildOffer, setRebuildOffer] = useState<"none" | "offer" | "later">("none");
  const [clearTarget, setClearTarget] = useState<"chat" | "embedding" | null>(null);
  const [remoteModels, setRemoteModels] = useState<{ chat: string[]; embedding: string[] }>({ chat: [], embedding: [] });
  const [destination, setDestination] = useState<string | null>(null);
  const [choosingLocation, setChoosingLocation] = useState(false);
  const [locationNotice, setLocationNotice] = useState<{ error: boolean; text: string } | null>(null);
  const choosingRef = useRef(false);
  const migrationRef = useRef(false);
  const migrationDialog = useRef<HTMLDivElement>(null);
  const { locked: relocating, progress: migrationProgress, activities } = useLibraryLocationStore();
  const workInProgress = Object.values(activities)[0];
  const worker = useWorkerStore();
  const desktop = isTauri();
  const chatPreset = presetFor(provider.providerId);
  const embeddingPreset = provider.embeddingMode === "same" ? chatPreset : presetFor(provider.embeddingProviderId);
  const canReuseChatKey = saved?.hasApiKey && saved.baseUrl === provider.baseUrl && (saved.protocol ?? "openai_compatible") === provider.protocol;
  const canReuseEmbeddingKey = saved?.hasEmbeddingApiKey && saved.embeddingBaseUrl === provider.embeddingBaseUrl;
  const chatReady = !!provider.chatModel.trim() && !!provider.baseUrl && !!(provider.apiKey?.trim() || canReuseChatKey);
  const embeddingReady = provider.embeddingMode !== "none" && !!provider.embeddingModel.trim() && (provider.embeddingMode === "same" ? chatReady : !!provider.embeddingBaseUrl && !!(provider.embeddingApiKey?.trim() || canReuseEmbeddingKey));
  const ready = chatReady && (provider.embeddingMode === "none" || embeddingReady);

  useEffect(() => { setSection(initialSection); }, [initialSection, sectionRequestKey]);

  useEffect(() => {
    void Promise.all([getProviderStatus(), getIndexStatus()]).then(([status, index]) => {
      setSaved(status); setIndexInfo(index);
      if (status.baseUrl) setProvider({ ...defaultProvider, ...status, providerId: status.providerId === "custom" || !status.providerId ? inferProvider(status.baseUrl ?? "") : status.providerId,
        embeddingMode: status.embeddingMode ?? "same", apiKey: "", embeddingApiKey: "" });
      if (index.needsRebuild) setRebuildOffer("later");
    }).catch((error: unknown) => setNotice({ kind: "error", text: String(error) })).finally(() => setLoading(false));
    void getWorkerInfo().then(setWorkerInfo).catch(() => setInfoError("暂时无法读取数据位置，请重新连接本地服务后重试。"));
  }, []);

  const update = (values: Partial<ProviderInput>) => {
    setProvider((current) => ({ ...current, ...values })); setNotice(null); setDirty(true);
    if (Object.keys(values).some((key) => ["providerId", "baseUrl", "apiKey", "chatModel", "protocol"].includes(key))) setChatTest(untested);
    if (Object.keys(values).some((key) => key.startsWith("embedding")) || provider.embeddingMode === "same") setEmbeddingTest(untested);
  };
  const test = async (target: "chat" | "embedding") => {
    if (busy || loading) return;
    setBusy(`test-${target}`); setNotice(null);
    const setResult = target === "chat" ? setChatTest : setEmbeddingTest;
    setResult({ phase: "testing" });
    try {
      const result = await testProvider(provider, target);
      if (!result.ok) throw new Error("连接测试未通过，请检查服务、模型与密钥。");
      setResult({ phase: "success" });
    } catch (error) { setResult({ phase: "error", message: error instanceof Error ? error.message : String(error) }); }
    finally { setBusy(null); }
  };
  const run = async (action: "save" | "index") => {
    if (busy || loading) return;
    const finishActivity = action === "index" ? beginLibraryActivity("正在重新处理智能检索") : undefined;
    setBusy(action); setNotice(null);
    try {
      if (action === "index") {
        const result = await rebuildEmbeddings();
        setIndexInfo(await getIndexStatus()); setRebuildOffer("none");
        setNotice({ kind: "success", text: `智能检索已更新，已处理 ${result.indexed} 个文本片段；原始资料未改变。` });
      } else {
        const changedEmbedding = embeddingSignature(provider) !== embeddingSignature(saved);
        await saveProvider(provider);
        setSaved(await getProviderStatus());
        const currentIndex = await getIndexStatus();
        setIndexInfo(currentIndex);
        setProvider((current) => ({ ...current, apiKey: "", embeddingApiKey: "" }));
        setDirty(false);
        if (provider.embeddingMode !== "none" && changedEmbedding && currentIndex.chunkCount > 0) setRebuildOffer("offer");
        if (provider.embeddingMode === "none") setRebuildOffer("none");
        setNotice({ kind: "success", text: "配置已保存。连接结果以单独测试为准；仅更换对话模型不会重新处理资料。" });
      }
    } catch (error) { setNotice({ kind: "error", text: error instanceof Error ? error.message : String(error) }); }
    finally { setBusy(null); finishActivity?.(); }
  };

  const chooseLocation = async () => {
    if (choosingRef.current || isLibraryRelocating() || busy || libraryBusyReason()) return;
    choosingRef.current = true; setChoosingLocation(true); setLocationNotice(null);
    try { const parent = await chooseLibraryParent(); if (parent) setDestination(parent); }
    catch (error) { setLocationNotice({ error: true, text: String(error) }); }
    finally { choosingRef.current = false; setChoosingLocation(false); }
  };
  const changeLocation = async () => {
    if (!destination || migrationRef.current || isLibraryRelocating() || busy) return;
    migrationRef.current = true; setLocationNotice(null);
    try {
      const result = await relocateLibrary(destination, flushNotes);
      setWorkerInfo(current => ({ ...current, dataDir: result.dataDir, protocolVersion: current?.protocolVersion ?? "", workerVersion: current?.workerVersion ?? "" }));
      setInfoError("");
      setLocationNotice({ error: false, text: `资料库已切换到新位置，已复制 ${result.copiedFiles} 个文件。原目录副本仍保留，请确认使用正常后再自行处理。` });
    } catch (error) {
      setLocationNotice({ error: true, text: `未能确认迁移完成，原库副本仍保留。${error instanceof Error ? error.message : String(error)} 请查看下方当前路径，检查目标磁盘空间及写入权限后重试。` });
    } finally {
      migrationRef.current = false; setDestination(null);
      // Native commit may have succeeded before an IPC interruption: always read reality.
      void getWorkerInfo().then((value) => { setWorkerInfo(value); setInfoError(""); }).catch(() => setInfoError("暂时无法确认当前资料库位置，请在本地服务恢复后重新检查。"));
    }
  };
  useEffect(() => {
    if (!destination && !relocating) return;
    const previous = document.activeElement as HTMLElement | null;
    (migrationDialog.current?.querySelector<HTMLElement>("button:not(:disabled)") ?? migrationDialog.current)?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, [destination, relocating]);

  const clearKey = async () => {
    if (!clearTarget || busy) return;
    const target = clearTarget; setBusy(`clear-${target}`); setNotice(null);
    try {
      await clearProviderKey(target);
      const status = await getProviderStatus(); setSaved(status);
      setProvider((current) => ({ ...current, [target === "chat" ? "apiKey" : "embeddingApiKey"]: "", ...(target === "embedding" ? { embeddingMode: status.embeddingMode ?? "none" } : {}) }));
      (target === "chat" ? setChatTest : setEmbeddingTest)(untested);
      if (target === "chat" && provider.embeddingMode === "same") setEmbeddingTest(untested);
      if (target === "embedding") setRebuildOffer("none");
      setClearTarget(null); setNotice({ kind: "success", text: "已清除所选服务的已保存密钥，本地资料和笔记不受影响。" });
    } catch (error) { setNotice({ kind: "error", text: String(error) }); }
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

  const testCopy = (value: TestState, name: string) => `${name}${value.phase === "untested" ? "未测试" : value.phase === "testing" ? "正在测试…" : value.phase === "success" ? "成功" : "失败"}`;
  const help = (id: string) => providerHelpUrl(id) ? <button type="button" onClick={() => void openProviderHelp(id).catch(() => setNotice({ kind: "error", text: "暂时无法打开帮助页面，请稍后重试。" }))} className="min-h-8 text-sm text-indigo underline-offset-4 hover:underline">如何获取密钥</button> : <span className="py-1 text-sm text-muted">请在所选服务商的官方控制台创建 API Key。</span>;

  return <><section className="mx-auto max-w-4xl px-6 py-8 lg:px-10">
    <p className="mb-3 text-xs font-semibold tracking-[0.2em] text-indigo">本地与模型</p>
    <div className="flex items-center justify-between gap-4"><h1 className="font-serif text-3xl font-semibold text-ink">设置</h1><span className="rounded-full bg-[#efedf6] px-3 py-1 text-xs text-muted">{loading ? "正在读取配置…" : dirty ? "有未保存的修改" : saved?.configured ? "配置已保存" : "未配置模型"}</span></div>
    <p className="mt-3 text-sm leading-6 text-muted">选择 AI 服务，了解数据去向，或查看本地服务与更新状态。</p>
    {!desktop && <p className="mt-4 rounded-lg bg-[#efeff7] px-4 py-3 text-xs leading-5 text-indigo">当前为浏览器预览，表单可体验；连接、保存与索引在桌面应用中执行。</p>}

    <div role="tablist" aria-label="设置分区" className="my-6 flex gap-1 border-b border-line">{([['ai', 'AI 服务'], ['privacy', '数据与隐私'], ['about', '关于与更新']] as const).map(([id, label], index, tabs) => <button key={id} id={`settings-tab-${id}`} role="tab" aria-selected={section === id} aria-controls={`settings-panel-${id}`} tabIndex={section === id ? 0 : -1} onClick={() => setSection(id)} onKeyDown={(event) => { const next = event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length][0] : event.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length][0] : event.key === "Home" ? tabs[0][0] : event.key === "End" ? tabs.at(-1)![0] : null; if (next) { event.preventDefault(); setSection(next); document.getElementById(`settings-tab-${next}`)?.focus(); } }} className={cn("min-h-10 border-b-2 px-4 py-2 text-sm", section === id ? "border-indigo font-semibold text-indigo" : "border-transparent text-muted hover:text-ink")}>{label}</button>)}</div>

    <div id="settings-panel-ai" role="tabpanel" aria-labelledby="settings-tab-ai" hidden={section !== "ai"}>
    <form className="space-y-5" onSubmit={(event) => event.preventDefault()}>
      <fieldset disabled={busy !== null || loading} className="min-w-0 rounded-2xl border border-line bg-panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-base font-semibold"><KeyRound className="size-4 text-indigo" />对话服务</h2><p role="status" className={cn("text-sm", chatTest.phase === "error" ? "text-red-700" : chatTest.phase === "success" ? "text-emerald-700" : "text-muted")}>{testCopy(chatTest, "对话连接")}</p></div>
        <p className="mt-2 text-sm leading-6 text-muted">用来理解问题、根据找到的记录组织回答。未配置时仍可导入资料、写笔记和按关键词查找。</p>
        <label className="mt-5 block text-sm">模型厂商<select className={field} value={provider.providerId} onChange={(event) => { update(changeChatProvider(provider, event.target.value)); setRemoteModels({ chat: [], embedding: [] }); }}>{providerPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
        <label className="mt-4 block text-sm">对话 API Key<input type="password" autoComplete="off" spellCheck={false} className={field} value={provider.apiKey ?? ""} onChange={(event) => update({ apiKey: event.target.value })} placeholder={canReuseChatKey ? "已安全保存；留空则继续使用" : "粘贴所选厂商的 API Key"} /></label>
        <div className="mt-1 flex flex-wrap items-center justify-between gap-3">{help(provider.providerId)}{canReuseChatKey && <button type="button" className="min-h-8 text-sm text-muted underline-offset-4 hover:underline" onClick={() => setClearTarget("chat")}>清除已保存的对话密钥</button>}</div>
        {chatTest.phase === "error" && <p role="alert" className="mt-3 text-sm leading-6 text-red-700">{chatTest.message}</p>}
        <details className="mt-4 border-t border-line pt-3" open={provider.providerId === "custom" ? true : undefined}>
          <summary className="cursor-pointer py-2 text-sm text-indigo">模型与服务地址 <span className="ml-2 text-muted">{provider.chatModel || "请选择模型"}</span></summary>
          <div className="mt-3 space-y-4">
            <ModelSelect key={`chat-${provider.providerId}`} label="对话模型" value={provider.chatModel} models={[...chatPreset.chatModels, ...remoteModels.chat]} onChange={(chatModel) => update({ chatModel })} onRefresh={() => void refresh("chat")} busy={!!busy} />
            {provider.providerId === "custom" && <label className="block text-sm">接口协议<select className={field} value={provider.protocol} onChange={(event) => update({ protocol: event.target.value as ProviderInput["protocol"], apiKey: "", embeddingMode: event.target.value === "anthropic" && provider.embeddingMode === "same" ? "none" : provider.embeddingMode })}><option value="openai_compatible">OpenAI 兼容</option><option value="anthropic">Anthropic Messages</option></select></label>}
            <Endpoint preset={chatPreset} value={provider.baseUrl} onChange={(baseUrl) => { update({ baseUrl, apiKey: "" }); setRemoteModels((current) => ({ ...current, chat: [] })); }} />
            {chatPreset.note && <p className="text-sm leading-6 text-muted">{chatPreset.note.replace(/Embedding/g, "智能检索")}</p>}
          </div>
        </details>
      </fieldset>

      <p className="flex items-start gap-2 text-sm leading-6 text-muted"><ShieldCheck className="mt-1 size-4 shrink-0 text-indigo" /><span>密钥保存在系统安全凭据存储中，不会回显。使用在线 AI 时，问题、所需资料片段和必要的对话上下文会发送给你选择的服务；在线智能检索还会依次发送待索引资料的全部文本片段。服务地址指向本机时，请求由该本地服务处理。模型调用及连接测试可能产生费用。</span></p>

      <fieldset disabled={busy !== null || loading} className="min-w-0 rounded-2xl border border-line bg-panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-base font-semibold"><Database className="size-4 text-indigo" />智能检索 <span className="font-normal text-muted">· 可选</span></h2><p role="status" className={cn("text-sm", embeddingTest.phase === "error" ? "text-red-700" : embeddingTest.phase === "success" ? "text-emerald-700" : "text-muted")}>{provider.embeddingMode === "none" ? "智能检索未启用" : testCopy(embeddingTest, "智能检索连接")}</p></div>
        <p className="mt-2 text-sm leading-6 text-muted">让不同表达也能找到相近内容。未启用时仍可按关键词查找和基于记录问答。</p>
        <details className="mt-3"><summary className="cursor-pointer py-2 text-sm text-indigo">检索方式与模型配置</summary>
        <label className="mt-5 block text-sm">检索方式<select className={field} value={provider.embeddingMode} onChange={(event) => {
          const mode = event.target.value as ProviderInput["embeddingMode"];
          update({ embeddingMode: mode, embeddingModel: mode === "same" ? chatPreset.embeddingModels[0] ?? "" : presetFor(provider.embeddingProviderId).embeddingModels[0] ?? "" });
          setRemoteModels((current) => ({ ...current, embedding: [] }));
        }}><option value="none">仅本地全文搜索（无需额外配置）</option>{provider.protocol !== "anthropic" && <option value="same">与对话使用同一服务</option>}<option value="separate">单独选择语义检索服务</option></select></label>
        {provider.embeddingMode !== "none" && <div className="mt-5 grid gap-5 md:grid-cols-2">
          {provider.embeddingMode === "separate" && <label className="text-sm">语义检索厂商<select className={field} value={provider.embeddingProviderId} onChange={(event) => { const preset = presetFor(event.target.value); update({ embeddingProviderId: preset.id, embeddingBaseUrl: preset.endpoints[0]?.url ?? "", embeddingModel: preset.embeddingModels[0] ?? "", embeddingApiKey: "" }); setRemoteModels((current) => ({ ...current, embedding: [] })); }}>{providerPresets.filter((preset) => preset.embeddingModels.length > 0 || preset.id === "custom" || preset.id === provider.embeddingProviderId).map((preset) => <option key={preset.id} value={preset.id}>{preset.name}{!preset.embeddingModels.length && preset.id !== "custom" ? "（需确认向量接口）" : ""}</option>)}</select></label>}
          <ModelSelect key={`embedding-${embeddingPreset.id}-${provider.embeddingMode}`} label="向量模型" value={provider.embeddingModel} models={[...embeddingPreset.embeddingModels, ...remoteModels.embedding]} onChange={(embeddingModel) => update({ embeddingModel })} onRefresh={() => void refresh("embedding")} busy={!!busy} />
          {provider.embeddingMode === "separate" && <><label className="text-sm md:col-span-2">语义检索 API Key<input type="password" autoComplete="off" spellCheck={false} className={field} value={provider.embeddingApiKey ?? ""} onChange={(event) => update({ embeddingApiKey: event.target.value })} placeholder={canReuseEmbeddingKey ? "已安全保存；留空则继续使用" : "粘贴语义检索厂商的 API Key"} /></label><div className="space-y-3 md:col-span-2"><Endpoint preset={embeddingPreset} value={provider.embeddingBaseUrl} onChange={(embeddingBaseUrl) => { update({ embeddingBaseUrl, embeddingApiKey: "" }); setRemoteModels((current) => ({ ...current, embedding: [] })); }} /></div></>}
          {provider.embeddingMode === "separate" && <div className="flex flex-wrap items-center justify-between gap-3 md:col-span-2">{help(provider.embeddingProviderId)}{canReuseEmbeddingKey && <button type="button" className="min-h-8 text-sm text-muted hover:underline" onClick={() => setClearTarget("embedding")}>清除已保存的检索密钥</button>}</div>}
        </div>}
        </details>
        {provider.embeddingMode !== "none" && <div className="mt-4"><Button variant="secondary" disabled={!desktop || !embeddingReady} onClick={() => void test("embedding")}>测试智能检索连接</Button>{embeddingTest.phase === "error" && <p role="alert" className="mt-3 text-sm leading-6 text-red-700">{embeddingTest.message}</p>}</div>}
      </fieldset>

      {clearTarget && <section aria-label="确认清除密钥" className="rounded-xl border border-line bg-panel p-5"><p className="text-sm leading-6">清除{clearTarget === "chat" ? "对话" : "智能检索"}服务的已保存密钥？对应在线服务将暂不可用，本地内容不会删除。</p><div className="mt-3 flex gap-3"><Button variant="secondary" disabled={!!busy} onClick={() => setClearTarget(null)}>取消清除</Button><Button disabled={!!busy} onClick={() => void clearKey()}>确认清除密钥</Button></div></section>}
      {rebuildOffer !== "none" && provider.embeddingMode !== "none" && <section aria-label="重新处理智能检索" className="rounded-xl border border-line bg-panel p-5"><p className="text-sm leading-6">{rebuildOffer === "offer" ? "你更换了智能检索服务，需要重新处理已有资料。" : "已有资料的智能检索等待重新处理。"}此操作可能产生模型费用，并将相关文本发送给所选服务。开始前保留旧索引，完成后再安全切换；未完成时仍可按关键词查找。</p>{dirty && <p className="mt-2 text-sm text-muted">请先保存当前配置，再开始处理。</p>}<div className="mt-3 flex flex-wrap gap-3">{rebuildOffer === "offer" && <Button variant="secondary" disabled={!!busy} onClick={() => setRebuildOffer("later")}>稍后处理</Button>}<Button variant="secondary" disabled={!!busy || dirty || !desktop} onClick={() => void run("index")}>{busy === "index" ? "正在处理…" : "开始处理"}</Button></div></section>}
      {notice && <p role={notice.kind === "error" ? "alert" : "status"} className={cn("rounded-lg px-4 py-3 text-sm leading-6", notice.kind === "success" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700")}>{notice.text}</p>}
      <p className="text-sm leading-6 text-muted">预设不代表账户已开通该模型。测试只发送固定测试文本，不读取你的资料，可能消耗少量额度。</p>
      <div className="flex flex-wrap gap-3 border-t border-line bg-paper py-4">
        <Button disabled={!!busy || loading || !ready || !desktop} onClick={() => void run("save")}>{busy === "save" ? "正在保存…" : "保存配置"}</Button>
        <Button variant="secondary" disabled={!!busy || loading || !chatReady || !desktop} onClick={() => void test("chat")}>{busy === "test-chat" ? "正在测试…" : "测试连接"}</Button>
      </div>
    </form>
    </div>
    <div id="settings-panel-privacy" role="tabpanel" aria-labelledby="settings-tab-privacy" hidden={section !== "privacy"} className="space-y-5">
      <section className="rounded-2xl border border-line bg-panel p-6"><h2 className="text-base font-semibold">数据保存在本机</h2><p className="mt-3 text-sm leading-6 text-muted">原始资料副本、笔记、对话、数据库和索引默认保存在这台电脑上。</p><p className="mt-3 break-all rounded-lg bg-paper p-3 text-sm" aria-label="当前资料库位置">{workerInfo?.dataDir ?? (infoError || "正在读取数据位置…")}</p><Button variant="secondary" className="mt-3" disabled={choosingLocation || relocating || !!busy || !!workInProgress || loading || (desktop && !workerInfo)} onClick={() => void chooseLocation()}>{choosingLocation ? "正在选择…" : "更改位置"}</Button>{workInProgress && <p role="status" className="mt-2 text-sm text-muted">{workInProgress}，完成后可更改位置。</p>}{infoError && workerInfo && <p role="alert" className="mt-2 text-sm text-red-700">{infoError}</p>}{locationNotice && <p role={locationNotice.error ? "alert" : "status"} className={cn("mt-3 break-words text-sm leading-6", locationNotice.error ? "text-red-700" : "text-emerald-700")}>{locationNotice.text}</p>}<p className="mt-3 text-sm leading-6 text-muted">更改位置会复制资料库，校验成功后切换，原目录副本保留。当前版本尚未提供一键备份或完整导出入口。删除资料只处理拾微管理的副本，不删除原始位置的文件。</p></section>
      <section className="rounded-2xl border border-line bg-panel p-6"><h2 className="text-base font-semibold">哪些文本会发送给模型服务</h2><ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-7 text-muted"><li>对话：当前问题、回答需要的资料片段及必要的近期对话上下文。</li><li>在线智能检索：建立索引时依次处理待索引资料的全部文本片段，检索时发送查询文本；不只是提问时发送少量资料。</li><li>解析文档和识别扫描件在本地进行。服务地址指向本机时，请求由该本地服务处理。</li></ul><p className="mt-3 text-sm leading-6 text-muted">这些模型请求直接发往你选择的服务，不是拾微统计服务。模型服务的内容保留与使用规则由该服务决定。</p></section>
      <ReleaseSettings section="privacy" />
    </div>
    <div id="settings-panel-about" role="tabpanel" aria-labelledby="settings-tab-about" hidden={section !== "about"} className="space-y-5">
      <ReleaseSettings section="about" />
      <section className="rounded-2xl border border-line bg-panel p-6"><h2 className="text-base font-semibold">诊断信息</h2><p className="mt-3 text-sm">本地服务：{worker.status === "connected" ? "正常" : worker.status === "checking" ? "正在检查" : worker.status === "preview" ? "浏览器预览" : worker.status === "error" ? "暂时不可用" : "尚未检查"}</p><Button variant="secondary" className="mt-3" disabled={worker.status === "checking"} onClick={() => { void worker.check(); void getWorkerInfo().then((value) => { setWorkerInfo(value); setInfoError(""); }).catch(() => setInfoError("无法读取本地服务信息，请重试。")); }}>重新检查</Button>{worker.error && <p role="alert" className="mt-3 break-words text-sm text-red-700">{worker.error}</p>}<details className="mt-4 text-sm text-muted"><summary className="cursor-pointer py-2">技术详情</summary><dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-3"><dt>本地服务版本</dt><dd className="break-all">{workerInfo?.workerVersion ?? worker.result?.workerVersion ?? "未知"}</dd><dt>协议版本</dt><dd>{workerInfo?.protocolVersion ?? worker.result?.protocolVersion ?? "未知"}</dd><dt>本地知识片段</dt><dd>{indexInfo.chunkCount}</dd><dt>智能检索索引</dt><dd className="break-all">{indexInfo.needsRebuild ? "需更新，当前使用本地全文检索" : indexInfo.model ?? "尚未建立"}</dd></dl></details></section>
    </div>
  </section>{(destination || relocating) && createPortal(<div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/25 p-6" onKeyDown={(event) => {
    if (event.key === "Escape") { event.preventDefault(); if (!isLibraryRelocating()) setDestination(null); }
    if (event.key === "Tab") {
      const controls = Array.from(migrationDialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
      if (!controls.length) { event.preventDefault(); migrationDialog.current?.focus(); }
      else if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0].focus(); }
    }
  }}><div ref={migrationDialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="library-location-title" className="w-full max-w-lg rounded-2xl border border-line bg-paper p-6 shadow-xl">
    <h2 id="library-location-title" className="font-serif text-xl font-semibold">{relocating ? "正在迁移资料库" : "更改资料库位置"}</h2>
    {relocating ? <div role="status" aria-live="polite" className="mt-4 text-sm leading-7"><p>{migrationProgress?.phase === "copying" ? "正在复制资料库…" : migrationProgress?.phase === "verifying" ? "正在校验副本…" : migrationProgress?.phase === "switching" ? "正在切换到新位置…" : "正在保存笔记并准备迁移…"}</p>{migrationProgress?.completedFiles !== undefined && migrationProgress.totalFiles !== undefined && <p>已处理 {migrationProgress.completedFiles} / {migrationProgress.totalFiles} 个文件</p>}<p className="mt-3 text-muted">迁移期间暂时不能编辑或关闭拾微。请保持目标磁盘连接，校验成功后才会切换；原目录副本会保留。</p></div> : <><p className="mt-4 text-sm leading-7">将在下面的文件夹中新建唯一的“拾微资料库-xxxx”目录，不覆盖现有文件。复制并校验成功后才切换，原目录副本仍保留。</p><p className="mt-3 break-all rounded-lg bg-panel p-3 text-sm">{destination}</p><p className="mt-3 text-sm leading-6 text-muted">迁移前会先保存笔记；导入、对话和索引处理结束后才能开始。迁移过程中请勿关闭应用或断开磁盘。</p><div className="mt-5 flex justify-end gap-3"><Button variant="secondary" onClick={() => setDestination(null)}>取消</Button><Button disabled={!!workInProgress || !!busy} onClick={() => void changeLocation()}>确认迁移</Button></div>{workInProgress && <p className="mt-2 text-sm text-muted">{workInProgress}，请稍后再确认。</p>}</>}
  </div></div>, document.body)}</>;
}
