from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationDocument(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    path: str = Field(min_length=1)


class EvaluationQuery(BaseModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    relevant_document_ids: list[str] = Field(min_length=1)
    evidence_by_document: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_documents(self) -> "EvaluationQuery":
        unknown_ids = set(self.evidence_by_document) - set(self.relevant_document_ids)
        if unknown_ids:
            raise ValueError(
                "证据短语只能关联相关文档：" + ", ".join(sorted(unknown_ids))
            )
        if any(
            not terms or any(not term.strip() for term in terms)
            for terms in self.evidence_by_document.values()
        ):
            raise ValueError("证据短语不能为空")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    documents: list[EvaluationDocument] = Field(min_length=1)
    queries: list[EvaluationQuery] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "EvaluationDataset":
        document_ids = [document.id for document in self.documents]
        document_titles = [document.title for document in self.documents]
        query_ids = [query.id for query in self.queries]

        if len(document_ids) != len(set(document_ids)):
            raise ValueError("评测文档 id 必须唯一")
        if len(document_titles) != len(set(document_titles)):
            raise ValueError("评测文档 title 必须唯一")
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("评测查询 id 必须唯一")

        known_document_ids = set(document_ids)
        for query in self.queries:
            unknown_ids = set(query.relevant_document_ids) - known_document_ids
            if unknown_ids:
                raise ValueError(
                    f"查询 {query.id} 引用了未知文档："
                    + ", ".join(sorted(unknown_ids))
                )
        return self


class RetrievedChunk(BaseModel):
    document_id: str | None = None
    document_title: str
    content: str
    score: float


class QueryEvaluationResult(BaseModel):
    query_id: str
    query: str
    recall_at_k: float
    reciprocal_rank: float
    citation_accuracy_at_k: float
    retrieved_document_ids: list[str]
    first_relevant_rank: int | None


class RetrievalEvaluationReport(BaseModel):
    dataset_name: str
    dataset_version: str
    search_mode: str
    top_k: int
    query_count: int
    recall_at_k: float
    mrr: float
    citation_accuracy_at_k: float
    queries: list[QueryEvaluationResult]
