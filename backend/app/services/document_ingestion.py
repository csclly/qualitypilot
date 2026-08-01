import asyncio
from dataclasses import dataclass
from pathlib import Path
from hashlib import sha256

from fastapi import UploadFile

from app.services.document_parser import (
    DocumentParseError,
    extract_plain_text,
    get_supported_extension,
    sanitize_filename,
)
from app.services.text_splitter import TextChunk, split_text


MAX_FILE_SIZE = 20 * 1024 * 1024
READ_BLOCK_SIZE = 1024 * 1024


class FileTooLargeError(ValueError):
    """Raised when an upload is larger than the configured limit."""


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    original_filename: str
    extension: str
    content_type: str
    file_size: int
    checksum_sha256: str
    content: bytes
    text: str
    chunks: list[TextChunk]


async def prepare_document(
    upload: UploadFile,
    *,
    max_file_size: int = MAX_FILE_SIZE,
    chunk_size: int = 800,
    overlap: int = 100,
) -> PreparedDocument:
    original_filename = sanitize_filename(upload.filename)
    extension = get_supported_extension(original_filename)
    content = await read_limited_upload(upload, max_file_size=max_file_size)
    text = await asyncio.to_thread(extract_plain_text, original_filename, content)
    chunks = split_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise DocumentParseError("文件中没有可切分的文本")
    return PreparedDocument(
        original_filename=original_filename,
        extension=extension,
        content_type=upload.content_type or "application/octet-stream",
        file_size=len(content),
        checksum_sha256=sha256(content).hexdigest(),
        content=content,
        text=text,
        chunks=chunks,
    )


async def read_limited_upload(upload: UploadFile, *, max_file_size: int) -> bytes:
    blocks: list[bytes] = []
    total_size = 0
    try:
        while block := await upload.read(READ_BLOCK_SIZE):
            total_size += len(block)
            if total_size > max_file_size:
                raise FileTooLargeError(f"文件大小不能超过 {max_file_size // 1024 // 1024} MB")
            blocks.append(block)
    finally:
        await upload.close()
    if total_size == 0:
        raise DocumentParseError("文件内容为空")
    return b"".join(blocks)


def resolve_upload_directory(configured_path: str) -> Path:
    return Path(configured_path).expanduser().resolve()
