import { invoke, isTauri } from "@tauri-apps/api/core";

export type WorkerPingResult = {
  status: "pong" | "preview";
  protocolVersion: string;
  workerVersion: string;
};

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
