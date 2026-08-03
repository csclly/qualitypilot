"""Change chunk embedding dimension from 1536 to 1024."""

from alembic import op
from pgvector.sqlalchemy import Vector


revision = "0003_embedding_1024"
down_revision = "0002_file_ingestion"
branch_labels = None
depends_on = None


def _ensure_embeddings_are_empty(action: str) -> None:
    if action not in {"upgrade", "downgrade"}:
        raise ValueError("Unsupported migration action")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM knowledge_document_chunks
                WHERE embedding IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot {action} embedding dimension while non-null vectors exist';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    _ensure_embeddings_are_empty("upgrade")
    op.alter_column(
        "knowledge_document_chunks",
        "embedding",
        existing_type=Vector(dim=1536),
        type_=Vector(dim=1024),
        existing_nullable=True,
        postgresql_using="embedding::vector(1024)",
    )


def downgrade() -> None:
    _ensure_embeddings_are_empty("downgrade")
    op.alter_column(
        "knowledge_document_chunks",
        "embedding",
        existing_type=Vector(dim=1024),
        type_=Vector(dim=1536),
        existing_nullable=True,
        postgresql_using="embedding::vector(1536)",
    )
