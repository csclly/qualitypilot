"""Add pg_trgm support and a GiST index for keyword retrieval."""

from alembic import op


revision = "0005_trigram_search"
down_revision = "0004_embedding_hnsw"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_knowledge_document_chunks_content_gist_trgm"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        INDEX_NAME,
        "knowledge_document_chunks",
        ["content"],
        unique=False,
        postgresql_using="gist",
        postgresql_ops={"content": "gist_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="knowledge_document_chunks")
    # pg_trgm may be shared by other database objects, so downgrade keeps it installed.
