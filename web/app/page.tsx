"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { api, ChatResult, ExportImport, JobResult, Source, Usage, Workspace } from "@/lib/api";

type Status = "booting" | "idle" | "ingesting" | "ready" | "error";
type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  usage?: Usage;
};
type SavedWorkspace = {
  workspace: Workspace;
  metrics: { catalog: number; videos: number; chunks: number };
  channel: string;
  videoUrl: string;
  importedChannel: string;
  importedKind: "video" | "channel";
  transcriptSource: "youtube_captions" | "local_speech_recognition" | "";
};

function saveWorkspaceSession(value: SavedWorkspace) {
  try { sessionStorage.setItem("channelmind-session", JSON.stringify(value)); } catch { /* Storage is optional. */ }
}

const suggestions = [
  "What are the main ideas in this video?",
  "What practical steps does the speaker recommend?",
  "Where does the speaker explain the key concept?",
];
const repositoryUrl = process.env.NEXT_PUBLIC_REPOSITORY_URL;

const pause = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export default function Home() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [status, setStatus] = useState<Status>("booting");
  const [channel, setChannel] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [transcriptSource, setTranscriptSource] = useState<"youtube_captions" | "local_speech_recognition" | "">("");
  const [maxVideos, setMaxVideos] = useState(5);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [metrics, setMetrics] = useState({ catalog: 0, videos: 0, chunks: 0 });
  const [error, setError] = useState("");
  const [answering, setAnswering] = useState(false);
  const [answerStage, setAnswerStage] = useState<"catalog" | "answer">("catalog");
  const [importedChannel, setImportedChannel] = useState("");
  const [importedKind, setImportedKind] = useState<"video" | "channel" | "playlist">("channel");
  const [importJob, setImportJob] = useState<{ workspace: Workspace; id: string } | null>(null);
  const initialization = useRef<Promise<{ workspace: Workspace; imported?: ExportImport; saved?: SavedWorkspace; notice?: string }> | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!initialization.current) {
      initialization.current = (async () => {
        const code = new URLSearchParams(window.location.hash.slice(1)).get("channelmind");
        let imported: ExportImport | undefined;
        let notice = "";
        if (code) {
          window.history.replaceState(null, "", window.location.pathname + window.location.search);
          try {
            imported = await api.claimExport(code);
            sessionStorage.setItem("channelmind-import", JSON.stringify(imported));
          } catch {
            sessionStorage.removeItem("channelmind-import");
            notice = "This extension export link has expired or was already used. Create a fresh link from the extension to import it; you can still process a video or channel here.";
          }
        } else {
          try {
            const saved = JSON.parse(sessionStorage.getItem("channelmind-import") || "null") as ExportImport | null;
            if (saved && saved.expires_at * 1000 > Date.now()) imported = saved;
            else sessionStorage.removeItem("channelmind-import");
          } catch { sessionStorage.removeItem("channelmind-import"); }
        }
        if (imported) return { workspace: {
          workspace_id: imported.workspace_id, token: imported.token,
          expires_at: new Date(imported.expires_at * 1000).toISOString(),
        }, imported, notice };
        try {
          const saved = JSON.parse(sessionStorage.getItem("channelmind-session") || "null") as SavedWorkspace | null;
          if (saved?.workspace?.token && Date.parse(saved.workspace.expires_at) > Date.now()) {
            return { workspace: saved.workspace, saved, notice };
          }
          sessionStorage.removeItem("channelmind-session");
        } catch { sessionStorage.removeItem("channelmind-session"); }
        return { workspace: await api.createWorkspace(), notice };
      })();
    }
    initialization.current
      .then(({ workspace: value, imported, saved, notice }) => {
        setWorkspace(value);
        if (notice) setError(notice);
        if (imported) {
          setMetrics(imported.metrics);
          setChannel(imported.channel.kind === "playlist" ? "" : `https://www.youtube.com/channel/${imported.channel.id}`);
          setImportedChannel(imported.channel.title);
          setImportedKind(imported.channel.kind === "playlist" ? "playlist" : "channel");
          setStatus("ready");
        } else if (saved) {
          setMetrics(saved.metrics);
          setChannel(saved.channel);
          setVideoUrl(saved.videoUrl);
          setImportedChannel(saved.importedChannel);
          setImportedKind(saved.importedKind);
          setTranscriptSource(saved.transcriptSource);
          setStatus("ready");
        } else setStatus("idle");
      })
      .catch((reason: Error) => {
        setError(reason.message);
        setStatus("error");
      });
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, answering]);

  async function waitForJob(jobId: string, target = workspace, maxAttempts = 300): Promise<JobResult> {
    if (!target) throw new Error("Workspace is not ready.");
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await pause(2000);
      const job = await api.job(target, jobId);
      if (job.status === "failed") {
        throw new Error(job.result?.message || "Background processing failed. Please try again.");
      }
      if (job.status === "finished") return job.result ?? {};
    }
    throw new Error("Background processing timed out.");
  }

  async function processChannel(event: FormEvent) {
    event.preventDefault();
    if (!workspace || !channel.trim()) return;
    setError("");
    setStatus("ingesting");
    setMessages([]);
    try {
      // Keep an imported channel intact when starting a new public-channel search.
      const target = importedChannel ? await api.createWorkspace() : workspace;
      setWorkspace(target);
      setImportedChannel("");
      setImportedKind("channel");
      sessionStorage.removeItem("channelmind-import");
      sessionStorage.removeItem("channelmind-session");
      const queued = await api.ingest(target, channel.trim(), maxVideos);
      const result = await waitForJob(queued.job_id, target);
      const nextMetrics = {
        catalog: result.catalog_count ?? 0,
        videos: result.video_count ?? 0,
        chunks: result.chunk_count ?? 0,
      };
      setMetrics(nextMetrics);
      saveWorkspaceSession({ workspace: target, metrics: nextMetrics, channel, videoUrl, importedChannel: "", importedKind: "channel", transcriptSource: "" });
      setStatus("ready");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to process this channel.");
      setStatus("error");
    }
  }

  async function processVideo(event: FormEvent) {
    event.preventDefault();
    if (!workspace || !videoUrl.trim()) return;
    setError("");
    setStatus("ingesting");
    setMessages([]);
    try {
      const target = importedChannel ? await api.createWorkspace() : workspace;
      setWorkspace(target);
      sessionStorage.removeItem("channelmind-import");
      sessionStorage.removeItem("channelmind-session");
      const queued = await api.ingestVideo(target, videoUrl.trim());
      const result = await waitForJob(queued.job_id, target, 3600);
      const nextMetrics = { catalog: 1, videos: 1, chunks: result.chunk_count ?? 0 };
      setMetrics(nextMetrics);
      setImportedChannel(result.title || "YouTube video");
      setImportedKind("video");
      setTranscriptSource(result.transcript_source || "youtube_captions");
      saveWorkspaceSession({ workspace: target, metrics: nextMetrics, channel, videoUrl, importedChannel: result.title || "YouTube video", importedKind: "video", transcriptSource: result.transcript_source || "youtube_captions" });
      setStatus("ready");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to process this video.");
      setStatus("error");
    }
  }

  async function finishImport(target: Workspace, id: string) {
    let current = await api.exportStatus(target, id);
    for (let attempt = 0; attempt < 1800 && !current.indexed; attempt += 1) {
      if (["paused", "cancelled", "interrupted"].includes(current.status)) {
        throw new Error(current.message || "Transcript import was interrupted.");
      }
      await pause(2000);
      current = await api.exportStatus(target, id);
    }
    if (!current.indexed || !current.metrics) throw new Error("Transcript import did not finish.");
    setMetrics(current.metrics);
    setChannel(current.channel.kind === "playlist" ? "" : `https://www.youtube.com/channel/${current.channel.id}`);
    setImportedChannel(current.channel.title);
    setImportedKind(current.channel.kind === "playlist" ? "playlist" : "channel");
    sessionStorage.setItem("channelmind-import", JSON.stringify({
      workspace_id: target.workspace_id, token: target.token,
      expires_at: Date.parse(target.expires_at) / 1000,
      channel: current.channel, metrics: current.metrics,
    }));
    sessionStorage.removeItem("channelmind-session");
    setImportJob(null);
    setStatus("ready");
  }

  async function importTranscriptFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");
    setStatus("ingesting");
    setMessages([]);
    try {
      if (file.size > 60 * 1024 * 1024) throw new Error("Choose an export smaller than 60 MB.");
      const target = await api.createWorkspace();
      const started = await api.uploadExport(target, file);
      setWorkspace(target);
      setImportJob({ workspace: target, id: started.id });
      await finishImport(target, started.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to import this transcript file.");
      setStatus("error");
    }
  }

  async function resumeImport() {
    if (!importJob) return;
    setError("");
    setStatus("ingesting");
    try {
      await api.resumeExport(importJob.workspace, importJob.id);
      await finishImport(importJob.workspace, importJob.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to resume import.");
      setStatus("error");
    }
  }

  async function ask(event: FormEvent, suggestedQuestion?: string) {
    event.preventDefault();
    const nextQuestion = (suggestedQuestion ?? question).trim();
    if (!workspace || status !== "ready" || !nextQuestion || answering) return;
    const previous = messages.map(({ role, content }) => ({ role, content }));
    setMessages((current) => [...current, { role: "user", content: nextQuestion }]);
    setQuestion("");
    setAnswering(true);
    setAnswerStage("catalog");
    setError("");
    try {
      if (!importedChannel) {
        const queued = await api.expand(workspace, nextQuestion);
        const expansion = await waitForJob(queued.job_id);
        setMetrics((current) => ({
          ...current,
          videos: current.videos + (expansion.added_video_count ?? 0),
          chunks: current.chunks + (expansion.chunk_count ?? 0),
        }));
      }
      setAnswerStage("answer");
      const result: ChatResult = await api.chat(workspace, nextQuestion, previous);
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: result.answer,
          sources: result.sources,
          usage: result.usage,
        },
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to answer right now.");
    } finally {
      setAnswering(false);
    }
  }

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="ChannelMind home">
          <span className="brandMark">CM</span>
          <span>ChannelMind</span>
        </a>
        <div className="topMeta">
          <span className="liveDot" /> Grounded answers
          {repositoryUrl && <a href={repositoryUrl} target="_blank" rel="noreferrer">GitHub ↗</a>}
        </div>
      </header>

      <section className="hero" id="top">
        <div className="eyebrow">YouTube intelligence, with receipts</div>
        <h1>Turn a video into<br /><em>searchable knowledge.</em></h1>
        <p>
          Ask across videos and get concise answers grounded in transcripts—each claim linked
          to the exact moment it came from.
        </p>
        <div className="flow" aria-label="Architecture overview">
          <span>YouTube</span><i>→</i><span>Transcripts</span><i>→</i><span>Vector search</span><i>→</i><span>Cited answer</span>
        </div>
      </section>

      <section className="workspace">
        <aside className="controlPanel">
          <div className="panelHeading">
            <span className="step">01</span>
            <div><h2>{importedChannel ? (importedKind === "video" ? "Video ready" : `${importedKind === "playlist" ? "Playlist" : "Channel"} export imported`) : "Build a knowledge base"}</h2><p>{importedChannel || "Start with one public YouTube video."}</p></div>
          </div>
          <form onSubmit={processVideo}>
            <label htmlFor="video">Video URL</label>
            <input id="video" value={videoUrl} onChange={(event) => setVideoUrl(event.target.value)} placeholder="https://www.youtube.com/watch?v=..." disabled={status === "ingesting"} />
            <small className="rangeHint">Uses free captions when available. Otherwise it downloads audio and recognizes speech on the app server; long videos may take time.</small>
            <button className="primary" disabled={!workspace || status === "ingesting" || status === "booting"}>
              {status === "ingesting" ? <><span className="spinner" /> Indexing transcript</> : "Process video →"}
            </button>
          </form>

          <div className="importPanel"><h3>Explore a channel</h3><p>Index recent videos and search its catalog for more relevant transcripts.</p></div>
          <form onSubmit={processChannel}>
            <label htmlFor="channel">Channel URL or @handle</label>
            <input
              id="channel"
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
              placeholder="https://youtube.com/@channel"
              disabled={status === "ingesting"}
            />
            <div className="rangeRow">
              <label htmlFor="videos">Initial videos to index</label>
              <output>{maxVideos}</output>
            </div>
            <input
              id="videos"
              type="range"
              min="1"
              max="10"
              value={maxVideos}
              onChange={(event) => setMaxVideos(Number(event.target.value))}
              disabled={status === "ingesting"}
            />
            <small className="rangeHint">The channel catalog stays searchable; relevant older transcripts load automatically.</small>
            <button className="primary" disabled={!workspace || status === "ingesting" || status === "booting"}>
              {status === "ingesting" ? <><span className="spinner" /> Indexing transcripts</> : "Process channel →"}
            </button>
          </form>

          <div className="importPanel">
            <h3>Import a channel or playlist export</h3>
            <p>Choose a ChannelMind ZIP or JSON file. Every exported transcript will be indexed for questions.</p>
            <input type="file" accept=".zip,.json,application/zip,application/json" onChange={importTranscriptFile} disabled={status === "ingesting" || status === "booting"} aria-label="Import ChannelMind transcript export" />
            {importJob && status === "error" && <button type="button" onClick={resumeImport}>Resume import</button>}
          </div>

          <div className="statusCard" data-state={status}>
            <span className="statusIcon">{status === "ready" ? "✓" : status === "error" ? "!" : "•"}</span>
            <div>
              <strong>{status === "ready" ? "Knowledge base ready" : status === "ingesting" ? "Processing videos" : status === "booting" ? "Creating secure workspace" : status === "error" ? "Action needed" : "Ready to process"}</strong>
              <small>{status === "error" ? error : status === "ready" ? (metrics.videos > 0 ? `${metrics.catalog} catalog videos · ${metrics.videos} transcripts ready${importedKind === "video" && transcriptSource ? ` · ${transcriptSource === "local_speech_recognition" ? "local speech recognition" : "YouTube captions"}` : ""}` : `${metrics.catalog} catalog videos · transcripts load when you ask`) : "Each session is isolated and expires automatically."}</small>
            </div>
          </div>

          <div className="privacyNote"><span>⌁</span><p><strong>Isolated by design</strong>Your session has its own vector namespace and signed access token.</p></div>
        </aside>

        <section className="chatPanel">
          <div className="panelHeading chatHeading">
            <span className="step">02</span>
            <div><h2>Ask the {importedChannel ? importedKind : "channel"}</h2><p>Answers include verified timestamp sources.</p></div>
            <div className="metrics"><span>{metrics.catalog}<small>catalog</small></span><span>{metrics.videos}<small>transcripts</small></span><span>{metrics.chunks}<small>chunks</small></span></div>
          </div>

          <div className="conversation" aria-live="polite">
            {messages.length === 0 && (
              <div className="emptyState">
                <div className="orb">⌕</div>
                <h3>{status === "ready" ? `Your ${importedChannel ? importedKind : "channel"} is ready` : "Answers that show their work"}</h3>
                <p>{status === "ready" ? "Ask anything. Relevant older videos are found and indexed automatically." : "Process a channel to search its ideas, tactics, and explanations."}</p>
                <div className="suggestions">
                  {suggestions.map((item) => (
                    <button key={item} disabled={status !== "ready"} onClick={(event) => ask(event, item)}>{item}<span>↗</span></button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message, index) => (
              <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
                <div className="avatar">{message.role === "assistant" ? "CM" : "YOU"}</div>
                <div className="bubble">
                  <div className="messageLabel">{message.role === "assistant" ? "ChannelMind" : "You"}</div>
                  <div className="answerText">{message.content}</div>
                  {message.sources && message.sources.length > 0 && (
                    <div className="sources">
                      <strong>Verified moments</strong>
                      {message.sources.map((source) => (
                        <a href={source.url} target="_blank" rel="noreferrer" key={`${source.url}-${source.timestamp}`}>
                          <span className="play">▶</span><span>{source.title}</span><time>{source.timestamp}</time>
                        </a>
                      ))}
                    </div>
                  )}
                  {message.usage && <div className="usage">{message.usage.total_tokens.toLocaleString()} tokens · {message.usage.input_tokens} in / {message.usage.output_tokens} out</div>}
                </div>
              </article>
            ))}
            {answering && <div className="thinking"><span /><span /><span /> {answerStage === "catalog" ? "Finding relevant videos" : "Writing a grounded answer"}</div>}
            <div ref={bottomRef} />
          </div>

          {error && <div className="errorBanner" role="alert">{error}</div>}
          <form className="composer" onSubmit={ask}>
            <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={status === "ready" ? "Ask a question about the whole channel…" : "Process a channel to begin…"} disabled={status !== "ready" || answering} maxLength={1000} />
            <button aria-label="Send question" disabled={status !== "ready" || answering || !question.trim()}>↑</button>
          </form>
        </section>
      </section>

      <footer><span>Built with FastAPI, Qdrant, Redis and Next.js.</span><span>Answers are generated from public transcript data.</span></footer>
    </main>
  );
}
