# Live YouTube ingestion and answer checks

Tested on 2026-10-06 with the local Windows API, RTX 3050 Laptop GPU (4 GB),
and `faster-whisper` small. The first runs used one GPU worker; repeat runs
used two. Each ingestion run used a fresh workspace. Times include catalog lookup, retrieval or speech
recognition, and vector indexing; question time is separate.

| Source | Public YouTube link | Indexed | First run | Caption-blocked repeat |
| --- | --- | ---: | ---: | ---: |
| Video, 4:06 | [Your First Python Error](https://www.youtube.com/watch?v=PU8SXzg3n88) | 1/1 | 10.1 s | Forced local speech recognition: 20.5 s |
| Playlist, six videos totaling about 27 minutes | [Python 3 for Absolute Beginners](https://www.youtube.com/playlist?list=PLRK31CzD-J6O3omf6Qr8k_w5g3vNr8DQW) | 6/6 | 40.1 s | 90.8 s for 5/6, then the transient HTTP 403 entry succeeded on retry |
| Channel, six uploads totaling about 10 minutes | [Python Without Panic](https://www.youtube.com/channel/UC5IjLuHUFBoB_svW7KORyCA) | 6/6 | 30.1 s | 50.4 s, 6/6 using local speech recognition after caption requests were blocked |

The first runs used YouTube captions. Later YouTube blocked caption requests
from this IP. The GPU fallback kept the channel complete. One playlist audio
download returned HTTP 403; a later retry indexed it. The downloader now
re-extracts and retries transient HTTP 403, 429, and 5xx failures up to twice.

The forced speech-recognition run on the 4:06 video produced 427 transcript
words. Its answer listed the five error-checking steps and cited the lesson at
01:38. The full transcription, indexing, and answer request took 52.7 s.

Answer checks:

- The video's Python-error answer cited two relevant transcript chunks.
- The playlist's casting and variable answers were grounded in the matching
  lessons. A citation matcher fix now recognizes shortened long video titles;
  the variable answer returned a source in a live retest.
- The channel distinguished `SyntaxError` from `NameError` with a source.
- An unrelated JavaScript-promises question now returns insufficient evidence
  and no sources, instead of a generic answer unrelated to the videos.

These timings demonstrate the short-source path. A long playlist or channel
with hours of audio and no captions cannot finish in two or three minutes on
this hardware. Download availability and YouTube rate limits also change the
total. The raw local results are in `data/benchmarks/`; the repeatable test
driver is `scripts/benchmark_live_sources.py`.
