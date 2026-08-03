import asyncio
import json
from pathlib import Path

import httpx

from app.evaluation.metrics import evaluate_retrieval
from app.evaluation.models import (
    EvaluationDataset,
    RetrievedChunk,
    RetrievalEvaluationReport,
)


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


async def _request_search(
    client: httpx.AsyncClient,
    *,
    query: str,
    top_k: int,
    search_mode: str,
    max_retries: int,
    retry_base_delay_seconds: float,
) -> httpx.Response:
    for attempt in range(max_retries + 1):
        try:
            response = await client.post(
                "/api/v1/knowledge/search",
                json={"query": query, "top_k": top_k, "mode": search_mode},
            )
        except httpx.TransportError:
            if attempt >= max_retries:
                raise
        else:
            retryable_status = response.status_code == 429 or response.status_code >= 500
            if not retryable_status or attempt >= max_retries:
                response.raise_for_status()
                return response

        await asyncio.sleep(retry_base_delay_seconds * (2**attempt))

    raise RuntimeError("评测请求重试流程出现不可达状态")


async def run_api_evaluation(
    dataset: EvaluationDataset,
    *,
    base_url: str,
    top_k: int,
    timeout_seconds: float = 60.0,
    search_mode: str = "vector",
    request_max_retries: int = 2,
    request_retry_base_delay_seconds: float = 1.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RetrievalEvaluationReport:
    if not 1 <= top_k <= 50:
        raise ValueError("top_k 必须在 1 到 50 之间")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if request_max_retries < 0:
        raise ValueError("request_max_retries 不能小于 0")
    if request_retry_base_delay_seconds < 0:
        raise ValueError("request_retry_base_delay_seconds 不能小于 0")
    if search_mode not in {"vector", "keyword", "hybrid"}:
        raise ValueError("search_mode 必须是 vector、keyword 或 hybrid")

    document_id_by_title = {
        document.title: document.id for document in dataset.documents
    }
    results_by_query: dict[str, list[RetrievedChunk]] = {}

    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=timeout_seconds,
        transport=transport,
    ) as client:
        for query in dataset.queries:
            response = await _request_search(
                client,
                query=query.query,
                top_k=top_k,
                max_retries=request_max_retries,
                search_mode=search_mode,
                retry_base_delay_seconds=request_retry_base_delay_seconds,
            )
            results_by_query[query.id] = [
                RetrievedChunk(
                    document_id=document_id_by_title.get(hit["document_title"]),
                    document_title=hit["document_title"],
                    content=hit["content"],
                    score=hit["score"],
                )
                for hit in response.json()
            ]

    return evaluate_retrieval(
        dataset,
        results_by_query,
        top_k=top_k,
        search_mode=search_mode,
    )


def save_report(report: RetrievalEvaluationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
