# ChannelMind — YouTube RAG with timestamp citations

ChannelMind turns a public YouTube channel into a searchable knowledge base. It extracts transcripts, builds a tenant-isolated vector index, and answers questions with links to the exact video moments used as evidence.

This repository contains the web app, API, ingestion worker, channel/playlist exporter extension, and the original Streamlit prototype.

## Why this project exists

Long-form creator content contains useful knowledge, but finding one explanation across dozens of videos is slow. ChannelMind makes that content queryable while keeping answers auditable: retrieval and generation use the same chunks, and only citations that appear in the final answer are displayed.

## Production architecture

```mermaid
flowchart LR
    U[Browser] -->|HTTPS| W[Next.js on Vercel]
    W -->|signed workspace token| A[FastAPI API]
    A --> R[(Redis)]
    A -->|enqueue ingestion / expansion| Q[RQ Worker]
    Q --> Y[YouTube captions and audio]
    Q --> S[Bundled local Whisper model]
    Q --> E[Local MiniLM embeddings]
    E --> V[(Qdrant Cloud)]
    A --> V
    A --> L[OpenRouter / Groq / xAI / NVIDIA]
    L --> A
```

- `web/` — responsive Next.js frontend designed for Vercel.
- `api/` — FastAPI boundary, signed anonymous workspaces, validation, rate limits, job status, and health checks.
- `src/` — extraction, chunking, retrieval, LLM routing, and citation logic.
- Redis + RQ — durable background ingestion, separated from web requests.
- Qdrant — one shared collection partitioned by a mandatory tenant payload.
- Chroma — local-development fallback and legacy Streamlit storage.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for trust boundaries and design decisions.

## Highlights

- Video-first indexing: paste one public video URL, use its free captions when available, or recognize speech locally from downloaded audio
- Exact timestamp citations linked back to YouTube
- Multi-video retrieval with cross-video diversification
- Isolated, expiring workspaces for public visitors
- Background channel ingestion with job polling
- Hybrid retrieval: catalog the channel once, index a small recent set, then fetch relevant older transcripts on demand
- Chrome extension for whole public-channel transcript exports, resumable progress, automatic ZIP download, and file import into ChannelMind; optional owner-authorized caption mode
- Fixed-window abuse limits backed by Redis in production
- Named, configurable LLM models and token telemetry
- Prompt-injection boundary around untrusted transcript text
- Atomic transcript caching for local use
- Non-root multi-stage API container
- Pinned Python and JavaScript dependencies
- Python tests, frontend build checks, secret scanning, SAST, and dependency audits in CI

## Local development

Prerequisites: Python 3.11+, Node.js 22+, a YouTube Data API key for channel workflows, and an LLM provider key for Q&A. Processing a single public video does not require the YouTube Data API key.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-api.txt -r requirements-dev.txt
Copy-Item .env.example .env
```

Add the credentials you use to `.env`. The video URL field accepts a standard watch URL, short URL, Shorts URL, live URL, or 11-character video ID. It reads public captions first; if none are available, `yt-dlp` downloads audio and `faster-whisper` recognizes speech locally. The Whisper model downloads once to `LOCAL_WHISPER_MODEL_PATH` (default `data/models`) in local development. The deployed API image bundles this model and Node 22 for YouTube audio extraction.

Audio is processed in 15-minute pieces, then the timestamped text is indexed. The default multilingual `small` model runs on CPU, so long videos can take hours. YouTube access is still required to fetch audio, and some private, restricted, or blocked videos cannot be processed. Q&A uses the configured external LLM provider.

Download the embedding model once if it is not already cached:

```powershell
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
```

Then start the API:

```powershell
uvicorn api.main:app --reload --port 8000
```

In a second terminal:

```powershell
cd web
Copy-Item .env.example .env.local
npm ci
npm run dev
```

Open `http://localhost:3000`. Development defaults use tenant-separated local Chroma collections and an in-process background executor, so Redis and Qdrant are optional locally.

To run the API, worker, Redis and Qdrant together for local development, use Docker Compose. Start the frontend separately with `npm run dev` in `web/`. Compose uses local development settings; it is not the public deployment configuration.

```powershell
docker compose up --build
```

The legacy interface remains available with:

```powershell
streamlit run app/app.py
```

## Verification

```powershell
python -m pytest tests -q -p no:cacheprovider
python -m compileall -q api app src config.py
cd web
npm run typecheck
npm run build
cd ..
node --test extension/tests/*.test.mjs
```

Export tests use simulated Google/YouTube responses; a live public export still requires a configured YouTube API key and an installed-extension browser check. A passing unit suite does not replace a deployed video, Q&A, extension and caption-free transcription smoke test.

## Deployment

For the channel and playlist exporter extension, follow [extension/README.md](extension/README.md). Public channel and playlist exports require a YouTube Data API key and an allowed extension ID, but not channel-owner OAuth consent. The optional owned-channel mode requires a Google OAuth Web application client. The extension package can be loaded unpacked locally; it has not been published to an extension store.

The recommended v1 setup is Vercel for `web/`, Railway for the API, worker and private Redis, and Qdrant Cloud Standard for vectors. Follow the configuration, smoke checks and rollout steps in [DEPLOYMENT.md](DEPLOYMENT.md). No cloud deployment is implied by the local tests.

## API surface

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/healthz` | Container health and non-sensitive runtime mode |
| `GET` | `/readyz` | Production queue, vector database and local model readiness |
| `POST` | `/api/v1/workspaces` | Issue an expiring signed workspace capability |
| `POST` | `/api/v1/workspaces/{id}/videos` | Queue one public video for caption or local speech indexing |
| `POST` | `/api/v1/workspaces/{id}/ingestions` | Queue channel processing |
| `POST` | `/api/v1/workspaces/{id}/expansions` | Find and index relevant older videos for a question |
| `GET` | `/api/v1/jobs/{id}` | Poll tenant-scoped ingestion status |
| `POST` | `/api/v1/workspaces/{id}/chat` | Retrieve context and generate a cited answer |
| `DELETE` | `/api/v1/workspaces/{id}` | Delete the workspace vector partition |

Production API documentation is disabled by default. It remains available at `/docs` during local development.

## Known trade-offs

- Local MiniLM embeddings reduce API cost but make API and worker images larger.
- Transcript availability depends on YouTube captions and upstream API behavior.
- Cloud datacenter IPs are often blocked by YouTube transcript endpoints; the API supports optional rotating Webshare residential proxy credentials for reliable hosted ingestion.
- Whole-channel discovery uses titles and descriptions; a poorly titled video can still be missed until it is directly indexed.
- Anonymous capability tokens provide workspace isolation, not user accounts or long-term history.
- Generated answers can still be wrong; timestamp citations make verification easier but do not guarantee truth.
- The Docker image and cloud services still need verification on the chosen host before a public launch. Local Docker was unavailable during this preparation, so the image has not been built here.

## License

MIT — see [LICENSE](LICENSE).
