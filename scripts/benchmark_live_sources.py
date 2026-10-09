"""Time live YouTube ingestion and inspect transcript-grounded answers.

Run against the local API: python scripts/benchmark_live_sources.py
The report contains public source URLs and answers, never workspace tokens.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8000/api/v1"
EXAMPLES = {
    "video": {
        "url": "https://www.youtube.com/watch?v=PU8SXzg3n88",
        "endpoint": "videos",
        "field": "video",
        "inspect": ["PU8SXzg3n88"],
        "questions": [
            "What should a beginner do when Python reports an error?",
            "Does this lesson explain JavaScript promises?",
        ],
    },
    "playlist": {
        "url": "https://www.youtube.com/playlist?list=PLRK31CzD-J6O3omf6Qr8k_w5g3vNr8DQW",
        "endpoint": "playlists",
        "field": "playlist",
        "inspect": ["SV9Vr4NdwrU", "wy7LBvaJsTU"],
        "questions": [
            "What is type casting in Python?",
            "What is a Python variable?",
        ],
    },
    "channel": {
        "url": "https://www.youtube.com/channel/UC5IjLuHUFBoB_svW7KORyCA",
        "endpoint": "ingestions",
        "field": "channel",
        "inspect": ["fEWppkIhfRM", "v9HBb5_23MA"],
        "questions": [
            "What is the difference between SyntaxError and NameError?",
            "Does this channel teach JavaScript promises?",
        ],
    },
}


def request_json(method, url, **kwargs):
    response = requests.request(method, url, timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()


def benchmark(kind, example):
    workspace = request_json("POST", f"{BASE}/workspaces")
    workspace_id = workspace["workspace_id"]
    headers = {"Authorization": f"Bearer {workspace['token']}"}
    root = f"{BASE}/workspaces/{workspace_id}"
    started = time.perf_counter()
    job = request_json(
        "POST", f"{root}/{example['endpoint']}",
        headers=headers, json={example["field"]: example["url"]},
    )
    while True:
        status = request_json("GET", f"{BASE}/jobs/{job['job_id']}", headers=headers)
        result = status.get("result") or {}
        coverage = result.get("coverage", {})
        print(
            f"{kind}: {time.perf_counter() - started:.1f}s "
            f"{status['status']} {coverage.get('indexed', 0)}/"
            f"{coverage.get('discovered', '?')}", flush=True,
        )
        if status["status"] in {"finished", "failed"}:
            break
        if time.perf_counter() - started > 900:
            raise TimeoutError(f"{kind} exceeded the 15-minute benchmark limit")
        time.sleep(10)
    elapsed = round(time.perf_counter() - started, 1)
    transcripts = []
    for video_id in example["inspect"]:
        try:
            transcript = request_json(
                "GET", f"{root}/videos/{video_id}/transcript", headers=headers,
            )
            transcripts.append({
                "video_id": video_id,
                "source": transcript["transcript_source"],
                "chunks": len(transcript["chunks"]),
                "sample": " ".join(chunk["text"] for chunk in transcript["chunks"])[:1200],
            })
        except requests.HTTPError as error:
            transcripts.append({"video_id": video_id, "error": str(error)})
    answers = []
    for question in example["questions"]:
        asked = time.perf_counter()
        try:
            response = request_json(
                "POST", f"{root}/chat", headers=headers,
                json={"question": question, "history": []},
            )
            answers.append({
                "question": question,
                "seconds": round(time.perf_counter() - asked, 1),
                "answer": response["answer"],
                "sources": response["sources"],
                "retrieved_chunks": response["retrieved_chunks"],
            })
        except requests.HTTPError as error:
            answers.append({"question": question, "error": str(error)})
    return {
        "url": example["url"],
        "workspace_id": workspace_id,
        "seconds": elapsed,
        "status": status["status"],
        "result": result,
        "transcripts": transcripts,
        "answers": answers,
    }


def benchmark_forced_asr():
    """Exercise the real GPU fallback even when this video has captions."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.embedder import Embedder
    from src.local_transcriber import transcribe_video_audio

    example = EXAMPLES["video"]
    workspace = request_json("POST", f"{BASE}/workspaces")
    workspace_id = workspace["workspace_id"]
    headers = {"Authorization": f"Bearer {workspace['token']}"}
    started = time.perf_counter()
    transcript = transcribe_video_audio("PU8SXzg3n88")
    transcribed_seconds = round(time.perf_counter() - started, 1)
    Embedder(workspace_id=workspace_id).ingest([transcript])
    question = "What are the steps in the lesson's error routine?"
    asked = time.perf_counter()
    answer = request_json(
        "POST", f"{BASE}/workspaces/{workspace_id}/chat", headers=headers,
        json={"question": question, "history": []},
    )
    return {
        "url": example["url"],
        "audio_seconds": 246,
        "transcription_seconds": transcribed_seconds,
        "total_seconds": round(time.perf_counter() - started, 1),
        "transcript_source": transcript["transcript_source"],
        "transcript_words": len(transcript["transcript"].split()),
        "transcript_sample": transcript["transcript"][:1200],
        "question": question,
        "answer_seconds": round(time.perf_counter() - asked, 1),
        "answer": answer["answer"],
        "sources": answer["sources"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=[*EXAMPLES, "asr", "all"], default="all")
    args = parser.parse_args()
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "runs": {}}
    suffix = "" if args.only == "all" else f"_{args.only}"
    output = Path(f"data/benchmarks/live_sources{suffix}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.only == "asr":
        result = benchmark_forced_asr()
        target = output.with_name("asr_fallback.json")
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved ASR result to {target}", flush=True)
        return
    for kind, example in EXAMPLES.items():
        if args.only not in {"all", kind}:
            continue
        report["runs"][kind] = benchmark(kind, example)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved {kind} to {output}", flush=True)


if __name__ == "__main__":
    main()
