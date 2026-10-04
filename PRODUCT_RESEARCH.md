# ChannelMind product and skill research

Research date: 2026-10-03. This is a feature benchmark based on public product documentation, not a claim that competitors' advertised coverage or accuracy has been independently verified.

## What ChannelMind already has

The repository supports one-video ingestion using captions and local speech recognition, channel cataloging with on-demand transcript indexing, timestamped answer links, resumable channel/playlist caption exports in the extension, ZIP/JSON imports, and isolated workspaces. See [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [extension/README.md](extension/README.md). The current web chat displays source titles and timestamps, but not the cited transcript passage; the web app has no transcript reader or source-scope selector. Public playlist processing currently starts in the extension rather than the website.

## Comparable products and ideas to adapt

| Product | Publicly documented behavior | ChannelMind opportunity |
| --- | --- | --- |
| [Ask Channel AI](https://askchannel.ai/features) | Per-video source cards with exact quotes and clickable timestamps; transcript phrase search; cross-channel answers; full channel imports | Add quote-level source cards and direct transcript search first. Treat cross-channel collections as a later architecture change. |
| [Video Highlight](https://videohighlight.com/) | Searchable transcripts, timestamped chat, playlists/channels, highlights and notes | Add a transcript reader tied to citations, then consider notes after core Q&A works reliably. |
| [Glasp](https://glasp.co/youtube-summary) | Transcript, summary, chat, and highlights beside the video; timestamp navigation and multiple export formats | Put the transcript next to the player and let a citation seek within it. Existing ChannelMind exports already cover basic downloads. |
| [Yutome](https://app.getyutome.com/) | One input accepts video, playlist, channel, or handle; cross-library spoken-word search and API access | Unify website input and expose clear source scope. Defer permanent cross-library search until the current 24-hour workspace design changes. |
| [Gemini Notebook / NotebookLM](https://support.google.com/gemininotebook/answer/16215270) | Select individual sources for a question; public YouTube URL import uses caption text | Add source selection. ChannelMind's local speech fallback remains valuable for caption-free video URLs. |
| [DomeScribe](https://domescribe.ai/guides/ask-questions-about-a-video) | Timestamp citations and explicit refusal when the transcript cannot answer | Make video-only versus labeled general-knowledge answers an explicit user choice; show when evidence is absent. |

An [MIT-licensed interactive transcript player](https://github.com/hyperaudio/hyperaudio-lite) demonstrates click-to-seek, search, and shareable timed passages. Its examples use word-level timing, while ChannelMind currently stores caption/speech segments. A segment-level reader is the smaller initial implementation; assess the library separately before adding it. An [MIT-licensed RAG evaluation harness](https://github.com/camposvinicius/rag-evals) illustrates offline retrieval metrics and regression gates, but ChannelMind needs its own labeled video questions and expected evidence before any gate is meaningful.

## Feature order

Implementation status (2026-10-04): items 1 and 2 now have source passage cards, a workspace-scoped transcript API, and a searchable per-video reader with playback seeking. Broader collection status, unified URL routing, explicit answer mode, and evaluation data remain planned.

1. **Evidence people can inspect:** return the supporting segment text and stable video ID/time for each cited source, then show quote cards that seek the video. Validate each displayed quote against stored transcript data rather than trusting generated citation text.
2. **Transcript visibility:** add an authenticated, tenant-scoped transcript endpoint and a searchable reader for one video. Show caption versus local-recognition provenance and coverage. Do not expose another workspace's transcript.
3. **Collection accountability:** display discovered, indexed, unavailable, pending, and failed counts in the website; surface resumable export/import status. Reuse the existing export manifest and worker checkpoints.
4. **One input for three scopes:** accept video, playlist, and channel URLs in the website, route each to the existing safe workflow, and show the selected scope. A native playlist workflow must share video ingestion rather than inventing a second transcription stack.
5. **Answer control and evaluation:** let the user choose transcript-only or clearly labeled general-knowledge answers. Test answerability, retrieval recall, quote support, latency, and cost against a labeled set before adding reranking, multi-query, or graders.
6. **Later:** highlights, notes, summaries, persistent libraries, multi-channel comparison, API/MCP access, upload alerts, and supercuts. These add storage, account, moderation, or compute requirements beyond the current anonymous 24-hour workspace.

## Skills and integrations

| Capability | Best fit | Status / condition |
| --- | --- | --- |
| RAG diagnosis and pattern selection | `rag-architecture` | Installed in the author's personal Codex setup; use when the labeled questions show a retrieval or grounding problem. |
| Repository and release security | `security-guardian` | Installed in the author's personal Codex setup; use for CI, secrets, tenant boundaries, and production readiness. |
| Browser walkthroughs | computer-use; optionally official [Playwright skill](https://github.com/openai/skills/tree/main/skills/.curated/playwright) | Computer Use is available when a browser is connected. The Playwright skill needs a compatible local CLI/runtime before installation is useful. |
| GitHub CI inspection | GitHub integration; optionally official [gh-fix-ci skill](https://github.com/openai/skills/tree/main/skills/.curated/gh-fix-ci) | GitHub integration is available to connect. `gh` CLI was not present at the last check. |
| Web deployment and logs | Vercel and Railway integrations | Available to connect; neither is confirmed connected. |
| Production errors | Official [Sentry skill](https://github.com/openai/skills/tree/main/skills/.curated/sentry) | Optional after Sentry is configured; no Sentry project is evidenced in this repository. |
| OpenAI model or API changes | `openai-docs` | Available; use only if ChannelMind adopts or changes an OpenAI integration. |
| Discover additional skills or account tools | `skill-installer` and `plugin-management` | Available; use for a specific missing capability, not indiscriminate installs. |
| New project-specific workflow | `skill-creator` | Available locally. Create a `channelmind-evaluation` skill only after the labeled corpus and measurable release rules exist. |

The official [transcribe skill](https://github.com/openai/skills/tree/main/skills/.curated/transcribe) calls a paid external API and is not a replacement for ChannelMind's free local speech-recognition path. Additional creator-analytics or video-generation skills do not improve the current transcript-to-answer flow.

GitHub, Vercel, and Railway account integrations are discoverable but require connection before use. The repository and public documentation can still be researched and changed without those integrations; live deployment inspection needs the corresponding account access.

## First acceptance checks

- For a known answerable video question, each source card contains a real passage from the indexed transcript, an exact time, and a working seek link.
- For an unanswerable question, the response states whether the transcript lacks evidence and never presents model knowledge as a video citation.
- For a playlist/channel, discovered count equals indexed + unavailable + pending + failed; resuming does not duplicate completed transcripts.
- A second workspace cannot read transcript text, quotes, jobs, or source selection from the first.
- Compare each change to a fixed local evaluation set before claiming better retrieval or broader coverage.
