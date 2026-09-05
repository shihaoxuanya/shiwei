export type ProtocolVersion = "1.0";

export type WorkerPingResult = {
  status: "pong";
  protocolVersion: ProtocolVersion;
  workerVersion: string;
};

export type JobType = "IMPORT" | "PARSE" | "INDEX" | "REINDEX" | "DELETE";
export type JobStatus = "pending" | "running" | "success" | "failed" | "cancelled";

export type SourceStatus = "processing" | "searchable" | "failed";
