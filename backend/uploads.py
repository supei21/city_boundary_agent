from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from boundary_agent.paths import UPLOADS_DIR
from boundary_agent.spatial import MAX_UPLOAD_BYTES


UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


class UploadTooLargeError(ValueError):
    pass


async def persist_upload(file: UploadFile, original_name: str) -> tuple[Path, int]:
    """Stream an UploadFile to runtime storage with a configurable size limit."""

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(original_name).name or "uploaded-data.zip"
    target = UPLOADS_DIR / f"{uuid4().hex}_{safe_name}"
    total = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                    raise UploadTooLargeError(f"上传文件不能超过{limit_mb}MB")
                output.write(chunk)
        return target, total
    except Exception:
        target.unlink(missing_ok=True)
        raise
