export type Workspace = {
  workspace_id: string;
  token: string;
  expires_at: string;
};
export type ExportImport = {
  workspace_id: string;
  token: string;
  expires_at: number;
  channel: { id: string; title: string; kind?: "playlist" };
  metrics: { catalog: number; videos: number; chunks: number };
  coverage?: ExportCoverage;
  report_id?: string;
};

export type ExportCoverage = {
  discovered: number;
  transcribed: number;
  unavailable: number;
  failed: number;
  pending: number;
  indexed: number;
  reasons: Array<{ reason: string; count: number }>;
};
export type ExportIssue = { video_id: string; title: string; reason: string; outcome: "unavailable" | "failed" | "pending" };

export type Source = { video_id: string; title: string; url: string; start_time: number; timestamp: string; quote: string };
export type TranscriptChunk = { text: string; start_time: number; timestamp: string; url: string };
export type Transcript = { video_id: string; title: string; transcript_source: string; chunks: TranscriptChunk[] };
export type Usage = { input_tokens: number; output_tokens: number; total_tokens: number };
export type ChatResult = {
  answer: string;
  sources: Source[];
  usage?: Usage;
  retrieved_chunks: number;
};
export type JobResult = {
  video_id?: string;
  title?: string;
  segment_count?: number;
  transcript_source?: "youtube_captions" | "local_speech_recognition";
  video_count?: number;
  catalog_count?: number;
  chunk_count?: number;
  added_video_count?: number;
  added_titles?: string[];
  message?: string;
  warning?: string;
};
export type TranscriptImportJob = {
  id: string;
  status: string;
  message: string;
  indexed: boolean;
  indexed_count: number;
  coverage: ExportCoverage;
  catalog_complete: boolean;
  issue_total: number;
  issues: ExportIssue[];
  metrics?: { catalog: number; videos: number; chunks: number };
  channel: { id: string; title: string; kind?: "playlist" };
};

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120_000);
  try {
    const response = await fetch(`${API_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || `Request failed (${response.status})`);
    }
    return body as T;
  } finally {
    window.clearTimeout(timeout);
  }
}

export const api = {
  transcript: (workspace: Workspace, videoId: string) =>
    request<Transcript>(
      `/api/v1/workspaces/${workspace.workspace_id}/videos/${encodeURIComponent(videoId)}/transcript`,
      { method: "GET" },
      workspace.token,
    ),
  ingestVideo: (workspace: Workspace, video: string) =>
    request<{ job_id: string; status: string }>(
      `/api/v1/workspaces/${workspace.workspace_id}/videos`,
      { method: "POST", body: JSON.stringify({ video }) },
      workspace.token,
    ),
  uploadExport: (workspace: Workspace, file: File) =>
    request<TranscriptImportJob>(
      `/api/v1/workspaces/${workspace.workspace_id}/imports`,
      { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } },
      workspace.token,
    ),
  exportStatus: (workspace: Workspace, exportId: string) =>
    request<TranscriptImportJob>(`/api/v1/exports/${exportId}`, { method: "GET" }, workspace.token),
  exportIssues: (workspace: Workspace, exportId: string, offset: number) =>
    request<{ total: number; offset: number; items: ExportIssue[] }>(
      `/api/v1/exports/${exportId}/issues?offset=${offset}&limit=50`,
      { method: "GET" }, workspace.token,
    ),
  resumeExport: (workspace: Workspace, exportId: string) =>
    request<TranscriptImportJob>(`/api/v1/exports/${exportId}/resume`, { method: "POST" }, workspace.token),
  claimExport: (code: string) => request<ExportImport>("/api/v1/export-handoffs/claim", {
    method: "POST", body: JSON.stringify({ code }),
  }),
  createWorkspace: () => request<Workspace>("/api/v1/workspaces", { method: "POST" }),
  ingest: (workspace: Workspace, channel: string, maxVideos: number) =>
    request<{ job_id: string; status: string }>(
      `/api/v1/workspaces/${workspace.workspace_id}/ingestions`,
      { method: "POST", body: JSON.stringify({ channel, max_videos: maxVideos }) },
      workspace.token,
    ),
  expand: (workspace: Workspace, question: string) =>
    request<{ job_id: string; status: string }>(
      `/api/v1/workspaces/${workspace.workspace_id}/expansions`,
      { method: "POST", body: JSON.stringify({ question }) },
      workspace.token,
    ),
  job: (workspace: Workspace, jobId: string) =>
    request<{ job_id: string; status: string; result?: JobResult }>(
      `/api/v1/jobs/${jobId}`,
      { method: "GET" },
      workspace.token,
    ),
  chat: (
    workspace: Workspace,
    question: string,
    history: Array<{ role: "user" | "assistant"; content: string }>,
  ) =>
    request<ChatResult>(
      `/api/v1/workspaces/${workspace.workspace_id}/chat`,
      { method: "POST", body: JSON.stringify({ question, history: history.slice(-4) }) },
      workspace.token,
    ),
};
