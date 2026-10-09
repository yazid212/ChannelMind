"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { api, ChatResult, ExportCoverage, ExportImport, ExportIssue, JobResult, Source, Usage, Workspace } from "@/lib/api";
import { detectSource, SourceScope } from "@/lib/source";
import TranscriptReader from "./transcript-reader";

type Status = "booting" | "idle" | "ingesting" | "ready" | "error";
type AnswerMode = "transcript" | "general";
type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  usage?: Usage;
};
type SavedWorkspace = {
  workspace: Workspace;
  metrics: { catalog: number; videos: number; chunks: number };
  sourceUrl: string;
  channel?: string;
  videoUrl?: string;
  importedChannel: string;
  importedKind: SourceScope;
  transcriptSource: "youtube_captions" | "local_speech_recognition" | "";
  videoId?: string;
  coverage?: ExportCoverage | null;
  issues?: ExportIssue[];
  issueTotal?: number;
  warning?: string;
  pendingJobId?: string;
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

const reasonLabel = (reason: string) => reason === "youtube_access_blocked"
  ? "YouTube requested bot verification from this connection"
  : reason.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " ");

export default function Home() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [status, setStatus] = useState<Status>("booting");
  const [sourceUrl, setSourceUrl] = useState("");
  const [transcriptSource, setTranscriptSource] = useState<"youtube_captions" | "local_speech_recognition" | "">("");
  const [videoId, setVideoId] = useState("");
  const [selectedSource, setSelectedSource] = useState<{ videoId: string; startTime: number } | null>(null);
  const [question, setQuestion] = useState("");
  const [answerMode, setAnswerMode] = useState<AnswerMode>("transcript");
  const [messages, setMessages] = useState<Message[]>([]);
  const [metrics, setMetrics] = useState({ catalog: 0, videos: 0, chunks: 0 });
  const [coverage, setCoverage] = useState<ExportCoverage | null>(null);
  const [reportJob, setReportJob] = useState<{ workspace: Workspace; id: string } | null>(null);
  const [issueItems, setIssueItems] = useState<ExportIssue[]>([]);
  const [issueTotal, setIssueTotal] = useState(0);
  const [loadingIssues, setLoadingIssues] = useState(false);
  const [error, setError] = useState("");
  const [resultWarning, setResultWarning] = useState("");
  const [processingMessage, setProcessingMessage] = useState("");
  const [answering, setAnswering] = useState(false);
  const [answerStage, setAnswerStage] = useState<"catalog" | "answer">("catalog");
  const [importedChannel, setImportedChannel] = useState("");
  const [importedKind, setImportedKind] = useState<"video" | "channel" | "playlist">("channel");
  const [activeScope, setActiveScope] = useState<SourceScope | null>(null);
  const [importJob, setImportJob] = useState<{ workspace: Workspace; id: string } | null>(null);
  const initialization = useRef<Promise<{ workspace: Workspace; imported?: ExportImport; saved?: SavedWorkspace; notice?: string }> | null>(null);
  const resumedJob = useRef(false);
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
            notice = "This extension export link has expired or was already used. Create a fresh link from the extension to import it; you can still process a YouTube URL here.";
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
          setCoverage(imported.coverage || null);
          if (imported.report_id) setReportJob({ workspace: value, id: imported.report_id });
          setSourceUrl(imported.channel.kind === "playlist" ? `https://www.youtube.com/playlist?list=${imported.channel.id}` : `https://www.youtube.com/channel/${imported.channel.id}`);
          setImportedChannel(imported.channel.title);
          setImportedKind(imported.channel.kind === "playlist" ? "playlist" : "channel");
          setActiveScope(imported.channel.kind === "playlist" ? "playlist" : "channel");
          setStatus("ready");
        } else if (saved) {
          setMetrics(saved.metrics);
          setSourceUrl(saved.sourceUrl || saved.videoUrl || saved.channel || "");
          setImportedChannel(saved.importedChannel);
          setImportedKind(saved.importedKind);
          setActiveScope(saved.importedKind);
          setTranscriptSource(saved.transcriptSource);
          setVideoId(saved.videoId || "");
          setCoverage(saved.coverage || null);
          setIssueItems(saved.issues || []);
          setIssueTotal(saved.issueTotal || 0);
          setResultWarning(saved.warning || "");
          setStatus(saved.pendingJobId ? "ingesting" : "ready");
          if (saved.pendingJobId && !resumedJob.current) {
            resumedJob.current = true;
            trackSourceJob(value, saved.sourceUrl, saved.importedKind, saved.pendingJobId)
              .catch((reason) => {
                setError(reason instanceof Error ? reason.message : "Unable to continue processing.");
                setStatus("error");
              });
          }
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

  useEffect(() => {
    if (!reportJob) return;
    let active = true;
    api.exportStatus(reportJob.workspace, reportJob.id).then((result) => {
      if (!active) return;
      setCoverage(result.coverage);
      setIssueItems(result.issues);
      setIssueTotal(result.issue_total);
    }).catch(() => { /* The export report can expire before the workspace. */ });
    return () => { active = false; };
  }, [reportJob]);

  async function waitForJob(jobId: string, target = workspace, maxAttempts = 300): Promise<JobResult> {
    if (!target) throw new Error("Workspace is not ready.");
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await pause(2000);
      let job: Awaited<ReturnType<typeof api.job>>;
      try {
        job = await api.job(target, jobId);
      } catch (error) {
        if (error instanceof TypeError || (error instanceof DOMException && error.name === "AbortError")) {
          setProcessingMessage("Connection to the API was interrupted. Retrying; indexed videos are saved.");
          continue;
        }
        throw error;
      }
      if (job.result?.message) setProcessingMessage(job.result.message);
      if (job.result?.coverage) {
        setCoverage(job.result.coverage);
        setIssueItems(job.result.issues || []);
        setIssueTotal(job.result.issue_total || 0);
        setMetrics({
          catalog: job.result.catalog_count ?? job.result.coverage.discovered,
          videos: job.result.video_count ?? job.result.coverage.indexed,
          chunks: job.result.chunk_count ?? 0,
        });
        if (job.result.title) setImportedChannel(job.result.title);
        try {
          const saved = JSON.parse(sessionStorage.getItem("channelmind-session") || "null") as SavedWorkspace | null;
          if (saved?.pendingJobId === jobId) saveWorkspaceSession({
            ...saved,
            metrics: {
              catalog: job.result.catalog_count ?? job.result.coverage.discovered,
              videos: job.result.video_count ?? job.result.coverage.indexed,
              chunks: job.result.chunk_count ?? 0,
            },
            importedChannel: job.result.title || saved.importedChannel,
            coverage: job.result.coverage,
            issues: job.result.issues,
            issueTotal: job.result.issue_total,
          });
        } catch { /* Storage is optional. */ }
      }
      if (job.status === "failed") {
        throw new Error(job.result?.message || "Background processing failed. Please try again.");
      }
      if (job.status === "finished") return job.result ?? {};
    }
    throw new Error("Background processing timed out.");
  }

  async function trackSourceJob(target: Workspace, url: string, scope: SourceScope, jobId: string) {
    const maxAttempts = scope === "video" ? 3600 : 41400;
    try {
      const result = await waitForJob(jobId, target, maxAttempts);
      completeSource(target, url, scope, result);
    } catch (reason) {
      if (scope === "video" || !(reason instanceof Error) || reason.message !== "Job not found") throw reason;
      setProcessingMessage("Continuing indexed transcripts after the server restart.");
      const queued = scope === "playlist" ? await api.ingestPlaylist(target, url) : await api.ingest(target, url);
      try {
        const saved = JSON.parse(sessionStorage.getItem("channelmind-session") || "null") as SavedWorkspace | null;
        if (saved?.workspace.workspace_id === target.workspace_id) {
          saveWorkspaceSession({ ...saved, pendingJobId: queued.job_id });
        }
      } catch { /* Storage is optional. */ }
      const result = await waitForJob(queued.job_id, target, maxAttempts);
      completeSource(target, url, scope, result);
    }
  }

  function completeSource(target: Workspace, url: string, scope: SourceScope, result: JobResult) {
    const nextMetrics = {
      catalog: result.catalog_count ?? (scope === "video" ? 1 : 0),
      videos: result.video_count ?? (scope === "video" ? 1 : 0),
      chunks: result.chunk_count ?? 0,
    };
    const title = result.title || `YouTube ${scope}`;
    setMetrics(nextMetrics);
    setCoverage(result.coverage || null);
    setIssueItems(result.issues || []);
    setIssueTotal(result.issue_total || 0);
    setResultWarning(result.warning || "");
    setImportedChannel(title);
    setVideoId(scope === "video" ? result.video_id || "" : "");
    setTranscriptSource(scope === "video" ? result.transcript_source || "youtube_captions" : "");
    saveWorkspaceSession({ workspace: target, metrics: nextMetrics, sourceUrl: url,
      importedChannel: title, importedKind: scope,
      transcriptSource: scope === "video" ? result.transcript_source || "youtube_captions" : "",
      videoId: scope === "video" ? result.video_id : undefined,
      coverage: result.coverage, issues: result.issues, issueTotal: result.issue_total,
      warning: result.warning });
    setStatus("ready");
    setProcessingMessage("");
  }

  async function processSource(event: FormEvent) {
    event.preventDefault();
    if (!workspace || !sourceUrl.trim()) return;
    setError("");
    setResultWarning("");
    setProcessingMessage("");
    let scope: SourceScope;
    try { scope = detectSource(sourceUrl); }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : "Use a public YouTube URL.");
      setStatus("error");
      return;
    }
    setActiveScope(scope);
    setStatus("ingesting");
    setMessages([]);
    setSelectedSource(null);
    setVideoId("");
    setCoverage(null);
    setReportJob(null);
    setIssueItems([]);
    setIssueTotal(0);
    setMetrics({ catalog: 0, videos: 0, chunks: 0 });
    try {
      let saved: SavedWorkspace | null = null;
      try { saved = JSON.parse(sessionStorage.getItem("channelmind-session") || "null") as SavedWorkspace | null; }
      catch { /* Storage is optional. */ }
      const sameCollection = saved?.workspace.workspace_id === workspace.workspace_id &&
        saved.sourceUrl.trim() === sourceUrl.trim() && saved.importedKind === scope;
      const target = status === "idle" || sameCollection ? workspace : await api.createWorkspace();
      setWorkspace(target);
      setImportedChannel("");
      setImportedKind(scope);
      setTranscriptSource("");
      sessionStorage.removeItem("channelmind-import");
      sessionStorage.removeItem("channelmind-session");
      const queued = scope === "video" ? await api.ingestVideo(target, sourceUrl.trim())
        : scope === "playlist" ? await api.ingestPlaylist(target, sourceUrl.trim())
        : await api.ingest(target, sourceUrl.trim());
      saveWorkspaceSession({ workspace: target, metrics: { catalog: 0, videos: 0, chunks: 0 },
        sourceUrl, importedChannel: "", importedKind: scope, transcriptSource: "",
        pendingJobId: queued.job_id });
      await trackSourceJob(target, sourceUrl, scope, queued.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Unable to process this ${scope}.`);
      setStatus("error");
    }
  }

  async function finishImport(target: Workspace, id: string) {
    let current = await api.exportStatus(target, id);
    setCoverage(current.coverage);
    setIssueItems(current.issues);
    setIssueTotal(current.issue_total);
    for (let attempt = 0; attempt < 1800 && !current.indexed; attempt += 1) {
      if (["paused", "cancelled", "interrupted"].includes(current.status)) {
        throw new Error(current.message || "Transcript import was interrupted.");
      }
      await pause(2000);
      current = await api.exportStatus(target, id);
      setCoverage(current.coverage);
      setIssueItems(current.issues);
      setIssueTotal(current.issue_total);
    }
    if (!current.indexed || !current.metrics) throw new Error("Transcript import did not finish.");
    setMetrics(current.metrics);
    setSourceUrl(current.channel.kind === "playlist" ? `https://www.youtube.com/playlist?list=${current.channel.id}` : `https://www.youtube.com/channel/${current.channel.id}`);
    setImportedChannel(current.channel.title);
    setImportedKind(current.channel.kind === "playlist" ? "playlist" : "channel");
    setActiveScope(current.channel.kind === "playlist" ? "playlist" : "channel");
    setResultWarning("");
    setReportJob({ workspace: target, id });
    sessionStorage.setItem("channelmind-import", JSON.stringify({
      workspace_id: target.workspace_id, token: target.token,
      expires_at: Date.parse(target.expires_at) / 1000,
      channel: current.channel, metrics: current.metrics,
      coverage: current.coverage, report_id: id,
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
    setSelectedSource(null);
    setVideoId("");
    setCoverage(null);
    setReportJob(null);
    setIssueItems([]);
    setIssueTotal(0);
    setMetrics({ catalog: 0, videos: 0, chunks: 0 });
    try {
      if (file.size > 60 * 1024 * 1024) throw new Error("Choose an export smaller than 60 MB.");
      const target = await api.createWorkspace();
      const started = await api.uploadExport(target, file);
      setWorkspace(target);
      setImportJob({ workspace: target, id: started.id });
      setCoverage(started.coverage);
      setIssueItems(started.issues);
      setIssueTotal(started.issue_total);
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

  async function loadMoreIssues() {
    if (!reportJob || loadingIssues || issueItems.length >= issueTotal) return;
    setLoadingIssues(true);
    try {
      const page = await api.exportIssues(reportJob.workspace, reportJob.id, issueItems.length);
      setIssueItems((current) => [...current, ...page.items]);
      setIssueTotal(page.total);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load the video report.");
    } finally {
      setLoadingIssues(false);
    }
  }

  async function ask(event: FormEvent, suggestedQuestion?: string) {
    event.preventDefault();
    const nextQuestion = (suggestedQuestion ?? question).trim();
    if (!workspace || !canAsk || !nextQuestion || answering) return;
    const previous = messages.map(({ role, content }) => ({ role, content }));
    setMessages((current) => [...current, { role: "user", content: nextQuestion }]);
    setQuestion("");
    setAnswering(true);
    setAnswerStage("catalog");
    setError("");
    try {
      if (answerMode === "transcript" && !importedChannel && !coverage) {
        const queued = await api.expand(workspace, nextQuestion);
        const expansion = await waitForJob(queued.job_id);
        setMetrics((current) => ({
          ...current,
          videos: current.videos + (expansion.added_video_count ?? 0),
          chunks: current.chunks + (expansion.chunk_count ?? 0),
        }));
      }
      setAnswerStage("answer");
      const result: ChatResult = await api.chat(workspace, nextQuestion, previous, answerMode);
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

  const canAsk = (answerMode === "general" || metrics.videos > 0) && (status === "ready" ||
    ((status === "ingesting" || status === "error") && (activeScope === "playlist" || activeScope === "channel")));

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
            <div><h2>{status === "ready" ? `${(activeScope || importedKind)[0].toUpperCase()}${(activeScope || importedKind).slice(1)} ready` : "Build a knowledge base"}</h2><p>{importedChannel || "Start with a public video, playlist, or channel."}</p></div>
          </div>
          <form onSubmit={processSource}>
            <label htmlFor="source">YouTube URL or @handle</label>
            <input
              id="source"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="Video, playlist, or channel URL"
              disabled={status === "ingesting"}
            />
            <small className="rangeHint">Every video in a playlist or channel is processed. You can ask about indexed videos while the rest continue. When captions are unavailable, local speech recognition takes longer.</small>
            <button className="primary" disabled={!workspace || status === "ingesting" || status === "booting"}>
              {status === "ingesting" ? <><span className="spinner" /> Processing {activeScope || "source"}</> : "Process source →"}
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
              <strong>{status === "ready" ? `${activeScope || importedKind} knowledge base ready` : status === "ingesting" ? `Processing ${activeScope || "source"}` : status === "booting" ? "Creating secure workspace" : status === "error" ? "Action needed" : "Ready to process"}</strong>
              {status === "ingesting" && processingMessage && <small>{processingMessage}</small>}
              <small>{status === "error" ? error : status === "ready" ? (metrics.videos > 0 ? `${metrics.catalog} catalog videos · ${metrics.videos} transcripts ready${importedKind === "video" && transcriptSource ? ` · ${transcriptSource === "local_speech_recognition" ? "local speech recognition" : "YouTube captions"}` : ""}` : `${metrics.catalog} catalog videos · ${importedKind === "channel" ? "transcripts load when you ask" : "no transcripts indexed"}`) : "Each session is isolated and expires automatically."}</small>
            </div>
          </div>
          {status === "ready" && resultWarning && <p className="resultWarning">{resultWarning}</p>}
          {status === "ingesting" && canAsk && <p className="resultWarning">You can ask about the {metrics.videos} transcripts indexed so far. Answers may change as more videos finish.</p>}
          {status === "ready" && importedKind === "video" && videoId && (
            <button type="button" className="browseTranscript" onClick={() => setSelectedSource({ videoId, startTime: 0 })}>Browse searchable transcript →</button>
          )}

          {coverage && (
            <div className="coveragePanel" aria-label="Collection coverage">
              <strong>Collection coverage</strong>
              <p>{coverage.discovered} found · {coverage.transcribed} transcribed · {coverage.indexed} indexed</p>
              <p>{coverage.unavailable} unavailable · {coverage.failed} failed · {coverage.pending} pending</p>
              {coverage.reasons.length > 0 && (
                <ul className="coverageReasons">
                  {coverage.reasons.map(({ reason, count }) => <li key={reason}><span>{reasonLabel(reason)}</span><b>{count}</b></li>)}
                </ul>
              )}
              {issueItems.length > 0 && (
                <details className="coverageIssues"><summary>Videos not indexed ({issueTotal})</summary>
                  <ul>{issueItems.map((issue, index) => <li key={`${issue.video_id}-${index}`}>{issue.title} · {issue.outcome}: {reasonLabel(issue.reason)}</li>)}</ul>
                  {reportJob && issueItems.length < issueTotal && <button type="button" onClick={loadMoreIssues} disabled={loadingIssues}>{loadingIssues ? "Loading…" : "Show more videos"}</button>}
                </details>
              )}
            </div>
          )}

          <div className="privacyNote"><span>⌁</span><p><strong>Isolated by design</strong>Your session has its own vector namespace and signed access token.</p></div>
        </aside>

        <section className="chatPanel">
          <div className="panelHeading chatHeading">
            <span className="step">02</span>
            <div><h2>Ask the {activeScope || importedKind}</h2><p>{answerMode === "transcript" ? "Answers link to their transcript passages." : "General knowledge answers are separate from video evidence."}</p></div>
            <div className="metrics"><span>{metrics.catalog}<small>catalog</small></span><span>{metrics.videos}<small>transcripts</small></span><span>{metrics.chunks}<small>chunks</small></span></div>
          </div>

          <fieldset className="answerMode" disabled={answering}>
            <legend>Answer source</legend>
            <label><input type="radio" name="answer-mode" checked={answerMode === "transcript"} onChange={() => { setAnswerMode("transcript"); setMessages([]); setError(""); }} /> Transcript only</label>
            <label><input type="radio" name="answer-mode" checked={answerMode === "general"} onChange={() => { setAnswerMode("general"); setMessages([]); setError(""); }} /> General knowledge</label>
            <small>{answerMode === "transcript" ? "Answers use indexed transcripts and cite their passages." : "Model knowledge is labeled and has no video citations."}</small>
          </fieldset>

          <div className="conversation" aria-live="polite">
            {messages.length === 0 && (
              <div className="emptyState">
                <div className="orb">⌕</div>
                <h3>{canAsk ? `Ask the ${activeScope || importedKind}` : "Answers that show their work"}</h3>
                <p>{answerMode === "general" ? "Ask a general question. This mode does not use or cite video transcripts." : canAsk ? (status === "ingesting" ? "Questions use the transcripts indexed so far while the collection continues." : "Ask about the transcripts indexed in this session.") : "Process a YouTube video, playlist, or channel to search its ideas."}</p>
                <div className="suggestions">
                  {(answerMode === "general" ? [] : suggestions).map((item) => (
                    <button key={item} disabled={!canAsk} onClick={(event) => ask(event, item)}>{item}<span>↗</span></button>
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
                      <strong>Transcript sources</strong>
                      {message.sources.map((source) => (
                        <div className="sourceCard" key={`${source.video_id}-${source.start_time}`}>
                          <div className="sourceHeading"><span>{source.title}</span><time>{source.timestamp}</time></div>
                          <p>{source.quote}</p>
                          <div className="sourceActions">
                            <button type="button" onClick={() => setSelectedSource({ videoId: source.video_id, startTime: source.start_time })}>Read transcript</button>
                            <a href={source.url} target="_blank" rel="noreferrer">Watch moment ↗</a>
                          </div>
                        </div>
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
            <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={answerMode === "general" ? "Ask using general knowledge…" : canAsk ? `Ask about ${metrics.videos} indexed transcripts…` : "Process a YouTube source to begin…"} disabled={!canAsk || answering} maxLength={1000} />
            <button aria-label="Send question" disabled={!canAsk || answering || !question.trim()}>↑</button>
          </form>
        </section>
      </section>

      {workspace && selectedSource && <TranscriptReader workspace={workspace} videoId={selectedSource.videoId} initialTime={selectedSource.startTime} onClose={() => setSelectedSource(null)} />}

      <footer><span>Built with FastAPI, Qdrant, Redis and Next.js.</span><span>Answers are generated from public transcript data.</span></footer>
    </main>
  );
}
