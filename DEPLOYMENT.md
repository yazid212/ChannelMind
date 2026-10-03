# ChannelMind v1 deployment

The recommended v1 layout is **Vercel for `web/`, Railway for the API, ingestion worker and private Redis, and Qdrant Cloud Standard for vectors**. The API and worker use the same `Dockerfile.api`; only the API gets a public domain. This keeps long audio transcription out of web requests and lets the frontend deploy independently. Railway runs each Compose service as a separate service rather than running `docker-compose.yml` directly: [Railway Compose guide](https://docs.railway.com/guides/docker-compose). Vercel supports a `web/` project root: [Vercel Git deployment](https://vercel.com/docs/git).

The Qdrant free cluster is useful for staging, but it suspends after inactivity and has no production backup schedule. Use Standard for a public v1 and enable backups: [Qdrant cluster choices](https://qdrant.tech/documentation/cloud/create-cluster/).

## 0. Push the source to GitHub

The target repository is `https://github.com/yazid212/ChannelMind`. It had no remote branch when checked on 2026-10-03. In PowerShell, from this project's root:

```powershell
cd C:\Users\piome\PycharmProjects\youtube_rag
git init -b main
git check-ignore -v .env web/.env.local data/ vectorstore/ .venv/ .codex/
git add .
git diff --cached --name-only
git diff --cached --check
```

Inspect the staged file list before committing. Never commit `.env`, `data/`, `vectorstore/`, model caches, ZIP exports or local transcripts. The ignored files above should not appear in the staged list. If they do, remove them from the Git index before proceeding and investigate why. Keep the repository private during setup and rotate any credential that was ever published. Then run:

```powershell
git commit -m "Prepare ChannelMind v1"
git remote add origin https://github.com/yazid212/ChannelMind.git
git push -u origin main
```

If Git requests an author identity, set `git config user.name "Your Name"` and `git config user.email "you@example.com"` for this repository, then retry the commit. Complete GitHub's browser sign-in if prompted. Confirm the `main` branch and GitHub Actions checks appear in the repository.

## Before creating cloud services

1. Run the release checks from the README. Build `Dockerfile.api` on a Linux builder; Railway can do this remotely if Docker Desktop is unavailable. The image bundles Node 22 for YouTube's JavaScript challenge and the local Whisper `small` model for caption-free videos. The build downloads both embedding and speech models and therefore needs network access and several gigabytes of disk.
2. Generate a fresh signing secret locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Put it in the hosting secret store, never in a file committed to Git. The API and worker must use the same value.

## 1. Data services

- Create one **private Redis** service on Railway. Use its internal `REDIS_URL`; do not enable a public TCP proxy. Attach persistent storage, enable persistence/backups and verify a restore before launch. Railway's Redis template is self-managed, so its backups and updates are your responsibility: [Railway Redis guide](https://docs.railway.com/databases/redis).
- Create a **Qdrant Cloud Standard** cluster in the closest practical region. Create an API key and a backup schedule. Set `QDRANT_URL` to its HTTPS endpoint and `QDRANT_API_KEY` in Railway. Keep Redis, API, worker and Qdrant in nearby regions to avoid needless latency.

## 2. API on Railway

Create a service from this repository. Set `RAILWAY_DOCKERFILE_PATH=Dockerfile.api`, `PORT=8000`, one replica, a public HTTPS domain and health check path `/readyz`. Railway's health check uses the host `healthcheck.railway.app`, so include it in `ALLOWED_HOSTS`: [Railway health checks](https://docs.railway.com/deployments/healthchecks). Start with 1–2 GB RAM for the API, measure actual use, and increase if model loading requires it.

Set these variables in Railway's secret/variable UI:

```env
APP_ENV=production
APP_SECRET=<new-random-secret-shared-with-worker>
ALLOWED_ORIGINS=https://app.example.com
ALLOWED_HOSTS=api.example.com,healthcheck.railway.app
TRUST_PROXY_HEADERS=false
REDIS_URL=<private-railway-redis-url>
VECTOR_BACKEND=qdrant
QDRANT_URL=https://<your-qdrant-endpoint>
QDRANT_API_KEY=<your-qdrant-key>
YOUTUBE_API_KEY=<your-youtube-data-api-key>
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<your-openrouter-key>
OPENROUTER_MODEL=openai/gpt-4o-mini
EMBEDDING_LOCAL_FILES_ONLY=true
ENABLE_TRANSCRIPT_CACHE=false
```

The API refuses to start in production if required credentials or safe host/origin settings are missing. `TRUST_PROXY_HEADERS=false` avoids trusting client-supplied forwarding headers during staging. Behind Railway's HTTP edge, this may make all visitors share the same application IP rate-limit bucket. **Before a public launch**, inspect the API's observed peer addresses, identify and allow only the ingress source CIDRs you actually trust, then set `TRUST_PROXY_HEADERS=true` and `TRUSTED_PROXY_CIDRS=<verified-CIDRs>`. Railway documents `X-Real-IP` as its client address header: [Railway public networking](https://docs.railway.com/networking/public-networking/specs-and-limits). The code also accepts the last `X-Forwarded-For` address from a trusted proxy. Verify with two separate client networks that quotas are independent and that a forged header cannot change the bucket. If the ingress cannot supply a stable, verifiable trust boundary, use an edge rate limit before public access. Set API request and model-credit limits at the edge as well as in the app.

## 3. Worker and cleanup on Railway

Create a second service from the same repository and Dockerfile. Give it **no public domain** and set its start command to `python -m api.worker`. Share `APP_ENV`, `APP_SECRET`, `REDIS_URL`, `VECTOR_BACKEND`, `QDRANT_URL`, `QDRANT_API_KEY`, `YOUTUBE_API_KEY`, `EMBEDDING_LOCAL_FILES_ONLY` and `ENABLE_TRANSCRIPT_CACHE` with the API. The worker does not need the OpenRouter key. Start with about **2 vCPU, 4 GB RAM and enough temporary disk for downloaded audio**, then size it from a caption-free long-video test. This is an initial sizing estimate, not a guarantee for every video.

Create a third service using the same image with start command `python -m api.cleanup` and cron schedule `0 * * * *` (UTC). It needs `VECTOR_BACKEND=qdrant`, `QDRANT_URL` and `QDRANT_API_KEY`; it removes expired anonymous vectors. Railway cron services must exit after a run: [Railway cron guide](https://docs.railway.com/cron-jobs).

## 4. Frontend on Vercel

Import the same Git repository, choose `web` as the project root and set:

```env
NEXT_PUBLIC_API_URL=https://api.example.com
NEXT_PUBLIC_REPOSITORY_URL=https://github.com/<owner>/<repository>
```

Deploy the frontend, then put its exact HTTPS origin in the API's `ALLOWED_ORIGINS`. `NEXT_PUBLIC_API_URL` is baked into browser JavaScript at build time, so redeploy `web/` after changing it. Do not put API, YouTube, Qdrant or model keys in any `NEXT_PUBLIC_` variable.

## 5. Extension

In the installed extension's settings, change its API origin to the production API and its app origin to the production frontend. Set `CHANNELMIND_EXTENSION_IDS` on the API for the packaged extension ID. Public channel and playlist exports need the YouTube key; owner-authorized mode additionally needs the Google OAuth client setup in [extension/README.md](extension/README.md). Package and review the extension separately before any store publication.

## Release smoke checks

1. Confirm `/healthz` and `/readyz` return 200, and that API and worker logs show a Redis connection. `/readyz` also checks Qdrant and the bundled speech model. It does not spend LLM credit. Verify the per-client IP rate limit as described above.
2. In a private browser window, process one public video. Confirm job completion, transcript source and chunk count. Ask one short question and open its timestamp citation. Test a caption-free video separately to exercise local speech recognition; cloud hosts can be blocked by YouTube, so this must be checked from the deployed worker.
3. Test one channel and one playlist export, then import the result. Confirm a second private window cannot read the first window's job or vector results.
4. Check that expired workspaces disappear after cleanup; restore a Redis and Qdrant backup in staging; set OpenRouter and Railway usage alerts before opening public access.

Do not market v1 as able to process every YouTube video. Private/restricted videos, blocked cloud IPs, unavailable audio, very long CPU jobs and provider outages remain real limits. Monitor failed-job reasons and queue time. If cloud YouTube access is unreliable, keep the launch small until that path is proven on the chosen host.

For rollback, deploy the previous API and worker image together and roll back the Vercel frontend independently. Keep Redis and Qdrant backups before changing their schemas or retention settings.
