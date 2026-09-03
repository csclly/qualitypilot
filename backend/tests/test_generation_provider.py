import json
import uuid

import httpx
import pytest

from app.agent.drafting import ResilientRecommendationGenerator
from app.agent.state import AgentBusinessRecord, AgentEvidence
from app.services.generation.errors import (
    GenerationConfigurationError,
    GenerationResponseError,
)
from app.services.generation.qwen import QwenStructuredRecommendationGenerator


def _evidence(count: int = 2) -> list[AgentEvidence]:
    return [
        {
            "chunk_id": str(uuid.uuid4()),
            "document_id": str(uuid.uuid4()),
            "document_title": f"质量规范 {index}",
            "source_uri": None,
            "original_filename": f"quality-{index}.md",
            "chunk_index": index - 1,
            "content": f"证据内容 {index}",
            "score": 0.9,
            "match_type": "hybrid",
            "vector_score": 0.8,
            "keyword_score": 0.9,
        }
        for index in range(1, count + 1)
    ]


def _business_records() -> list[AgentBusinessRecord]:
    return [
        {
            "tool_name": "mes-reader",
            "system": "mes",
            "record_id": "MES-LOT-001",
            "record_type": "batch_status",
            "summary": "批次已暂停流转",
            "attributes": {"line": "SMT-01"},
        }
    ]


def _provider(client: httpx.AsyncClient) -> QwenStructuredRecommendationGenerator:
    return QwenStructuredRecommendationGenerator(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="qwen3.7-max-2026-05-20",
        timeout_seconds=5,
        max_completion_tokens=800,
        client=client,
    )


async def test_qwen_generator_requests_json_and_maps_citations() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "桥接可能与印刷参数有关。",
                                    "suggested_actions": ["核查钢网和锡膏印刷参数"],
                                    "risk_notes": ["需结合现场数据确认"],
                                    "citations": [2, 1, 2],
                                    "business_references": [1, 1],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    evidence = _evidence()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _provider(client).generate(
            "桥接原因是什么？",
            evidence,
            _business_records(),
        )

    assert captured["response_format"] == {"type": "json_object"}
    assert captured["enable_thinking"] is False
    assert "JSON" in captured["messages"][0]["content"]
    assert result["generation_mode"] == "model"
    assert result["citations"] == [evidence[1]["chunk_id"], evidence[0]["chunk_id"]]
    assert result["business_record_references"] == [
        {"tool_name": "mes-reader", "record_id": "MES-LOT-001"}
    ]
    assert "只读业务记录" in captured["messages"][1]["content"]


async def test_qwen_generator_rejects_unknown_evidence_citation() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "结论",
                                    "suggested_actions": ["动作"],
                                    "risk_notes": ["风险"],
                                    "citations": [3],
                                    "business_references": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GenerationResponseError, match="不存在"):
            await _provider(client).generate("问题", _evidence())


async def test_resilient_generator_retries_then_falls_back() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = ResilientRecommendationGenerator(
            lambda: _provider(client),
            max_retries=2,
            retry_base_delay_seconds=0,
        )
        result = await generator.generate("问题", _evidence())

    assert calls == 3
    assert result["generation_mode"] == "deterministic_fallback"
    assert result["citations"]
    assert result["business_record_references"] == []
    assert "远端模型服务错误" in result["risk_notes"][0]


async def test_resilient_generator_falls_back_when_key_is_missing() -> None:
    def missing_provider() -> QwenStructuredRecommendationGenerator:
        raise GenerationConfigurationError("未配置 API Key")

    generator = ResilientRecommendationGenerator(
        missing_provider,
        max_retries=2,
        retry_base_delay_seconds=0,
    )
    result = await generator.generate("问题", _evidence())

    assert result["generation_mode"] == "deterministic_fallback"
    assert "模型配置或响应校验错误" in result["risk_notes"][0]


async def test_empty_evidence_does_not_create_model_provider() -> None:
    created = False

    def provider_factory() -> QwenStructuredRecommendationGenerator:
        nonlocal created
        created = True
        raise AssertionError("空证据不应创建模型客户端")

    generator = ResilientRecommendationGenerator(
        provider_factory,
        max_retries=2,
        retry_base_delay_seconds=0,
    )
    result = await generator.generate("问题", [])

    assert created is False
    assert result["citations"] == []
    assert result["business_record_references"] == []
    assert result["generation_mode"] == "deterministic_fallback"


async def test_qwen_generator_can_use_business_record_without_knowledge() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "批次当前已暂停。",
                                    "suggested_actions": ["核查批次状态"],
                                    "risk_notes": ["只读记录需要人工确认"],
                                    "citations": [],
                                    "business_references": [1],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _provider(client).generate(
            "批次状态是什么？",
            [],
            _business_records(),
        )

    assert result["citations"] == []
    assert result["business_record_references"] == [
        {"tool_name": "mes-reader", "record_id": "MES-LOT-001"}
    ]
    assert result["generation_mode"] == "model"
