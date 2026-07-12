import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, Device, Release
from ..schemas import ReleaseMetadata
from ..security import require_admin, require_device
from ..services.releases import file_sha256, is_android_apk, rollout_eligible

router = APIRouter()
settings = get_settings()


@router.post("/v1/admin/releases", dependencies=[Depends(require_admin)], status_code=201)
async def upload_release(
    metadata: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    parsed = ReleaseMetadata.model_validate_json(metadata)
    safe_name = Path(file.filename or "release.bin").name
    if safe_name != file.filename or safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="invalid filename")
    filename = f"{parsed.version_code}-{safe_name}"
    destination = settings.release_dir / filename
    digest = hashlib.sha256()
    size = 0
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 500 * 1024 * 1024:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="release too large")
            digest.update(chunk)
            output.write(chunk)
    item = Release(
        **parsed.model_dump(),
        filename=filename,
        file_size=size,
        sha256=digest.hexdigest(),
        status="draft",
    )
    db.add(item)
    db.add(
        AuditLog(
            action="release.upload",
            target=str(parsed.version_code),
            detail={"sha256": item.sha256},
        )
    )
    await db.commit()
    return {"id": item.id, "sha256": item.sha256, "file_size": size, "status": item.status}


@router.post("/v1/admin/releases/{version_code}/{action}", dependencies=[Depends(require_admin)])
async def release_action(version_code: int, action: str, db: AsyncSession = Depends(get_db)):
    if action not in {"publish", "promote", "pause", "resume", "rollback"}:
        raise HTTPException(status_code=400, detail="unsupported action")
    item = await db.scalar(select(Release).where(Release.version_code == version_code))
    if not item:
        raise HTTPException(status_code=404, detail="release not found")
    path = settings.release_dir / item.filename
    if not path.is_file() or file_sha256(path) != item.sha256:
        raise HTTPException(status_code=409, detail="release file hash mismatch")
    if action in {"publish", "promote", "resume"} and settings.environment == "production":
        if not is_android_apk(path):
            raise HTTPException(status_code=409, detail="production release is not a valid APK")
    item.status = {
        "publish": "published",
        "promote": "published",
        "pause": "paused",
        "resume": "published",
        "rollback": "rolled_back",
    }[action]
    if action == "promote":
        item.channel = "stable"
    if action in {"publish", "promote", "resume"}:
        item.published_at = datetime.now(UTC)
    db.add(AuditLog(action=f"release.{action}", target=str(version_code)))
    await db.commit()
    return {"version_code": version_code, "status": item.status, "channel": item.channel}


@router.get("/v1/device/releases/latest")
async def compatible_release(
    android_api: int,
    current_version_code: int,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    channels = ["stable", "beta"] if device.update_channel == "beta" else ["stable"]
    result = await db.execute(
        select(Release)
        .where(
            Release.status == "published",
            Release.min_android <= android_api,
            Release.version_code > current_version_code,
            Release.channel.in_(channels),
        )
        .order_by(Release.version_code.desc())
    )
    item = next(
        (value for value in result.scalars() if rollout_eligible(device.id, value.rollout_percent)),
        None,
    )
    if not item:
        return {"update": None}
    return {
        "update": {
            "version_code": item.version_code,
            "version_name": item.version_name,
            "download_url": f"/v1/device/releases/{item.version_code}/download",
            "file_size": item.file_size,
            "sha256": item.sha256,
            "forced": item.forced,
            "release_notes": item.release_notes,
            "channel": item.channel,
        }
    }


@router.get("/v1/device/releases/{version_code}/download")
async def download_release(
    version_code: int,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    channels = ["stable", "beta"] if device.update_channel == "beta" else ["stable"]
    item = await db.scalar(
        select(Release).where(
            Release.version_code == version_code,
            Release.status == "published",
            Release.channel.in_(channels),
        )
    )
    if not item or not rollout_eligible(device.id, item.rollout_percent):
        raise HTTPException(status_code=404, detail="release not found")
    path = (settings.release_dir / item.filename).resolve()
    root = settings.release_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="release file missing")
    return FileResponse(
        path, filename=Path(item.filename).name, headers={"X-Content-SHA256": item.sha256}
    )
