"""Fixed-evidence generation evaluation; no database writes or rule fallback."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from app.core.config import Settings, get_settings
from app.services.generation import qwen
from app.services.generation.errors import GenerationServiceError
from app.services.generation.factory import create_generation_provider

DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "evaluation/generation_v1/dataset.json"
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Text
    content: Text

class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Text
    category: Text
    question: Text
    evidence: list[EvidenceInput] = Field(min_length=1)
    review_expectations: list[Text] = Field(min_length=1)

def load_cases(path: Path) -> tuple[list[EvaluationCase], str]:
    raw = path.read_bytes()
    cases = [EvaluationCase.model_validate(item) for item in json.loads(raw)]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("评测集必须非空，且 case id 不得重复")
    return cases, hashlib.sha256(raw).hexdigest()

def evidence_for(case: EvaluationCase) -> list[dict]:
    return [{
        "chunk_id": str(uuid5(NAMESPACE_URL, f"qualitypilot-eval:{case.id}:{index}")),
        "document_id": str(uuid5(NAMESPACE_URL, f"qualitypilot-eval-doc:{case.id}:{index}")),
        "document_title": item.title,
        "source_uri": f"synthetic://generation-v1/{case.id}/{index}",
        "original_filename": None, "chunk_index": 0,
        "content": item.content, "score": 1.0,
        "match_type": "fixed_evidence", "vector_score": None, "keyword_score": None,
    } for index, item in enumerate(case.evidence, start=1)]

def summarize(results: list[dict], total: int) -> dict:
    latencies = sorted(item["latency_seconds"] for item in results)
    passed = sum(item["contract_passed"] for item in results)
    return {
        "planned_cases": total, "completed_cases": len(results),
        "contract_passed": passed,
        "contract_pass_rate": passed / len(results) if results else None,
        "http_requests": sum(len(item["attempts"]) for item in results),
        "format_correction_requests": sum(max(0, len(item["attempts"]) - 1) for item in results),
        "latency_seconds_median": statistics.median(latencies) if latencies else None,
        "latency_seconds_p95_nearest_rank": latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None,
        "business_quality_score": None,
    }

async def evaluate_case(case: EvaluationCase, settings: Settings, *, transport=None) -> dict:
    attempts = []
    key = settings.generation_api_key or settings.dashscope_api_key
    secret = key.get_secret_value() if key else ""
    async def capture(response: httpx.Response):
        await response.aread()
        attempt = {"http_status": response.status_code}
        # Do not persist headers, credentials or upstream error bodies.
        if response.status_code == 200:
            try:
                body = response.json()
                choice = body["choices"][0]
                content = choice["message"]["content"]
                if isinstance(content, str):
                    if secret:
                        content = content.replace(secret, "[REDACTED]")
                    attempt["content"] = content[:16000]
                    attempt["content_truncated"] = len(content) > 16000
                attempt["finish_reason"] = choice.get("finish_reason")
            except (ValueError, KeyError, IndexError, TypeError):
                attempt["malformed_envelope"] = True
        attempts.append(attempt)

    evidence = evidence_for(case)
    result = {
        "case_id": case.id, "category": case.category,
        "question": case.question, "evidence": evidence,
        "review_expectations": case.review_expectations,
        "contract_passed": False, "draft": None, "error": None,
        "attempts": attempts,
        "human_review": {
            "status": "not_reviewed", "reviewer": None,
            "evidence_fidelity_0_to_2": None,
            "uncertainty_handling_0_to_2": None,
            "actionability_0_to_2": None,
            "operation_boundaries_0_to_2": None,
            "notes": None,
        },
    }
    started = time.perf_counter()
    async with httpx.AsyncClient(
        timeout=settings.generation_timeout_seconds, transport=transport,
        event_hooks={"response": [capture]},
    ) as client:
        try:
            provider = create_generation_provider(settings, client=client)
            result["draft"] = await provider.generate(case.question, evidence)
            result["contract_passed"] = True
        except GenerationServiceError as exc:
            result["error"] = {"type": type(exc).__name__, "message": str(exc)}
            if hasattr(exc, "status_code"):
                result["error"]["http_status"] = exc.status_code
            cause = exc.__cause__
            if hasattr(cause, "errors"):
                result["error"]["validation"] = [
                    {"type": item["type"], "location": list(item["loc"])}
                    for item in cause.errors(include_input=False)
                ]
    result["latency_seconds"] = round(time.perf_counter() - started, 4)
    return result

async def execute(args) -> int:
    cases, dataset_hash = load_cases(args.dataset)
    settings = get_settings()
    report = {
        "schema_version": 1, "label": args.label,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running", "dataset_sha256": dataset_hash,
        "generator_source_sha256": hashlib.sha256(Path(qwen.__file__).read_bytes()).hexdigest(),
        "model": settings.generation_model, "provider": settings.generation_provider,
        "max_completion_tokens": settings.generation_max_completion_tokens,
        "timeout_seconds": settings.generation_timeout_seconds,
        "temperature": 0 if settings.generation_provider == "openai_compatible" else .1,
        "network_retries": 0, "max_format_corrections_per_case": 1 if settings.generation_provider == "openai_compatible" else 0,
        "scope": "synthetic fixed evidence; no retrieval, DB writes, baseline comparison or rule fallback",
        "training_overlap": "unknown; training data not yet inspected",
        "results": [], "summary": summarize([], len(cases)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite a previous experiment.
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    for case in cases:
        result = await evaluate_case(case, settings)
        report["results"].append(result)
        report["summary"] = summarize(report["results"], len(cases))
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"case_id": case.id, "contract_passed": result["contract_passed"], "latency_seconds": result["latency_seconds"]}, ensure_ascii=False), flush=True)
    report["status"] = "completed"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0 if report["summary"]["contract_passed"] == len(cases) else 1

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--label", required=True, help="Experiment label, never a credential")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(execute(args))

if __name__ == "__main__":
    raise SystemExit(main())
