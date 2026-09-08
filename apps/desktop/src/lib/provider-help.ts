import { invoke, isTauri } from "@tauri-apps/api/core";

// Official documentation verified on 2026-09-08. Unknown providers get plain
// control-panel guidance rather than an invented link or search redirect.
const help: Record<string,string> = {
  deepseek: "https://api-docs.deepseek.com/",
  qwen: "https://help.aliyun.com/zh/model-studio/get-api-key",
};
export function providerHelpUrl(id: string): string | undefined { return help[id]; }
export async function openProviderHelp(id: string): Promise<void> {
  const url = providerHelpUrl(id);
  if(!url) throw new Error("请从所选服务商的官方控制台查看密钥说明");
  if(isTauri()) await invoke("provider_help", { providerId:id });
  else window.open(url, "_blank", "noopener,noreferrer");
}
