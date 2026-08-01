import asyncio
from datetime import datetime, timezone
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import get_db
from app.models import Document, DocumentChunk
from app.schemas import DocumentChunkResponse, DocumentCreate, DocumentResponse
from app.services.document_ingestion import FileTooLargeError, prepare_document, resolve_upload_directory
from app.services.document_parser import DocumentParseError, UnsupportedFileTypeError
from app.services.file_storage import remove_file, store_file


router = APIRouter(prefix="/knowledge/documents", tags=["knowledge"])
settings = get_settings()


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: DocumentCreate,
    db: AsyncSession = Depends(get_db),
) -> Document:
    document = Document(**payload.model_dump())
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: Annotated[UploadFile, File(description="支持 TXT、Markdown、PDF 和 DOCX，最大 20 MB")],
    title: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    db: AsyncSession = Depends(get_db),
) -> Document:
    try:
        prepared = await prepare_document(
            file,
            max_file_size=settings.max_upload_size,
            chunk_size=settings.document_chunk_size,
            overlap=settings.document_chunk_overlap,
        )
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except DocumentParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    document_title = (title or prepared.original_filename.rsplit(".", 1)[0]).strip()
    if not document_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="文档标题不能为空",
        )

    upload_directory = resolve_upload_directory(settings.upload_directory)
    stored_name, stored_path = await asyncio.to_thread(
        store_file, upload_directory, prepared.extension, prepared.content
    )
    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        title=document_title,
        source_type="upload",
        source_uri=f"upload://{stored_name}",
        status="ready",
        original_filename=prepared.original_filename,
        content_type=prepared.content_type,
        file_size=prepared.file_size,
        checksum_sha256=prepared.checksum_sha256,
        storage_path=stored_name,
        chunk_count=len(prepared.chunks),
        processed_at=datetime.now(timezone.utc),
    )
    chunks = [
        DocumentChunk(
            document_id=document_id,
            chunk_index=index,
            content=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
        )
        for index, chunk in enumerate(prepared.chunks)
    ]

    try:
        async with db.begin():
            db.add(document)
            db.add_all(chunks)
    except Exception:
        await asyncio.to_thread(remove_file, stored_path)
        raise

    await db.refresh(document)
    return document


@router.get("", response_model=list[DocumentResponse])
async def list_documents(db: AsyncSession = Depends(get_db)) -> list[Document]:
    result = await db.execute(select(Document).order_by(Document.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{document_id}/chunks", response_model=list[DocumentChunkResponse])
async def list_document_chunks(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentChunk]:
    document_exists = await db.scalar(select(Document.id).where(Document.id == document_id))
    if document_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    )
    return list(result.scalars().all())
