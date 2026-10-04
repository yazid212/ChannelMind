"use client";

import { useEffect, useState } from "react";
import { api, Transcript, Workspace } from "@/lib/api";

type Props = {
  workspace: Workspace;
  videoId: string;
  initialTime: number;
  onClose: () => void;
};

export default function TranscriptReader({ workspace, videoId, initialTime, onClose }: Props) {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [seekTime, setSeekTime] = useState(initialTime);

  useEffect(() => {
    let active = true;
    api.transcript(workspace, videoId)
      .then((result) => { if (active) setTranscript(result); })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [workspace, videoId]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const needle = query.trim().toLocaleLowerCase();
  const chunks = transcript?.chunks.filter((chunk) => chunk.text.toLocaleLowerCase().includes(needle)) ?? [];
  const playerUrl = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?start=${Math.max(0, Math.floor(seekTime))}`;

  return (
    <div className="transcriptOverlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="transcriptReader" role="dialog" aria-modal="true" aria-label="Video transcript">
        <header className="transcriptHeader">
          <div><small>{transcript?.transcript_source === "local_speech_recognition" ? "Local speech recognition" : transcript?.transcript_source === "youtube_captions" ? "YouTube captions" : "Indexed transcript"}</small><h2>{transcript?.title || "Video transcript"}</h2></div>
          <button type="button" className="transcriptClose" onClick={onClose} aria-label="Close transcript">×</button>
        </header>
        <div className="transcriptBody">
          <div className="transcriptPlayer">
            <iframe key={playerUrl} src={playerUrl} title="YouTube video" allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture; web-share" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
            <a href={`https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}&t=${Math.floor(seekTime)}s`} target="_blank" rel="noreferrer">Open video on YouTube ↗</a>
          </div>
          <div className="transcriptContent">
            <label htmlFor="transcriptSearch">Search this transcript</label>
            <input id="transcriptSearch" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find spoken words…" autoFocus />
            {error && <p className="transcriptNotice" role="alert">{error}</p>}
            {!transcript && !error && <p className="transcriptNotice">Loading transcript…</p>}
            {transcript && <p className="transcriptCount">{needle ? `${chunks.length} of ${transcript.chunks.length} passages match` : `${chunks.length} indexed passages`}</p>}
            <div className="transcriptChunks">
              {transcript && chunks.length === 0 && <p className="transcriptNotice">No matching passage in this transcript.</p>}
              {chunks.map((chunk, index) => (
                <article className="transcriptChunk" key={`${chunk.start_time}-${index}`}>
                  <button type="button" onClick={() => setSeekTime(chunk.start_time)} title={`Play from ${chunk.timestamp}`}>{chunk.timestamp} ▶</button>
                  <p>{chunk.text}</p>
                </article>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
