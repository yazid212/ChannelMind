# Manual test cases for Days 2 and 3

Open the local site at http://127.0.0.1:3000. Use the **YouTube URL or @handle** field and **Process source →** button. Run the same cases again against the staging site before public release. Record a screenshot of the final status and **Collection coverage** panel for each collection.

For each run, record: date, site URL (local or staging), source URL, final status, found, indexed, unavailable, failed, pending, and any failure reason. Never record workspace tokens or API keys.

## TC1 — Single public video

**Input:** `https://www.youtube.com/watch?v=PU8SXzg3n88`

1. Paste the URL and click **Process source →**.
2. Wait for **video knowledge base ready**.
3. Click **Browse searchable transcript →**.

**Pass:** One catalog video, one transcript, more than zero chunks, and readable timestamped transcript text. **Fail:** A job error, zero transcripts, or a transcript that cannot be opened.

**Current evidence:** A different public video indexed with one transcript and 18 chunks on 2026-10-08. TC1's exact URL has not been manually checked in the site.

## TC2 — Answer and timestamp citation

**Input:** Any video that passed TC1. Ask a question whose answer you can verify in its transcript. For the video titled *I've Made $500K with faceless instagram Carousels using AI*, ask: “What two engagement metrics does the speaker identify as viral triggers, and what target percentages does he recommend?”

1. Submit the question in **Ask the video**.
2. Compare the answer with the cited **Transcript sources** passage.
3. Click **Read transcript**, then **Watch moment**.

**Pass:** The answer states shares above 4% and saves at least 7% for the example video; the cited passage contains both claims; the transcript opens at the passage; the YouTube link seeks to its timestamp. **Fail:** Unsupported numbers, missing citation, wrong passage, or broken links.

**Current evidence:** Answer and cited passage were checked on 2026-10-08. Link clicks still need manual confirmation.

## TC3 — Public playlist and recovery of failed entries

**Input:** `https://www.youtube.com/playlist?list=PLRK31CzD-J6O3omf6Qr8k_w5g3vNr8DQW`

1. Paste the playlist URL and click **Process source →**.
2. Confirm the UI says **Processing playlist** and shows **Collection coverage**.
3. While processing, check that **pending** decreases and indexed transcripts can be used for questions.
4. At completion, check that **indexed + unavailable + failed = found** and **pending = 0**.
5. If failures remain, open **Videos not indexed** and record each title and reason. Click **Process source →** again with the same URL to retry missing videos; completed transcripts should be reused.
6. For this Python tutorial playlist, ask: “According to the Python Functions video, why should a large program be split into functions?” Check that the answer cites the *Python Functions* transcript. An Instagram carousel question is outside this playlist and should receive an insufficient-information answer in **Transcript only** mode.

**Pass:** The job processes beyond 10 entries, keeps completed transcripts, and accounts for every entry. For videos that YouTube lets this connection download, the transcript count increases on retry. A remaining **YouTube requested bot verification** reason means that video could not be accessed from this connection; record it as an external access limit.

**Current evidence:** The 2026-10-08 retry finished with 23 found, 23 transcribed, 23 indexed, and zero unavailable, failed or pending (user screenshot at 23:38). Local collection inspection found Python tutorial titles and no carousel or Reels text. The earlier run had seven YouTube bot challenges; alternate audio clients recovered those entries on retry.

## TC4 — Original 32-video regression

**Input:** The original 32-video playlist used in the earlier manual check. Its URL was not saved in the project notes. If unavailable, use a public playlist with at least 32 visible entries and record the URL.

1. Process the playlist and let it finish. It may take hours when local speech recognition is needed.
2. Reload the same browser tab once while it runs and confirm progress resumes.
3. Check final coverage and open the list of videos not indexed.

**Pass:** Processing continues past the former 10-video cap; **pending = 0**; **indexed + unavailable + failed = found**; every missing transcript has a reason. If all 32 entries are accessible and contain speech, expect 32 indexed transcripts. **Fail:** Silent stop at 10, lost progress on reload, or missing entries without reasons.

## TC5 — Channel URL

**Input:** `https://www.youtube.com/channel/UC5IjLuHUFBoB_svW7KORyCA`

1. Process the URL and confirm the UI says **Processing channel**.
2. Check that discovered entries appear in coverage and progress updates.
3. Ask about an indexed video while the rest process.

**Pass:** The channel is recognized, indexing continues beyond 10 accessible entries, questions use indexed transcripts, and final coverage accounts for every discovered entry. A large channel may take hours.

## TC6 — Caption-free video

**Input:** A public video you have confirmed has no YouTube captions. Record its URL.

1. Process the direct video URL.
2. Wait for **video knowledge base ready**.
3. Ask one question and open its cited transcript passage.

**Pass:** One transcript is indexed, the source says **local speech recognition**, and the answer has a matching citation. **Fail:** No transcript, or a generic error without a useful cause.

## TC7 — Transcript-only answer boundary

**Input:** Any indexed video. Leave **Answer source** set to **Transcript only**. Ask: “What does this video say about JavaScript promises?” if the video does not cover that subject.

1. Confirm **Transcript only** is selected before submitting.
2. Read the answer and inspect any **Transcript sources** shown below it.

**Pass:** The answer says the transcript does not establish the requested information and does not invent a video claim or timestamp. **Fail:** A general JavaScript explanation presented as if the speaker said it, or a source passage unrelated to the claim.

## TC8 — General knowledge label and source separation

**Input:** Select **General knowledge** and ask: “What is a JavaScript promise?”

1. Confirm the answer begins with **General knowledge (not from video transcripts)**.
2. Confirm there is no **Transcript sources** citation card for that answer.
3. Switch back to **Transcript only** and ask a question about the indexed video; confirm the new answer has a relevant transcript citation.

**Pass:** General knowledge is labeled and has no video citations; switching back restores transcript-grounded answers. **Fail:** General information is attributed to the video, a video citation appears in general mode, or the mode switch leaves earlier conversation context attached to the new mode.

## Before public release

Repeat TC1–TC8 on the deployed staging site. Also run the health, workspace isolation, extension, rate limit, backup restore, and billing alert checks in [DEPLOYMENT.md](DEPLOYMENT.md). The Day 2 milestone is not complete until the playlist retests and deployed source checks pass; see [BUILD_PLAN.md](BUILD_PLAN.md).
