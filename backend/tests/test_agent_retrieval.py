import uuid

import pytest

from app.agent.retrieval import build_agent_evidence_retriever
from app.services.knowledge_search import KnowledgeSearchHit


def _hit(*, match_type: str, score: float) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="回流焊规范",
        source_type="upload",
        source_uri="upload://reflow.txt",
        original_filename="回流焊规范.txt",
        chunk_index=0,
        content="桥接时检查钢网开口和锡膏印刷参数。",
        char_start=0,
        char_end=20,
        score=score,
        match_type=match_type,
        vector_score=score if match_type == "vector" else None,
        keyword_score=score if match_type == "keyword" else None,
    )


async def test_agent_keyword_retrieval_does_not_generate_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def keyword_search(_db: object, query: str, *, top_k: int):
        assert query == "桥接"
        assert top_k == 2
        return [_hit(match_type="keyword", score=0.8)]

    async def unexpected_embedding(*_args: object, **_kwargs: object):
        raise AssertionError("关键词模式不应生成查询向量")

    monkeypatch.setattr(
        "app.agent.retrieval.search_keyword_chunks",
        keyword_search,
    )
    monkeypatch.setattr(
        "app.agent.retrieval.embed_query_text",
        unexpected_embedding,
    )
    retriever = build_agent_evidence_retriever(object(), lambda: object())

    evidence = await retriever("桥接", top_k=2, mode="keyword")

    assert len(evidence) == 1
    assert evidence[0]["match_type"] == "keyword"
    assert evidence[0]["keyword_score"] == pytest.approx(0.8)


async def test_agent_hybrid_retrieval_uses_embedding_and_fusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_hit = _hit(match_type="vector", score=0.9)
    keyword_hit = _hit(match_type="keyword", score=0.7)
    embedding_calls = 0

    async def searchable(_db: object) -> bool:
        return True

    async def embed(*_args: object, **_kwargs: object):
        nonlocal embedding_calls
        embedding_calls += 1
        return [1.0] + [0.0] * 1023

    async def vector_search(_db: object, _vector: list[float], *, top_k: int):
        assert top_k >= 2
        return [vector_hit]

    async def keyword_search(_db: object, _query: str, *, top_k: int):
        assert top_k >= 2
        return [keyword_hit]

    def fuse(vector_hits, keyword_hits, *, top_k: int, rrf_k: int):
        assert vector_hits == [vector_hit]
        assert keyword_hits == [keyword_hit]
        assert top_k == 2
        assert rrf_k >= 1
        return [vector_hit, keyword_hit]

    monkeypatch.setattr("app.agent.retrieval.has_searchable_chunks", searchable)
    monkeypatch.setattr("app.agent.retrieval.embed_query_text", embed)
    monkeypatch.setattr("app.agent.retrieval.search_knowledge_chunks", vector_search)
    monkeypatch.setattr("app.agent.retrieval.search_keyword_chunks", keyword_search)
    monkeypatch.setattr("app.agent.retrieval.fuse_search_hits", fuse)
    retriever = build_agent_evidence_retriever(object(), lambda: object())

    evidence = await retriever("桥接", top_k=2, mode="hybrid")

    assert embedding_calls == 1
    assert [item["match_type"] for item in evidence] == ["vector", "keyword"]
