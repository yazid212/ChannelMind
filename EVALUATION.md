# Answer quality evaluation

The fixed cases are in [evaluation/day3_cases.json](evaluation/day3_cases.json). They use one public video, two answerable transcript questions, one unanswerable transcript question, and one separate general-knowledge check. The script records retrieval recall, citation-passage support, answerability, latency, token usage, and estimated provider cost without saving credentials or full transcripts.

Run against the local API (ingests the source video and makes four paid model calls):

```powershell
.venv\Scripts\python.exe scripts/evaluate_day3.py
```

For a previously indexed local workspace, skip ingestion:

```powershell
.venv\Scripts\python.exe scripts/evaluate_day3.py --reuse-workspace-id <workspace-id>
```

The report is written to `data/evaluations/day3_latest.json`. Cost is an **estimate** from the provider's current model listing and reported input/output tokens; it is not a billing receipt. Retrieval recall checks whether the expected phrases appear in retrieved passages. Citation support checks the displayed source passages, not every factual claim in the prose answer. The small one-video set is a regression starting point, not evidence of general accuracy.

| Local run, 2026-10-08 | Retrieval recall | Citation passage support | Answerability | Estimated cost, four calls |
| --- | ---: | ---: | ---: | ---: |
| Three retrieved chunks | 1/2 | 1/2 | 3/3 | $0.00071775 |
| Four retrieved chunks | 2/2 | 2/2 | 3/3 | $0.00082245 |

The relevant “study the analytics” passage ranked fourth for one question. Increasing `TOP_K` from 3 to 4 recovered it in the fixed set. The four-chunk run used approximately 15% more estimated model cost across the four calls. General-knowledge mode was labeled and returned zero video citations. Results may differ with transcription wording, model response, and provider pricing; rerun the set after retrieval, prompt, or model changes.

## Multi-part playlist regression, 2026-10-09

Manual questions against the 23-video Python tutorial playlist revealed that named-video retrieval reserved slots for loosely related titles. The Functions answer omitted the passage defining parameters and arguments from its citations. The sorting answer said the price-key implementation was unavailable even though it appears later in the transcript. Title routing now favors a matching title phrase and gives a single clearly named video up to four chunks. Citation markers are rendered from stored source metadata so the displayed times cannot be mistyped by the model. A live rerun retrieved and cited the Functions passages at 00:03 and 02:31, and the sorting passages at 00:03, 01:18, 02:36 and 03:53. The full Python suite passed with 111 tests. These are two focused regression checks, not a general playlist-quality score.

The user's six-question browser run then showed supported answers and citations for Functions, control flow, sorting, map/filter, and machine-learning/data-cleaning questions. The first cross-video `enumerate()` versus browser Back answer cited both videos but misstated which page Back displays. The transcript says the current page is popped and the previous page is displayed. After clarifying sequence reasoning in the transcript prompt, a live rerun and the user's browser retry both stated that correctly and cited both videos. All six manual questions now pass this local check; model answers still require passage checks for important claims.
