# ChannelMind public channel and playlist transcript exporter

Enter a public channel URL, @handle, or playlist URL to collect every accessible public transcript in one resumable job. The ZIP downloads automatically while Chrome is running, even if you close the export tab. TXT and JSON downloads are also available. You do not open individual videos.

Public export uses the YouTube Data API to discover channel uploads or public playlist items and the public transcript extractor to retrieve accessible captions. It does not require a channel owner's Google consent. Videos without accessible captions are listed as skipped. It does not download video/audio or generate speech recognition transcripts. The separate **Export a channel you own with Google** option still uses owner-authorized captions.

## Install locally

1. In Chrome, open `chrome://extensions`, turn on **Developer mode**, and choose **Load unpacked**.
2. Select this repository's `extension` directory (or unzip the release package and select its folder).
3. Copy the extension ID. Click the extension icon to open its dashboard. Expand **Connection settings & setup** and save your API and website origins.

Chrome 116+ is supported. Edge may support these Chromium APIs, but has not been verified. No extension-store listing or developer account is required for local unpacked use. Public export needs `YOUTUBE_API_KEY` in the API's `.env` and the extension ID in `CHANNELMIND_EXTENSION_IDS` so the API accepts requests from this Chrome extension. Google OAuth setup below is only for the optional owned-channel mode.

For local development, install `requirements-api.txt`, run `uvicorn api.main:app --reload --port 8000`, and set the extension API address to `http://127.0.0.1:8000`. Run the website from `web/` with `npm ci` and `npm run dev` to import a downloaded ZIP. Keep the API running during collection. If it restarts, reopen the extension and resume the saved export.

## Optional: configure Google for a channel you own

1. Create/select a Google Cloud project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen. In testing mode, add your Google account as a test user. Add the scope `https://www.googleapis.com/auth/youtube.force-ssl`.
3. Create an OAuth client of type **Web application**. The secret stays on the API server, not in the extension.
4. Register this exact authorized redirect URI, substituting the extension ID:

   `https://YOUR_EXTENSION_ID.chromiumapp.org/oauth2`

5. Configure the API and worker with the same environment values:

   ```dotenv
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   CHANNELMIND_EXTENSION_IDS=your-32-character-extension-id
   APP_SECRET=a-long-random-secret-at-least-32-characters
   EXPORT_RETENTION_SECONDS=86400
   WORKSPACE_TTL_SECONDS=86400
   ```

   Generate the secret locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Put real values only in the ignored `.env` or your host's secret settings. Never publish them or place them in `NEXT_PUBLIC_*` variables.

6. Install API requirements and restart the API. For production, configure Redis; API and worker must share Redis and `APP_SECRET`. Run the existing `rq worker ... ingestion` worker. Use TLS for external Redis and keep it private.
7. In the extension settings, save your API origin and ChannelMind website origin. Development defaults are `http://127.0.0.1:8000` and `http://localhost:3000`. Grant access to the API when Chrome asks.

Google's caption-download scope includes broad YouTube management permissions. The implementation only uses read endpoints and does not edit or delete YouTube content. Public distribution may require Google OAuth verification and Chrome Web Store review. Changes to the extension ID require an updated redirect URI and allowlist entry.

## Use

- Enter any public channel URL or @handle and click **Export public channel**. No owner sign-in is required. The backend discovers all public upload pages and collects one accessible caption track per video. Human captions are preferred; a preferred language can be selected, with fallback to another existing track.
- Enter a public playlist URL, a YouTube watch URL containing `list=`, or a playlist ID and click **Export public playlist**. Videos may come from different channels. Pagination, checkpoints, automatic retries, ZIP/JSON/TXT downloads, partial ZIP recovery, and ChannelMind Q&A work as they do for channel exports. Private or inaccessible playlists cannot be exported through this public mode.
- For a channel you own, expand the Google section, click **Connect YouTube**, complete consent, select the channel, and click **Export owned channel**.
- Watch counts of exported and unavailable transcripts. Closing the extension page does not stop the worker. The background extension checks completion once a minute and starts the ZIP download; browser download settings can still prompt for a save location.
- Download **TXT**, **JSON**, or a partial **ZIP** from a paused or stopped job. On completion the ZIP is downloaded once automatically. If a browser download is interrupted, use the manual download button to retry.
- Open the website and choose the downloaded ZIP or JSON under **Import a channel or playlist export**. Every successfully exported transcript is indexed, including videos beyond the initial 10-video setting. The website can also be opened directly with **Use in ChannelMind**, using a short-lived, single-use handoff. Questions use the imported transcripts without calling the public transcript scraper.

## Export contents

```text
ChannelMind-COLLECTION_ID.zip
  channel.txt                 All video titles, URLs and timestamped text
  channel.json                Structured segments, languages and per-video status
  README.txt
  transcripts/VIDEO_ID.txt     One file for each successfully exported video
```

JSON uses `schema: channelmind.channel-transcripts`, `version: 1`. Playlist bundles include `channel.kind: "playlist"` and `source: "youtube-public-playlist-transcripts"`; older channel bundles remain compatible. Segment timestamps are in seconds, with fractional precision preserved. A manifest entry remains for each discovered video, including missing, inaccessible or pending transcripts. `complete` means all discovered videos were processed; it does not mean every video had captions. Imports accept finished ZIP or JSON bundles up to 60 MB.

## Quota and recovery

Public export uses YouTube Data API quota for channel discovery; transcript retrieval is separate and can be blocked or rate limited by YouTube, especially from cloud hosts. The job pauses and can be resumed. Owner-authorized caption listing costs 50 YouTube quota units per video and caption download costs 200 per track. Roughly 100 owner-authorized videos with one track each require **25,000 units plus channel discovery**.

The worker checkpoints catalog pagination and every completed video. Quota exhaustion pauses the job with an explanation; after quota is restored, click **Resume export**. Temporary transcript failures are retried by the extension after increasing delays, up to three times; you can also resume manually. Completed transcripts are reused. Reconnect Google if authorization expires, then resume. Caption tracks that are permanently missing or forbidden are reported and skipped.

If you already downloaded an incomplete ZIP or JSON export, select it under **Continue an older partial export**. The extension reuses its saved transcripts and checks the remaining uploads. A partial file has `complete: false` and pending video entries in `channel.json`; the ZIP's README also labels it as partial. Keep your original ZIP until the new export finishes.

An export has a 50 MB structured transcript budget and a one-hour production worker timeout. A killed production worker's lease expires after 62 minutes; resume then continues from its last checkpoint. Export and OAuth data expire with the workspace (24 hours by default, or earlier if `EXPORT_RETENTION_SECONDS` is shorter for exports). Download valuable results before they expire. Local mode stores encrypted export checkpoints in `data/channel-exports.sqlite3`, so API restarts preserve them within their TTL. Production uses Redis.

## Verification and packaging

```powershell
python -m pytest tests/test_channel_exports.py -q
cd extension
npm test
cd ..
powershell -ExecutionPolicy Bypass -File scripts/package-extension.ps1
```

The package is created at `dist/channelmind-extension.zip`. Tests simulate Google/YouTube responses and cover authorization state, replay, tenant isolation, quota recovery, caption parsing, exports and the import handoff. A live OAuth run requires your configured Google project and account consent.

Official references: [Chrome identity](https://developer.chrome.com/docs/extensions/reference/api/identity), [Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [caption list](https://developers.google.com/youtube/v3/docs/captions/list), [caption download](https://developers.google.com/youtube/v3/docs/captions/download).
