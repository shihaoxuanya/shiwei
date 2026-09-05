import { invoke, isTauri } from "@tauri-apps/api/core";

export type ProviderInput = {
  baseUrl: string;
  apiKey?: string;
  chatModel: string;
  embeddingModel: string;
  providerId: string;
  protocol: "openai_compatible" | "anthropic";
  embeddingMode: "same" | "separate" | "none";
  embeddingProviderId: string;
  embeddingBaseUrl: string;
  embeddingApiKey?: string;
};

export type ProviderStatus = Partial<Omit<ProviderInput, "apiKey" | "embeddingApiKey">> & {
  configured: boolean;
  hasApiKey: boolean;
  hasEmbeddingApiKey?: boolean;
};

export type ProviderTestResult = {
  ok: boolean;
  chatOk: boolean;
  embeddingEnabled: boolean;
  embeddingModel?: string;
  embeddingDimension?: number;
};

export type EmbeddingBuildResult = {
  indexed: number;
  model?: string;
  dimension?: number;
  embeddingVersionId?: string;
};

export type IndexStatus = {
  chunkCount: number;
  needsRebuild?: boolean;
  searchTextVersion?: number;
  embeddingVersionId?: string;
  provider?: string;
  model?: string;
  dimension?: number;
  lastIndexedAt?: string;
};

export async function getProviderStatus(): Promise<ProviderStatus> {
  if (!isTauri()) return { configured: false, hasApiKey: false };
  return invoke<ProviderStatus>("provider_status");
}

export async function testProvider(config: ProviderInput): Promise<ProviderTestResult> {
  return invoke<ProviderTestResult>("provider_test", { config });
}

export async function fetchProviderModels(config: ProviderInput, target: "chat" | "embedding"): Promise<string[]> {
  const result = await invoke<{ models: string[] }>("provider_models", { config, target });
  return result.models;
}

export async function saveProvider(config: ProviderInput): Promise<void> {
  await invoke("provider_save", { config });
}

export async function rebuildEmbeddings(): Promise<EmbeddingBuildResult> {
  return invoke<EmbeddingBuildResult>("rebuild_embeddings");
}

export async function getIndexStatus(): Promise<IndexStatus> {
  if (!isTauri()) return { chunkCount: 0 };
  return invoke<IndexStatus>("index_status");
}
