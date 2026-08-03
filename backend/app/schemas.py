from enum import Enum
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source_type: str = Field(default="manual", max_length=50)
    source_uri: str | None = Field(default=None, max_length=1000)


class DocumentResponse(DocumentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    original_filename: str | None = None
    content_type: str | None = None
    file_size: int | None = None
    checksum_sha256: str | None = None
    storage_path: str | None = None
    chunk_count: int = 0
    processed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    char_start: int | None
    char_end: int | None
    has_embedding: bool
    embedding_dimension: int | None
    created_at: datetime


class DocumentEmbeddingBackfillResponse(BaseModel):
    document_id: uuid.UUID
    total_chunks: int
    embedded_chunks: int
    skipped_chunks: int


class KnowledgeSearchMode(str, Enum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


class KnowledgeSearchRequest(BaseModel):
    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]
    top_k: int = Field(default=5, ge=1, le=50)
    mode: KnowledgeSearchMode = KnowledgeSearchMode.VECTOR


class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_type: str
    source_uri: str | None
    original_filename: str | None
    chunk_index: int
    content: str
    char_start: int | None
    char_end: int | None
    score: float
    match_type: KnowledgeSearchMode
    vector_score: float | None = None
    keyword_score: float | None = None
