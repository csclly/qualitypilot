"""Add an HNSW cosine index for searchable chunk embeddings."""

from alembic import op
import sqlalchemy as sa


revision = "0004_embedding_hnsw"
down_revision = "0003_embedding_1024"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_knowledge_document_chunks_embedding_hnsw_cosine"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "knowledge_document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="knowledge_document_chunks")
