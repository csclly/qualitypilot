import json

import httpx
import pytest
from pydantic import ValidationError

from app.agent.drafting import ResilientRecommendationGenerator
from app.core.config import Settings
from app.services.embedding.factory import create_embedding_provider
from app.services.generation.errors import GenerationConfigurationError
from app.services.generation.factory import create_generation_provider


def _settings(**overrides) -> Settings:
    values = {
        "generation_provider": "openai_compatible",
        "generation_api_key": "self-hosted-test-key",
        "generation_base_url": "http://127.0.0.1:18001/v1/",
        "generation_model": "pcb-qwen-lora",
        "generation_max_completion_tokens": 1200,
        "dashscope_api_key": "embedding-test-key",
        "embedding_base_url": "https://embedding.example.test/v1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _records() -> list[dict]:
    return [{
        "tool_name": "mes-reader",
        "system": "mes",
        "record_id": "MES-TEST-1",
        "record_type": "batch_status",
        "summary": "测试批次待人工复核",
        "attributes": {},
    }]


def _content(**overrides) -> str:
    values = {
        "summary": "现有证据不足以确认短路根因。",
        "suggested_actions": ["复核批次状态并收集现场证据"],
        "risk_notes": ["尚未确认真实铜桥，不得据此调整工艺。"],
        "citations": [],
        "business_references": [1],
    }
    values.update(overrides)
    return json.dumps(values, ensure_ascii=False)


async def test_self_hosted_contract_and_independent_embedding_key() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [
                {"index": 0, "embedding": [0.1] * 1024}
            ]})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": _content()}}],
            "performance": {"latency_seconds": 9.334},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = create_generation_provider(_settings(), client=client)
        result = await provider.generate("AOI 短路报点增加", [], _records())
        embedding = create_embedding_provider(_settings(), client=client)
        await embedding.embed_documents(["测试文本"])

    generated, embedded = requests
    assert str(generated.url) == "http://127.0.0.1:18001/v1/chat/completions"
    assert generated.headers["Authorization"] == "Bearer self-hosted-test-key"
    assert embedded.headers["Authorization"] == "Bearer embedding-test-key"
    payload = json.loads(generated.content)
    assert set(payload) == {"model", "messages", "temperature", "max_tokens"}
    assert payload["model"] == "pcb-qwen-lora"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 1200
    assert "JSON" in payload["messages"][0]["content"]
    assert result["generation_mode"] == "model"
    assert result["business_record_references"] == [
        {"tool_name": "mes-reader", "record_id": "MES-TEST-1"}
    ]


@pytest.mark.parametrize("key", [None, "", "   "])
def test_self_hosted_does_not_fall_back_to_dashscope_key(key) -> None:
    with pytest.raises(GenerationConfigurationError, match="GENERATION_API_KEY"):
        create_generation_provider(_settings(generation_api_key=key))


@pytest.mark.parametrize("key", [None, "", "dedicated-test-key"])
async def test_dashscope_key_compatibility_and_request_fields(key) -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": _content()}}]
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = create_generation_provider(_settings(
            generation_provider="dashscope", generation_api_key=key,
        ), client=client)
        await provider.generate("问题", [], _records())

    assert captured[0].headers["Authorization"] == (
        f"Bearer {key or 'embedding-test-key'}"
    )
    payload = json.loads(captured[0].content)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False
    assert payload["max_completion_tokens"] == 1200
    assert "max_tokens" not in payload


@pytest.mark.parametrize("content", [
    "先分析 AOI 误报和工艺波动，收集现场证据。",
    _content(business_references=[2]),
    _content(citations=[1]),
    _content(business_references=[]),
])
async def test_self_hosted_invalid_output_falls_back_with_bounded_correction(content) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}]
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = ResilientRecommendationGenerator(
            lambda: create_generation_provider(_settings(), client=client),
            max_retries=2, retry_base_delay_seconds=0,
        )
        result = await generator.generate("问题", [], _records())

    assert calls == (2 if content.startswith("先分析") else 1)
    assert result["generation_mode"] == "deterministic_fallback"
    assert "响应校验错误" in result["risk_notes"][0]


@pytest.mark.parametrize("failure,expected_calls", [("unauthorized", 1), ("timeout", 2)])
async def test_self_hosted_failures_use_existing_retry_policy(failure, expected_calls):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(401, text="private upstream details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = ResilientRecommendationGenerator(
            lambda: create_generation_provider(_settings(), client=client),
            max_retries=1, retry_base_delay_seconds=0,
        )
        result = await generator.generate("问题", [], _records())

    assert calls == expected_calls
    assert result["generation_mode"] == "deterministic_fallback"
    assert "private upstream details" not in str(result)


def test_provider_configuration_rejects_unknown_mode_and_hides_key() -> None:
    with pytest.raises(ValidationError):
        _settings(generation_provider="typo")
    assert "self-hosted-test-key" not in repr(_settings())


@pytest.mark.parametrize("valid_json", [True, False, "corrected"])
async def test_self_hosted_agent_api_preserves_approval_and_fallback(
    monkeypatch, valid_json,
) -> None:
    from app.api.routes.agent import (
        get_agent_run_error_store, get_recommendation_generator,
    )
    from app.agent.retrieval import get_agent_evidence_retriever_builder
    from app.agent.workflow import QualityAgentWorkflow, get_quality_agent_workflow
    from app.main import app
    from test_agent_api import FakeAgentRunErrorStore, _fake_retriever_builder

    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = (
            _content(citations=[1], business_references=[])
            if valid_json else "仅返回普通文字"
        )
        if valid_json == "corrected" and calls == 1:
            content += "\n必须人工复核。"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}]
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as model_client:
        generator = ResilientRecommendationGenerator(
            lambda: create_generation_provider(_settings(), client=model_client),
            max_retries=0, retry_base_delay_seconds=0,
        )
        workflow = QualityAgentWorkflow()
        errors = FakeAgentRunErrorStore()
        for dependency, replacement in [
            (get_quality_agent_workflow, lambda: workflow),
            (get_recommendation_generator, lambda: generator),
            (get_agent_evidence_retriever_builder, lambda: _fake_retriever_builder),
            (get_agent_run_error_store, lambda: errors),
        ]:
            monkeypatch.setitem(app.dependency_overrides, dependency, replacement)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/agent/runs", json={
                "question": "AOI 短路报点增加怎么办？",
                "top_k": 2, "search_mode": "keyword",
            })

    assert calls == (1 if valid_json is True else 2)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_required"] is True
    assert body["final_response"] is None
    assert body["draft"]["generation_mode"] == (
        "model" if valid_json else "deterministic_fallback"
    )
    assert body["draft"]["citations"] == [body["evidence"][0]["chunk_id"]]


@pytest.mark.parametrize("wrapper", ["trailing_text", "markdown", "plain_text"])
async def test_self_hosted_corrects_json_syntax_once(wrapper) -> None:
    good = _content()
    malformed = {
        "trailing_text": good + "\n补充说明：必须人工复核。",
        "markdown": "```json\n" + good + "\n```",
        "plain_text": "先收集现场数据。",
    }[wrapper]
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {
                "content": malformed if len(captured) == 1 else good,
            }}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = create_generation_provider(_settings(), client=client)
        result = await provider.generate("问题", [], _records())

    assert len(captured) == 2
    assert captured[1]["messages"][:2] == captured[0]["messages"]
    assert captured[1]["messages"][2] == {"role": "assistant", "content": malformed}
    assert "不要在 JSON 前后追加" in captured[1]["messages"][3]["content"]
    assert captured[1]["model"] == "pcb-qwen-lora"
    assert captured[1]["max_tokens"] == captured[0]["max_tokens"]
    assert result["generation_mode"] == "model"
    assert result["business_record_references"] == [
        {"tool_name": "mes-reader", "record_id": "MES-TEST-1"}
    ]


@pytest.mark.parametrize("corrected", [
    _content() + "\n仍有尾部文字",
    _content(business_references=[2]),
    _content(citations=[1]),
    _content(business_references=[]),
    _content(extra="不允许的字段"),
])
async def test_corrected_output_must_pass_original_contract(corrected) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "choices": [{"message": {
                "content": _content() + "\n尾部文字" if calls == 1 else corrected,
            }}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = ResilientRecommendationGenerator(
            lambda: create_generation_provider(_settings(), client=client),
            max_retries=2, retry_base_delay_seconds=0,
        )
        result = await generator.generate("问题", [], _records())

    assert calls == 2
    assert result["generation_mode"] == "deterministic_fallback"


@pytest.mark.parametrize("mode,content", [
    ("dashscope", _content() + "\n尾部文字"),
    ("openai_compatible", "x" * 16001),
    ("openai_compatible", _content(summary="")),
])
async def test_format_correction_does_not_expand_scope(mode, content) -> None:
    from app.services.generation.errors import GenerationResponseError

    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = create_generation_provider(
            _settings(generation_provider=mode), client=client,
        )
        with pytest.raises(GenerationResponseError):
            await provider.generate("问题", [], _records())
    assert calls == 1
