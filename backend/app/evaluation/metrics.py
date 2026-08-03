from collections.abc import Mapping, Sequence

from app.evaluation.models import (
    EvaluationDataset,
    QueryEvaluationResult,
    RetrievedChunk,
    RetrievalEvaluationReport,
)


def _citation_is_correct(
    hit: RetrievedChunk,
    *,
    relevant_document_ids: set[str],
    evidence_by_document: Mapping[str, Sequence[str]],
) -> bool:
    if hit.document_id is None or hit.document_id not in relevant_document_ids:
        return False

    evidence_terms = evidence_by_document.get(hit.document_id, ())
    return not evidence_terms or any(term in hit.content for term in evidence_terms)


def evaluate_retrieval(
    dataset: EvaluationDataset,
    results_by_query: Mapping[str, Sequence[RetrievedChunk]],
    *,
    top_k: int,
    search_mode: str = "vector",
) -> RetrievalEvaluationReport:
    if top_k < 1:
        raise ValueError("top_k 必须大于等于 1")

    query_results: list[QueryEvaluationResult] = []
    for query in dataset.queries:
        hits = list(results_by_query.get(query.id, ()))[:top_k]
        relevant_ids = set(query.relevant_document_ids)
        retrieved_relevant_ids = {
            hit.document_id
            for hit in hits
            if hit.document_id is not None and hit.document_id in relevant_ids
        }
        first_relevant_rank = next(
            (
                rank
                for rank, hit in enumerate(hits, start=1)
                if hit.document_id in relevant_ids
            ),
            None,
        )
        correct_citation_count = sum(
            _citation_is_correct(
                hit,
                relevant_document_ids=relevant_ids,
                evidence_by_document=query.evidence_by_document,
            )
            for hit in hits
        )

        query_results.append(
            QueryEvaluationResult(
                query_id=query.id,
                query=query.query,
                recall_at_k=len(retrieved_relevant_ids) / len(relevant_ids),
                reciprocal_rank=(
                    1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0
                ),
                citation_accuracy_at_k=(
                    correct_citation_count / len(hits) if hits else 0.0
                ),
                retrieved_document_ids=[
                    hit.document_id or f"unknown:{hit.document_title}" for hit in hits
                ],
                first_relevant_rank=first_relevant_rank,
            )
        )

    query_count = len(query_results)
    return RetrievalEvaluationReport(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        search_mode=search_mode,
        top_k=top_k,
        query_count=query_count,
        recall_at_k=sum(result.recall_at_k for result in query_results) / query_count,
        mrr=sum(result.reciprocal_rank for result in query_results) / query_count,
        citation_accuracy_at_k=(
            sum(result.citation_accuracy_at_k for result in query_results) / query_count
        ),
        queries=query_results,
    )
