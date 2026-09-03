import json
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from scripts.evaluate_generation import (
    DEFAULT_DATASET, EvaluationCase, evaluate_case, load_cases, summarize,
)


def settings():
    return Settings(
        _env_file=None, generation_provider="openai_compatible",
        generation_api_key="evaluation-private-key",
        generation_base_url="http://model.example.test/v1",
        generation_model="test-model", generation_max_completion_tokens=400,
    )


def case():
    return EvaluationCase(
        id="eval-one", category="test", question="如何复核？",
        evidence=[{"title": "合成证据", "content": "先核查原图。"}],
        review_expectations=["不编造现场结论"],
    )


def valid_content():
    return json.dumps({
        "summary": "证据不足。", "suggested_actions": ["核查原图。"],
        "risk_notes": ["缺少现场复核。"],
        "citations": [1], "business_references": [],
    })


async def test_evaluation_counts_correction_but_never_hides_failed_contract():
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "前言\n" + valid_content() if count == 1 else valid_content(),
        }, "finish_reason": "stop"}]})
    result = await evaluate_case(case(), settings(), transport=httpx.MockTransport(handler))
    assert result["contract_passed"] is True
    assert len(result["attempts"]) == 2
    assert result["draft"]["citations"] == [result["evidence"][0]["chunk_id"]]
    assert result["human_review"]["status"] == "not_reviewed"
    assert summarize([result], 1)["format_correction_requests"] == 1
    assert summarize([result], 1)["business_quality_score"] is None


async def test_evaluation_retains_missing_fields_as_failure_without_rule_fallback():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '{"summary":"不完整"}',
        }}]})
    result = await evaluate_case(case(), settings(), transport=httpx.MockTransport(handler))
    assert result["contract_passed"] is False
    assert result["draft"] is None
    assert len(result["attempts"]) == 1
    assert result["error"]["type"] == "GenerationResponseError"
    assert result["error"]["validation"]


async def test_evaluation_does_not_store_auth_headers_or_upstream_error_body():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer evaluation-private-key"
        return httpx.Response(401, json={"detail": "evaluation-private-key upstream secret"})
    result = await evaluate_case(case(), settings(), transport=httpx.MockTransport(handler))
    assert result["error"]["http_status"] == 401
    assert "evaluation-private-key" not in json.dumps(result)
    assert "upstream secret" not in json.dumps(result)


def test_summary_uses_completed_denominator_and_keeps_quality_unscored():
    results = [
        {"contract_passed": True, "latency_seconds": 2, "attempts": [{}]},
        {"contract_passed": False, "latency_seconds": 6, "attempts": [{}, {}]},
    ]
    summary = summarize(results, 8)
    assert summary["completed_cases"] == 2
    assert summary["contract_pass_rate"] == .5
    assert summary["latency_seconds_median"] == 4
    assert summary["latency_seconds_p95_nearest_rank"] == 6
    assert summarize([], 8)["contract_pass_rate"] is None


def test_fixed_dataset_and_duplicate_ids(tmp_path: Path):
    cases, digest = load_cases(DEFAULT_DATASET)
    assert len(cases) == 8
    assert len(digest) == 64
    assert any(c.category == "证据内指令" for c in cases)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps([cases[0].model_dump()] * 2), encoding="utf-8")
    with pytest.raises(ValueError, match="不得重复"):
        load_cases(duplicate)
