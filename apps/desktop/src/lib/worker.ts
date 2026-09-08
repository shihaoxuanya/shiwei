import { invoke, isTauri } from "@tauri-apps/api/core";

export type WorkerPingResult = {
  status: "pong" | "preview";
  protocolVersion: string;
  workerVersion: string;
};

export async function getWorkerInfo(): Promise<{ dataDir: string; protocolVersion: string; workerVersion: string }> {
  if (!isTauri()) return { dataDir: "请在桌面应用中查看实际数据位置", protocolVersion: "1.0", workerVersion: "browser-preview" };
  return invoke("worker_info");
}

export async function pingWorker(): Promise<WorkerPingResult> {
  if (!isTauri()) {
    return {
      status: "preview",
      protocolVersion: "1.0",
      workerVersion: "browser-preview",
    };
  }

  return invoke<WorkerPingResult>("worker_ping");
}
