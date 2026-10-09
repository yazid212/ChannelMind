"""Run a fixed, transcript-grounded evaluation against the local API.

Usage: .venv/Scripts/python.exe scripts/evaluate_day3.py
Or pass --reuse-workspace-id ID to evaluate an existing local Chroma workspace.
Both paths make four paid LLM calls. Reports contain no workspace token, API
key, or full third-party transcript.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import LLM_MODELS, LLM_PROVIDER, TOP_K  # noqa: E402
from src.embedder import Embedder  # noqa: E402
from src.rag import YouTubeRAG  # noqa: E402


API = "http://127.0.0.1:8000/api/v1"
DATASET = ROOT / "evaluation" / "day3_cases.json"
OUTPUT = ROOT / "data" / "evaluations" / "day3_latest.json"


def call(session, method, path, token=None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = session.request(method, f"{API}{path}", headers=headers, timeout=120, **kwargs)
    response.raise_for_status()
    return response.json()


def price_per_token(session):
    if LLM_PROVIDER != "openrouter":
        return None
    try:
        response = session.get("https://openrouter.ai/api/v1/models", timeout=20)
        response.raise_for_status()
        model = next(item for item in response.json()["data"] if item["id"] == LLM_MODELS["openrouter"])
        return {
            "input": Decimal(model["pricing"]["prompt"]),
            "output": Decimal(model["pricing"]["completion"]),
        }
    except (requests.RequestException, KeyError, StopIteration, ValueError):
        return None


def matches(text, terms):
    normalized = " ".join(text.casefold().split())
    return all(" ".join(term.casefold().split()) in normalized for term in terms)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-workspace-id", help="Use an already indexed local workspace without downloading the video again")
    args = parser.parse_args()
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    session = requests.Session()
    session.trust_env = False
    if args.reuse_workspace_id:
        workspace_id = args.reuse_workspace_id
        rag = YouTubeRAG(workspace_id=workspace_id)
        answer_question = lambda question, mode: rag.ask(question, [], mode)
    else:
        workspace = call(session, "POST", "/workspaces")
        workspace_id, token = workspace["workspace_id"], workspace["token"]
        job = call(session, "POST", f"/workspaces/{workspace_id}/videos", token,
                   json={"video": data["source_url"]})
        deadline = time.monotonic() + 900
        while True:
            status = call(session, "GET", f"/jobs/{job['job_id']}", token)
            if status["status"] in {"finished", "failed"}:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Video ingestion exceeded 15 minutes")
            time.sleep(3)
        if status["status"] != "finished" or not status.get("result", {}).get("ok"):
            raise RuntimeError(f"Video ingestion failed: {status.get('result', {}).get('message', 'unknown reason')}")
        answer_question = lambda question, mode: call(
            session, "POST", f"/workspaces/{workspace_id}/chat", token,
            json={"question": question, "history": [], "mode": mode},
        )

    embedder = Embedder(workspace_id=workspace_id)
    prices = price_per_token(session)
    results = []
    for case in data["cases"]:
        question = case["question"]
        evidence = case["expected_evidence"]
        started = time.perf_counter()
        retrieved = embedder.search(question, k=TOP_K)
        retrieval_seconds = round(time.perf_counter() - started, 3)
        retrieval_hit = (matches(" ".join(doc.page_content for doc in retrieved), evidence)
                         if case["answerable"] else None)

        started = time.perf_counter()
        answer = answer_question(question, "transcript")
        answer_seconds = round(time.perf_counter() - started, 3)
        source_quotes = " ".join(source["quote"] for source in answer["sources"])
        citation_supported = (
            bool(answer["sources"])
            and all(source["video_id"] == data["video_id"] for source in answer["sources"])
            and matches(source_quotes, evidence)
        ) if case["answerable"] else None
        insufficient = YouTubeRAG.INSUFFICIENT_ANSWER in answer["answer"]
        answerability_correct = (
            not insufficient and bool(answer["sources"])
            if case["answerable"] else insufficient and not answer["sources"]
        )
        usage = answer.get("usage") or {}
        cost = None
        if prices and usage:
            cost = str(
                Decimal(usage.get("input_tokens", 0)) * prices["input"]
                + Decimal(usage.get("output_tokens", 0)) * prices["output"]
            )
        results.append({
            "id": case["id"], "answerable": case["answerable"],
            "retrieval_hit": retrieval_hit, "citation_supported": citation_supported,
            "answerability_correct": answerability_correct,
            "retrieval_seconds": retrieval_seconds, "answer_seconds": answer_seconds,
            "retrieved_chunks": len(retrieved), "cited_sources": len(answer["sources"]),
            "usage": usage, "estimated_cost_usd": cost,
        })

    general = answer_question("What are JavaScript promises?", "general")
    general_mode = {
        "labeled": general["answer"].startswith(YouTubeRAG.GENERAL_KNOWLEDGE_LABEL),
        "has_no_video_citations": not general["sources"] and general["retrieved_chunks"] == 0,
        "usage": general.get("usage") or {},
    }
    if prices and general_mode["usage"]:
        general_mode["estimated_cost_usd"] = str(
            Decimal(general_mode["usage"].get("input_tokens", 0)) * prices["input"]
            + Decimal(general_mode["usage"].get("output_tokens", 0)) * prices["output"]
        )
    supported = [row for row in results if row["answerable"]]
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "source_url": data["source_url"], "workspace_id": workspace_id,
        "evaluation_path": "existing_local_workspace" if args.reuse_workspace_id else "local_api",
        "model": LLM_MODELS.get(LLM_PROVIDER), "provider": LLM_PROVIDER,
        "pricing_usd_per_token": ({key: str(value) for key, value in prices.items()} if prices else None),
        "results": results,
        "general_mode": general_mode,
        "summary": {
            "retrieval_recall": sum(bool(row["retrieval_hit"]) for row in supported) / len(supported),
            "citation_support": sum(bool(row["citation_supported"]) for row in supported) / len(supported),
            "answerability_accuracy": sum(row["answerability_correct"] for row in results) / len(results),
            "estimated_cost_usd": (str(sum(Decimal(row["estimated_cost_usd"])
                                       for row in results) + Decimal(general_mode["estimated_cost_usd"]))
                                   if prices and "estimated_cost_usd" in general_mode else None),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Report saved to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
