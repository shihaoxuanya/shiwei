import { create } from "zustand";
import { pingWorker, type WorkerPingResult } from "../lib/worker";

type WorkerStatus = "idle" | "checking" | "connected" | "preview" | "error";

type WorkerState = {
  status: WorkerStatus;
  result: WorkerPingResult | null;
  error: string | null;
  check: () => Promise<void>;
};

export const useWorkerStore = create<WorkerState>((set) => ({
  status: "idle",
  result: null,
  error: null,
  check: async () => {
    set({ status: "checking", error: null });
    try {
      const result = await pingWorker();
      set({
        result,
        status: result.status === "pong" ? "connected" : "preview",
      });
    } catch (error) {
      set({
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  },
}));
