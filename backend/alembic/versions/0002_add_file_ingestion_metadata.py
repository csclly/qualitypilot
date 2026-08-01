"""Add file ingestion metadata and chunk source offsets."""

from alembic import op
import sqlalchemy as sa


revision = "0002_file_ingestion"
down_revision = "0001_existing_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("original_filename", sa.String(255), nullable=True))
    op.add_column("knowledge_documents", sa.Column("content_type", sa.String(100), nullable=True))
    op.add_column("knowledge_documents", sa.Column("file_size", sa.BigInteger(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("checksum_sha256", sa.String(64), nullable=True))
    op.add_column("knowledge_documents", sa.Column("storage_path", sa.String(1000), nullable=True))
    op.add_column(
        "knowledge_documents",
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("knowledge_documents", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_document_chunks", sa.Column("char_start", sa.Integer(), nullable=True))
    op.add_column("knowledge_document_chunks", sa.Column("char_end", sa.Integer(), nullable=True))
    op.add_column(
        "knowledge_document_chunks",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("knowledge_document_chunks", "created_at")
    op.drop_column("knowledge_document_chunks", "char_end")
    op.drop_column("knowledge_document_chunks", "char_start")
    op.drop_column("knowledge_documents", "processed_at")
    op.drop_column("knowledge_documents", "chunk_count")
    op.drop_column("knowledge_documents", "storage_path")
    op.drop_column("knowledge_documents", "checksum_sha256")
    op.drop_column("knowledge_documents", "file_size")
    op.drop_column("knowledge_documents", "content_type")
    op.drop_column("knowledge_documents", "original_filename")
