from pathlib import Path
import uuid


def store_file(upload_directory: Path, extension: str, content: bytes) -> tuple[str, Path]:
    upload_directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = upload_directory / stored_name
    temporary = upload_directory / f".{stored_name}.tmp"
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return stored_name, destination


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)
