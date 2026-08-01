from io import BytesIO

from fastapi import UploadFile
import pytest
from starlette.datastructures import Headers

from app.services.document_ingestion import FileTooLargeError, prepare_document


def make_upload(filename: str, content: bytes, content_type: str = "text/plain") -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def test_prepares_document_metadata_and_chunks() -> None:
    content = ("质量异常分析。" * 200).encode()

    prepared = await prepare_document(make_upload("quality.txt", content))

    assert prepared.original_filename == "quality.txt"
    assert prepared.file_size == len(content)
    assert len(prepared.checksum_sha256) == 64
    assert len(prepared.chunks) > 1
    assert prepared.chunks[0].char_start == 0


async def test_rejects_file_larger_than_limit() -> None:
    upload = make_upload("large.txt", b"a" * 11)

    with pytest.raises(FileTooLargeError):
        await prepare_document(upload, max_file_size=10)
