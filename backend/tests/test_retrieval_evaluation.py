from pathlib import Path

import httpx
from pydantic import ValidationError
import pytest

from app.evaluation.metrics import evaluate_retrieval
from app.evaluation.models import EvaluationDataset, RetrievedChunk
from app.evaluation.runner import load_dataset, run_api_evaluation


def make_dataset() -> EvaluationDataset:
    return EvaluationDataset.model_validate(
        {
            "name": "测试评测集",
            "version": "1.0",
            "description": "只用于单元测试",
            "documents": [
                {"id": "D1", "title": "文档一", "path": "d1.md"},
                {"id": "D2", "title": "文档二", "path": "d2.md"},
            ],
            "queries": [
                {
                    "id": "Q1",
                    "query": "问题一",
                    "relevant_document_ids": ["D1"],
                    "evidence_by_document": {"D1": ["证据一"]},
                },
                {
                    "id": "Q2",
                    "query": "问题二",
                    "relevant_document_ids": ["D1", "D2"],
                    "evidence_by_document": {"D1": ["证据二"]},
                },
            ],
        }
    )


def test_evaluate_retrieval_calculates_macro_metrics() -> None:
    dataset = make_dataset()
    results = {
        "Q1": [
            RetrievedChunk(
                document_id="D2", document_title="文档二", content="无关", score=0.9
            ),
            RetrievedChunk(
                document_id="D1",
                document_title="文档一",
                content="包含证据一",
                score=0.8,
            ),
        ],
        "Q2": [
            RetrievedChunk(
                document_id="D1",
                document_title="文档一",
                content="包含证据二",
                score=0.7,
            ),
            RetrievedChunk(
                document_id=None,
                document_title="评测集外文档",
                content="无关",
                score=0.6,
            ),
        ],
    }

    report = evaluate_retrieval(dataset, results, top_k=2)

    assert report.query_count == 2
    assert report.recall_at_k == pytest.approx(0.75)
    assert report.mrr == pytest.approx(0.75)
    assert report.citation_accuracy_at_k == pytest.approx(0.5)
    assert report.queries[0].first_relevant_rank == 2
    assert report.queries[1].retrieved_document_ids[-1] == "unknown:评测集外文档"


def test_evaluate_retrieval_returns_zero_for_missing_hits() -> None:
    report = evaluate_retrieval(make_dataset(), {}, top_k=3)

    assert report.recall_at_k == 0
    assert report.mrr == 0
    assert report.citation_accuracy_at_k == 0


def test_dataset_rejects_unknown_relevant_document() -> None:
    payload = make_dataset().model_dump()
    payload["queries"][0]["relevant_document_ids"] = ["UNKNOWN"]
    payload["queries"][0]["evidence_by_document"] = {}

    with pytest.raises(ValidationError, match="未知文档"):
        EvaluationDataset.model_validate(payload)


def test_synthetic_pcb_dataset_is_valid() -> None:
    dataset_path = (
        Path(__file__).parents[1] / "evaluation" / "pcb_sop_v1" / "dataset.json"
    )
    dataset = load_dataset(dataset_path)

    assert len(dataset.documents) == 5
    assert len(dataset.queries) == 8
    for document in dataset.documents:
        assert (dataset_path.parent / document.path).is_file()


async def test_api_runner_maps_document_titles_and_evaluates() -> None:
    dataset = make_dataset()

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["mode"] == "hybrid"
        if payload["query"] == "问题一":
            hits = [
                {
                    "document_title": "文档一",
                    "content": "包含证据一",
                    "score": 0.95,
                }
            ]
        else:
            hits = [
                {
                    "document_title": "评测集外文档",
                    "content": "无关",
                    "score": 0.4,
                }
            ]
        return httpx.Response(200, json=hits)

    report = await run_api_evaluation(
        dataset,
        base_url="http://test",
        top_k=1,
        search_mode="hybrid",
        transport=httpx.MockTransport(handler),
    )

    assert report.search_mode == "hybrid"
    assert report.queries[0].recall_at_k == 1
    assert report.queries[0].citation_accuracy_at_k == 1
    assert report.queries[1].recall_at_k == 0
    assert report.mrr == pytest.approx(0.5)


async def test_api_runner_retries_transient_server_error() -> None:
    dataset = make_dataset()
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(503, json={"detail": "上游暂时不可用"})
        return httpx.Response(
            200,
            json=[
                {
                    "document_title": "文档一",
                    "content": "包含证据一和证据二",
                    "score": 0.9,
                }
            ],
        )

    report = await run_api_evaluation(
        dataset,
        base_url="http://test",
        top_k=1,
        request_max_retries=1,
        request_retry_base_delay_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert request_count == 3
    assert report.query_count == 2
