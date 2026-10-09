# Architecture and security model

## Components

| Component | Responsibility | Deployment |
|---|---|---|
| Next.js frontend | Input, job polling, chat, citation display | Vercel |
| FastAPI API | Validation, workspace authorization, limits, chat orchestration | Container web service |
| RQ worker | Collection discovery, YouTube transcription, chunking, embedding, vector writes | Container worker |
| Redis | Job durability, status, distributed rate limiting | Managed Redis/Valkey |
| Qdrant | Tenant-filtered transcript vectors | Qdrant Cloud or managed Qdrant |
| LLM provider | Grounded answer generation | OpenRouter, Groq, xAI, or NVIDIA |

The frontend never receives YouTube or LLM provider credentials. It receives only an expiring capability token scoped to a random workspace UUID.

## Tenant isolation

Every Qdrant point contains a hashed `workspace_id` payload. Every search and deletion operation constructs its workspace filter internally; the API never accepts a vector filter from the client. Local Chroma uses a separate hashed collection and directory per workspace.

Each workspace contains two explicitly separated vector document types. `catalog` points hold lightweight public title and description metadata for channel discovery. `transcript` points hold timestamped text and are the only documents permitted into answer generation.

## Collection ingestion and retrieval

1. Playlist and channel ingestion discovers all public entries through the YouTube Data API and records them in the catalog.
2. The worker processes each video with public captions or local speech recognition, then indexes its transcript and publishes progress.
3. Questions can run while ingestion continues, using the transcript chunks indexed so far.
4. Answer retrieval runs only over transcript chunks, so catalog descriptions are never treated as evidence or cited.

Large collections can take hours when caption requests are blocked and audio must be transcribed locally. The coverage report distinguishes indexed, unavailable, failed, and pending entries.

RQ jobs store their owning workspace in job metadata. Job polling returns `404` when the capability and job owner do not match, which avoids confirming another tenant's job identifiers.

## Trust boundaries

The Chrome extension's public-channel mode accepts a channel URL or handle, discovers public uploads with the YouTube Data API, and fetches accessible public transcripts without owner consent. The optional owned-channel mode uses Google OAuth authorization code flow with PKCE and single-use state bound to its workspace. Google tokens remain encrypted in the backend store, which uses Redis in production and a local SQLite file during native development. Only configured extension IDs can start authorization. Export status, checkpoints and caption text expire and are encrypted using a domain-separated key derived from `APP_SECRET`.

The worker lists channels available through `channels.list(mine=true)`, downloads authorized captions, and saves per-video progress. Quota exhaustion pauses the run; completion does not imply every video had captions. TXT/JSON/ZIP downloads require the owning workspace token. The extension's alarm downloads a completed ZIP even when its dashboard is closed.

The website also accepts the exported ZIP or JSON and validates its manifest, video IDs, segments and timestamps before indexing every successful transcript in a new workspace. Choosing **Use in ChannelMind** builds a separate search workspace directly from the server export. A 60-second single-use handoff in the browser fragment transfers that search capability to the website; it cannot access the original workspace's Google grant. See [extension/README.md](extension/README.md) for installation, quota and recovery limits.

1. Browser input is untrusted and constrained with Pydantic lengths, types, and a YouTube host allowlist.
2. Workspace capabilities are HMAC-signed, expire automatically, and are compared in constant time.
3. Transcript text is untrusted third-party content. It is wrapped as quoted context and the model is explicitly instructed never to follow embedded instructions.
4. Provider errors are logged by exception class only; request bodies, prompts, response bodies, tokens, and keys are not logged.
5. The API container runs as a non-root user and receives secrets only through runtime environment variables.

## Abuse controls

Default limits are intentionally conservative:

- Workspace creation: 10 per source address per hour
- Ingestion: 3 per source address per hour
- Chat: 20 per source address per minute
- On-demand expansion: 20 per source address per hour
- Channel metadata catalog: at most 2,000 videos by default
- Request body: 32 KiB
- Workspace lifetime: 24 hours
- RQ timeout: 15 minutes for a channel catalog job, up to 2 hours for one video, and up to 1 hour for an extension export job

Production refuses to start without a 32-character `APP_SECRET` and Redis. Exact CORS origins are required; wildcard origins are rejected.

## Data lifecycle

Qdrant points include the workspace expiry timestamp. `python -m api.cleanup` deletes expired points and should run hourly. A visitor can also explicitly clear their workspace through the authenticated `DELETE` endpoint.

Transcript disk caching is disabled by default in the production worker. When enabled locally, cache writes use atomic replacement to avoid partially written JSON.

## Failure behavior

- Ingestion runs outside the API request lifecycle and reports only `queued`, `started`, `finished`, or `failed`.
- Internal exception details are not sent to browsers.
- Expired, malformed, cross-tenant, and missing capabilities fail closed.
- LLM answers use a bounded output budget and only return citations actually present in the answer.

## Remaining risks

- LLM prompt-injection resistance is best-effort, not a formal sandbox.
- Rate limits identify anonymous clients by network address; authenticated accounts would provide stronger quotas.
- Reverse-proxy client IPs require a verified trusted ingress configuration. Until then, all visitors behind one edge address may share the same limit.
- Provider and YouTube quotas are external dependencies and require monitoring.
- Qdrant backup, Redis availability, WAF rules, alerting, and secret rotation are deployment-platform responsibilities.
- Dynamic application scanning and a live multi-user load test have not been run.
