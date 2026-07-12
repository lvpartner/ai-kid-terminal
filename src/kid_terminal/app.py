import asyncio
import hashlib
import json
import logging
import shutil
import time
import uuid
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .audio import G711Ulaw8kEncoder, RealtimeAudioPacer
from .config import get_settings
from .db import SessionLocal, engine, get_db, init_db
from .knowledge import CurriculumKnowledgeBase
from .logging_config import configure_logging
from .models import (
    AuditLog,
    Conversation,
    Device,
    EnrollmentToken,
    LongTermMemory,
    Message,
    Release,
    RemoteConfig,
    Telemetry,
)
from .official_sources import OfficialSourceRetriever
from .privacy import redact_private_text, summarize_messages
from .prompts import DEFAULT_REMOTE_CONFIG, PROMPT_VERSION
from .providers import BufferedAudioProvider, ProviderError, QwenRealtimeProvider, create_provider
from .schemas import (
    ConfigUpdate,
    DeviceChannelUpdate,
    EnrollmentCreate,
    EnrollmentRequest,
    HeartbeatRequest,
    ReleaseMetadata,
    TelemetryRequest,
)
from .security import (
    authenticate_token,
    new_token,
    redact,
    require_admin,
    require_device,
    token_hash,
)
from .services.turn_orchestrator import TurnOrchestrator
from .services.turn_state import TurnState, TurnStateMachine
from .text_answer import CosyVoiceSynthesizer, DeepSeekAnswerer, QwenASRClient
from .web_research import (
    QwenTextSearchClient,
    WebEvidenceRetriever,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("kid_terminal")
REQUESTS = Counter(
    "kid_terminal_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
WS_SESSIONS = Counter("kid_terminal_ws_sessions_total", "WebSocket sessions", ["result"])
TURN_LATENCY = Histogram("kid_terminal_turn_seconds", "Voice turn duration")
curriculum_knowledge = CurriculumKnowledgeBase(settings.knowledge_db_path)
official_source_retriever = OfficialSourceRetriever()
web_evidence_retriever = WebEvidenceRetriever()
qwen_text_search = QwenTextSearchClient(settings)
deepseek_answerer = DeepSeekAnswerer(settings)
cosyvoice_synthesizer = CosyVoiceSynthesizer(settings)
qwen_asr = QwenASRClient(settings)
turn_orchestrator = TurnOrchestrator(
    knowledge=curriculum_knowledge,
    official_sources=official_source_retriever,
    web_sources=web_evidence_retriever,
    web_search=qwen_text_search,
    answerer=deepseek_answerer,
)


class DeviceConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, dict[int, WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._active_responses = 0
        self._draining = False
        self.upstream_status = "not_checked"

    async def add(self, device_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(device_id, {})[id(ws)] = ws

    async def remove(self, device_id: str, ws: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(device_id)
            if connections:
                connections.pop(id(ws), None)
                if not connections:
                    self._connections.pop(device_id, None)

    async def broadcast_config(self, version: int, prompt_version: str) -> int:
        async with self._lock:
            targets = [
                ws for connections in self._connections.values() for ws in connections.values()
            ]
        message = {
            "type": "config.changed",
            "version": version,
            "prompt_version": prompt_version,
        }
        results = await asyncio.gather(
            *(ws_send(ws, message) for ws in targets), return_exceptions=True
        )
        return sum(not isinstance(result, Exception) for result in results)

    async def start_response(self) -> bool:
        async with self._lock:
            if self._draining:
                return False
            self._active_responses += 1
            return True

    async def finish_response(self) -> None:
        async with self._lock:
            self._active_responses = max(0, self._active_responses - 1)

    async def set_draining(self, enabled: bool) -> None:
        async with self._lock:
            self._draining = enabled

    async def activity(self) -> dict[str, int | bool]:
        async with self._lock:
            return {
                "websockets": sum(len(items) for items in self._connections.values()),
                "active_responses": self._active_responses,
                "draining": self._draining,
            }


connections = DeviceConnectionManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.release_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    async with SessionLocal() as db:
        count = await db.scalar(select(func.count()).select_from(RemoteConfig))
        if not count:
            db.add(
                RemoteConfig(version=1, data=DEFAULT_REMOTE_CONFIG, prompt_version=PROMPT_VERSION)
            )
            await db.commit()
    yield
    # Mark devices offline so readiness after a restart is deterministic.
    async with SessionLocal() as db:
        await db.execute(update(Device).values(online=False))
        await db.commit()
    await asyncio.gather(
        qwen_asr.aclose(),
        official_source_retriever.aclose(),
        web_evidence_retriever.aclose(),
        qwen_text_search.aclose(),
        deepseek_answerer.aclose(),
        cosyvoice_synthesizer.aclose(),
    )
    await engine.dispose()


app = FastAPI(title="AI Kid Terminal", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:100]
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled request error", extra={"request_id": request_id})
        response = JSONResponse(
            status_code=500, content={"error": "internal_error", "request_id": request_id}
        )
    response.headers["X-Request-ID"] = request_id
    REQUESTS.labels(request.method, request.url.path, response.status_code).inc()
    logger.info(
        "request",
        extra={"request_id": request_id, "duration_ms": int((time.monotonic() - started) * 1000)},
    )
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready", "provider": settings.ai_provider}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/admin/activity", dependencies=[Depends(require_admin)])
async def admin_activity():
    return await connections.activity()


@app.post("/v1/admin/drain", dependencies=[Depends(require_admin)])
async def admin_drain(enabled: bool = True):
    await connections.set_draining(enabled)
    return await connections.activity()


@app.get("/version")
async def version():
    return {"version": __version__, "protocol_versions": [1], "prompt_version": PROMPT_VERSION}


@app.post("/v1/enroll", status_code=201)
async def enroll(payload: EnrollmentRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(EnrollmentToken).where(
            EnrollmentToken.token_hash == token_hash(payload.enrollment_token)
        )
    )
    enrollment = result.scalar_one_or_none()
    current = datetime.now(UTC)
    if not enrollment or enrollment.used_at or enrollment.expires_at.replace(tzinfo=UTC) <= current:
        raise HTTPException(status_code=401, detail="invalid or expired enrollment token")
    access_token = new_token("dev")
    device = Device(
        name=payload.device_name,
        token_hash=token_hash(access_token),
        token_expires_at=current + timedelta(days=settings.access_token_days),
        app_version=payload.app_version,
        os_version=payload.os_version,
        manufacturer=payload.manufacturer,
        device_model=payload.device_model,
        security_patch=payload.security_patch,
    )
    enrollment.used_at = current
    db.add(device)
    await db.commit()
    return {
        "device_id": device.id,
        "access_token": access_token,
        "expires_at": device.token_expires_at,
    }


@app.post("/v1/admin/enrollments", dependencies=[Depends(require_admin)], status_code=201)
async def create_enrollment(payload: EnrollmentCreate, db: AsyncSession = Depends(get_db)):
    raw_token = new_token("enroll")
    item = EnrollmentToken(
        token_hash=token_hash(raw_token),
        label=payload.label,
        expires_at=datetime.now(UTC) + timedelta(minutes=payload.expires_minutes),
    )
    db.add(item)
    db.add(AuditLog(action="enrollment.create", target=item.id, detail={"label": payload.label}))
    await db.commit()
    return {"enrollment_token": raw_token, "expires_at": item.expires_at}


@app.get("/v1/admin/devices", dependencies=[Depends(require_admin)])
async def list_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.created_at.desc()))
    return [
        {
            "id": item.id,
            "name": item.name,
            "online": item.online,
            "revoked": item.revoked_at is not None,
            "last_seen_at": item.last_seen_at,
            "app_version": item.app_version,
            "os_version": item.os_version,
            "manufacturer": item.manufacturer,
            "device_model": item.device_model,
            "security_patch": item.security_patch,
            "network_type": item.network_type,
            "update_channel": item.update_channel,
        }
        for item in result.scalars()
    ]


@app.post("/v1/admin/devices/{device_id}/revoke", dependencies=[Depends(require_admin)])
async def revoke_device(device_id: str, db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    device.revoked_at = datetime.now(UTC)
    device.online = False
    db.add(AuditLog(action="device.revoke", target=device_id))
    await db.commit()
    return {"revoked": True}


@app.post("/v1/admin/devices/{device_id}/update-channel", dependencies=[Depends(require_admin)])
async def set_device_update_channel(
    device_id: str,
    payload: DeviceChannelUpdate,
    db: AsyncSession = Depends(get_db),
):
    device = await db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    device.update_channel = payload.channel
    db.add(
        AuditLog(
            action="device.update_channel",
            target=device_id,
            detail={"channel": payload.channel},
        )
    )
    await db.commit()
    return {"device_id": device_id, "update_channel": device.update_channel}


@app.post("/v1/admin/devices/{device_id}/rotate", dependencies=[Depends(require_admin)])
async def rotate_device_token(device_id: str, db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    if not device or device.revoked_at:
        raise HTTPException(status_code=404, detail="active device not found")
    raw_token = new_token("dev")
    device.token_hash = token_hash(raw_token)
    device.token_expires_at = datetime.now(UTC) + timedelta(days=settings.access_token_days)
    db.add(AuditLog(action="device.token.rotate", target=device_id))
    await db.commit()
    return {"access_token": raw_token, "expires_at": device.token_expires_at}


@app.get("/v1/admin/devices/{device_id}/status", dependencies=[Depends(require_admin)])
async def device_status(device_id: str, db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    latest = await db.scalar(
        select(Telemetry)
        .where(Telemetry.device_id == device_id)
        .order_by(Telemetry.created_at.desc())
        .limit(1)
    )
    return {
        "device": {
            "id": device.id,
            "name": device.name,
            "online": device.online,
            "last_seen_at": device.last_seen_at,
            "app_version": device.app_version,
            "os_version": device.os_version,
            "manufacturer": device.manufacturer,
            "device_model": device.device_model,
            "security_patch": device.security_patch,
            "network_type": device.network_type,
        },
        "latest_telemetry": None
        if not latest
        else {
            "type": latest.event_type,
            "severity": latest.severity,
            "created_at": latest.created_at,
        },
    }


@app.get("/v1/admin/faults", dependencies=[Depends(require_admin)])
async def recent_faults(
    device_id: str | None = None,
    session_id: str | None = None,
    since_hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    query = select(Telemetry).where(
        Telemetry.severity.in_(["warning", "error"]),
        Telemetry.created_at >= datetime.now(UTC) - timedelta(hours=min(max(since_hours, 1), 720)),
    )
    if device_id:
        query = query.where(Telemetry.device_id == device_id)
    if session_id:
        query = query.where(Telemetry.session_id == session_id)
    result = await db.execute(query.order_by(Telemetry.created_at.desc()).limit(100))
    return [
        {
            "device_id": item.device_id,
            "session_id": item.session_id,
            "type": item.event_type,
            "severity": item.severity,
            "data": redact(item.data),
            "created_at": item.created_at,
        }
        for item in result.scalars()
    ]


@app.get("/v1/admin/diagnose", dependencies=[Depends(require_admin)])
async def diagnose(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    disk = shutil.disk_usage(Path.cwd())
    qwen_configured = bool(settings.dashscope_api_key)
    return {
        "database": "ok",
        "disk_free_bytes": disk.free,
        "disk_ok": disk.free > 512 * 1024 * 1024,
        "provider": settings.ai_provider,
        "qwen_configured": qwen_configured,
        "qwen_status": (
            connections.upstream_status if settings.ai_provider == "qwen" else "not_used"
        ),
    }


@app.post("/v1/device/heartbeat")
async def heartbeat(
    payload: HeartbeatRequest,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    device.online = True
    device.last_seen_at = datetime.now(UTC)
    device.app_version = payload.app_version
    device.os_version = payload.os_version
    device.manufacturer = payload.manufacturer
    device.device_model = payload.device_model
    device.security_patch = payload.security_patch
    device.network_type = payload.network_type
    db.add(
        Telemetry(
            device_id=device.id,
            event_type="heartbeat",
            data=redact(payload.model_dump()),
        )
    )
    await db.commit()
    return {"status": "ok", "server_time": datetime.now(UTC)}


@app.post("/v1/device/telemetry", status_code=202)
async def telemetry(
    payload: TelemetryRequest,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump(exclude={"crash_stack"})
    data["events"] = [redact(event) for event in payload.events[-50:]]
    db.add(
        Telemetry(
            device_id=device.id,
            session_id=payload.session_id,
            event_type=payload.event_type,
            severity=payload.severity,
            data=data,
            crash_stack=redact_private_text(payload.crash_stack or "") or None,
        )
    )
    await db.commit()
    return {"accepted": True}


async def latest_config(db: AsyncSession) -> RemoteConfig:
    result = await db.execute(select(RemoteConfig).order_by(RemoteConfig.version.desc()).limit(1))
    return result.scalar_one()


@app.get("/v1/device/config")
async def get_config(
    request: Request,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    config = await latest_config(db)
    etag = f'"config-{config.version}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(
        {"version": config.version, "prompt_version": config.prompt_version, "config": config.data},
        headers={"ETag": etag, "Cache-Control": "private, max-age=30"},
    )


@app.put("/v1/admin/config", dependencies=[Depends(require_admin)])
async def update_config(payload: ConfigUpdate, db: AsyncSession = Depends(get_db)):
    current = await latest_config(db)
    item = RemoteConfig(
        version=current.version + 1,
        data=payload.model_dump(),
        prompt_version=PROMPT_VERSION,
    )
    db.add(item)
    db.add(
        AuditLog(action="config.update", target=str(item.version), detail={"version": item.version})
    )
    await db.commit()
    notified = await connections.broadcast_config(item.version, item.prompt_version)
    return {"version": item.version, "notified_connections": notified}


def rollout_eligible(device_id: str, percent: float) -> bool:
    bucket = int(hashlib.sha256(device_id.encode()).hexdigest()[:8], 16) % 10_000 / 100
    return bucket < percent


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def is_android_apk(path: Path) -> bool:
    if path.suffix.lower() != ".apk" or not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    return {"AndroidManifest.xml", "classes.dex", "resources.arsc"} <= names


@app.post("/v1/admin/releases", dependencies=[Depends(require_admin)], status_code=201)
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
            action="release.upload", target=str(parsed.version_code), detail={"sha256": item.sha256}
        )
    )
    await db.commit()
    return {"id": item.id, "sha256": item.sha256, "file_size": size, "status": item.status}


@app.post("/v1/admin/releases/{version_code}/{action}", dependencies=[Depends(require_admin)])
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


@app.get("/v1/device/releases/latest")
async def compatible_release(
    android_api: int,
    current_version_code: int,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Release)
        .where(
            Release.status == "published",
            Release.min_android <= android_api,
            Release.version_code > current_version_code,
            Release.channel.in_(
                ["stable", "beta"] if device.update_channel == "beta" else ["stable"]
            ),
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


@app.get("/v1/device/releases/{version_code}/download")
async def download_release(
    version_code: int,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    item = await db.scalar(
        select(Release).where(
            Release.version_code == version_code,
            Release.status == "published",
            Release.channel.in_(
                ["stable", "beta"] if device.update_channel == "beta" else ["stable"]
            ),
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


async def store_turn(
    db: AsyncSession, device_id: str, conversation_id: str, user_text: str, ai_text: str
) -> None:
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        conversation = Conversation(
            id=conversation_id,
            device_id=device_id,
            profile_id="default",
            message_count=0,
        )
        db.add(conversation)
    clean_user = redact_private_text(user_text or "[语音输入，未保存原始音频]")
    clean_ai = redact_private_text(ai_text)
    db.add_all(
        [
            Message(conversation_id=conversation_id, role="user", content=clean_user),
            Message(conversation_id=conversation_id, role="assistant", content=clean_ai),
        ]
    )
    conversation.message_count = (conversation.message_count or 0) + 2
    conversation.updated_at = datetime.now(UTC)
    if settings.enable_long_term_memory and conversation.message_count >= 10:
        rows = await db.scalars(
            select(Message.content)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(8)
        )
        conversation.summary = summarize_messages(list(rows))
        db.add(
            LongTermMemory(
                device_id=device_id,
                content=conversation.summary,
                expires_at=datetime.now(UTC) + timedelta(days=settings.memory_retention_days),
            )
        )
    await db.commit()


async def memory_context(
    db: AsyncSession, device_id: str, conversation: Conversation | None = None
) -> str:
    parts: list[str] = []
    if conversation and conversation.summary:
        parts.append(conversation.summary)
    result = await db.scalars(
        select(LongTermMemory.content)
        .where(
            LongTermMemory.device_id == device_id,
            LongTermMemory.expires_at > datetime.now(UTC),
        )
        .order_by(LongTermMemory.created_at.desc())
        .limit(5)
    )
    parts.extend(result)
    unique = list(dict.fromkeys(redact_private_text(part) for part in parts if part))
    return "\n".join(unique)[:4000]


async def recent_turn_context(db: AsyncSession, device_id: str, turns: int = 8) -> str:
    """Render the newest completed dialogue turns across all device sessions."""
    rows = (
        await db.execute(
            select(Message.role, Message.content)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.device_id == device_id)
            .order_by(Message.created_at.desc())
            .limit(turns * 2)
        )
    ).all()
    labels = {"user": "孩子", "assistant": "助手"}
    lines = [
        f"{labels.get(role, role)}：{redact_private_text(content)}"
        for role, content in reversed(rows)
        if content
    ]
    while lines and sum(len(line) + 1 for line in lines) > 6000:
        lines.pop(0)
    return "\n".join(lines)


@app.delete("/v1/admin/devices/{device_id}/data", dependencies=[Depends(require_admin)])
async def delete_device_data(device_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Conversation).where(Conversation.device_id == device_id))
    await db.execute(delete(LongTermMemory).where(LongTermMemory.device_id == device_id))
    await db.execute(delete(Telemetry).where(Telemetry.device_id == device_id))
    db.add(AuditLog(action="device.data.delete", target=device_id))
    await db.commit()
    return {"deleted": True}


async def ws_send(ws: WebSocket, message: dict[str, Any]) -> None:
    try:
        await asyncio.wait_for(ws.send_json(message), timeout=5)
    except TimeoutError as exc:
        raise RuntimeError("slow client") from exc


@app.websocket("/v1/device/ws")
async def device_ws(ws: WebSocket):
    await ws.accept(subprotocol="kid-terminal.v1")
    authorization = ws.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    async with SessionLocal() as db:
        device = await authenticate_token(token, db)
        if not device:
            await ws.close(code=4401, reason="unauthorized")
            WS_SESSIONS.labels("unauthorized").inc()
            return
        device.online = True
        device.last_seen_at = datetime.now(UTC)
        await db.commit()
        await connections.add(device.id, ws)
        provider = create_provider(settings)
        provider_session = None
        conversation_id = str(uuid.uuid4())
        event_ids: deque[str] = deque(maxlen=1000)
        timestamps: deque[float] = deque()
        ai_text = ""
        user_text = ""
        response_task: asyncio.Task[None] | None = None
        response_output_complete = asyncio.Event()
        output_codec = (
            "g711_ulaw_8000_mono"
            if "g711_ulaw_8000" in ws.headers.get("x-audio-codecs", "")
            else "pcm_s16le_24000_mono"
        )
        try:
            config = (await latest_config(db)).data.copy()
            config["memory_context"] = await memory_context(db, device.id)
            config["standalone_transcription"] = deepseek_answerer.enabled
            provider_session = await provider.open(config)
            await ws_send(
                ws,
                {
                    "type": "session.ready",
                    "protocol_version": 1,
                    "session_id": conversation_id,
                    "resume_token": conversation_id,
                    "audio": {"input": "pcm_s16le_16000_mono", "output": output_codec},
                },
            )
            while True:
                try:
                    incoming = await asyncio.wait_for(
                        ws.receive(), timeout=settings.ws_idle_seconds
                    )
                except TimeoutError:
                    # Long searched answers can exceed the inbound idle window while audio is
                    # still flowing out. The provider has its own bounded event timeout.
                    if response_task and not response_task.done():
                        continue
                    raise
                now_mono = time.monotonic()
                timestamps.append(now_mono)
                while timestamps and timestamps[0] < now_mono - 1:
                    timestamps.popleft()
                if len(timestamps) > settings.ws_events_per_second:
                    await ws_send(ws, {"type": "error", "code": "rate_limited", "retryable": True})
                    await ws.close(code=4429)
                    break
                if incoming.get("bytes") is not None:
                    data = incoming["bytes"]
                    if len(data) > settings.ws_max_message_bytes:
                        await ws.close(code=4409, reason="message too large")
                        break
                    await provider.append_audio(provider_session, data)
                    continue
                raw = incoming.get("text")
                if raw is None:
                    break
                if len(raw.encode()) > settings.ws_max_message_bytes:
                    await ws.close(code=4409, reason="message too large")
                    break
                event = json.loads(raw)
                event_id = str(event.get("event_id", ""))[:100]
                if not event_id:
                    await ws_send(
                        ws, {"type": "error", "code": "missing_event_id", "retryable": False}
                    )
                    continue
                if event_id in event_ids:
                    await ws_send(
                        ws, {"type": "event.ack", "event_id": event_id, "duplicate": True}
                    )
                    continue
                event_ids.append(event_id)
                event_type = event.get("type")
                if event_type == "heartbeat":
                    device.last_seen_at = datetime.now(UTC)
                    await db.commit()
                    await ws_send(ws, {"type": "heartbeat.ack", "event_id": event_id})
                elif event_type == "session.resume":
                    requested = str(event.get("resume_token", ""))
                    existing = await db.get(Conversation, requested)
                    if existing and existing.device_id == device.id:
                        conversation_id = existing.id
                        await provider.set_context(
                            provider_session,
                            await memory_context(db, device.id, existing),
                        )
                    await ws_send(ws, {"type": "session.resumed", "session_id": conversation_id})
                elif event_type == "speech.start":
                    user_text = str(event.get("text_hint", ""))[:4000]
                    ai_text = ""
                    await provider.start_turn(provider_session)
                    await ws_send(ws, {"type": "speech.started", "event_id": event_id})
                elif event_type == "speech.stop":
                    if response_task and not response_task.done():
                        await ws_send(
                            ws, {"type": "error", "code": "response_in_progress", "retryable": True}
                        )
                        continue
                    if not await connections.start_response():
                        await ws_send(
                            ws,
                            {"type": "error", "code": "service_draining", "retryable": True},
                        )
                        continue
                    response_output_complete.clear()

                    async def stream_response(
                        response_event_id: str = event_id,
                        response_conversation_id: str = conversation_id,
                        response_user_text: str = user_text,
                        response_provider_session: Any = provider_session,
                    ) -> None:
                        nonlocal ai_text
                        resolved_user_text = response_user_text
                        response_started = time.monotonic()
                        audio_bytes = 0
                        wire_bytes = 0
                        audio_chunks = 0
                        encoder = G711Ulaw8kEncoder() if output_codec.startswith("g711") else None
                        pacer = RealtimeAudioPacer() if encoder else None
                        outbound: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=512)
                        producer_task: asyncio.Task[None] | None = None
                        context_task: asyncio.Task[str] | None = None
                        turn_state = TurnStateMachine(entered_at=response_started)
                        strict_grounding = settings.strict_grounding and isinstance(
                            provider, BufferedAudioProvider | QwenRealtimeProvider
                        )
                        standalone_transcription = strict_grounding and deepseek_answerer.enabled
                        grounding_requested = False

                        async def load_context() -> str:
                            async with SessionLocal() as context_db:
                                return await recent_turn_context(
                                    context_db,
                                    device.id,
                                    turns=settings.context_turns,
                                )

                        async def collect_provider_output() -> None:
                            nonlocal audio_bytes, audio_chunks
                            try:
                                async for kind, value in provider.respond(
                                    response_provider_session
                                ):
                                    if kind == "audio":
                                        audio_bytes += len(value)
                                        audio_chunks += 1
                                        value = encoder.encode(value) if encoder else value
                                        kind = "wire_audio"
                                    await outbound.put((kind, value))
                            except ProviderError as exc:
                                await outbound.put(("provider_error", exc))
                            finally:
                                await outbound.put(("complete", None))

                        async def collect_transcription_output() -> None:
                            try:
                                transcript = await qwen_asr.transcribe(
                                    bytes(response_provider_session.audio)
                                )
                                await outbound.put(("user_text", transcript))
                            except ProviderError as exc:
                                await outbound.put(("provider_error", exc))
                            finally:
                                await outbound.put(("complete", None))

                        try:
                            await ws_send(
                                ws,
                                {"type": "ai.response.started", "event_id": response_event_id},
                            )
                            producer_task = asyncio.create_task(
                                collect_transcription_output()
                                if standalone_transcription
                                else collect_provider_output()
                            )
                            if strict_grounding:
                                context_task = asyncio.create_task(load_context())
                                turn_state.transition(TurnState.TRANSCRIBING)
                            while True:
                                kind, value = await outbound.get()
                                if kind == "complete":
                                    break
                                if kind == "provider_error":
                                    raise value
                                if kind == "wire_audio":
                                    if strict_grounding:
                                        continue
                                    if value:
                                        if pacer:
                                            delay = pacer.delay_for(len(value))
                                            if delay > 0:
                                                await asyncio.sleep(delay)
                                        await asyncio.wait_for(ws.send_bytes(value), timeout=5)
                                        if pacer:
                                            pacer.record_sent(len(value))
                                        wire_bytes += len(value)
                                elif kind == "text":
                                    if not strict_grounding:
                                        ai_text += value
                                        await ws_send(ws, {"type": "ai.text.delta", "text": value})
                                elif kind == "user_text":
                                    resolved_user_text = str(value)[:4000]
                                    if strict_grounding and not grounding_requested:
                                        grounding_requested = True
                                        if not standalone_transcription:
                                            await provider.interrupt(response_provider_session)
                                elif kind == "interrupted":
                                    if not grounding_requested:
                                        await ws_send(ws, {"type": "ai.response.interrupted"})
                            if strict_grounding:
                                if not resolved_user_text.strip():
                                    raise ProviderError(
                                        "Qwen returned no transcription",
                                        retryable=True,
                                        code="transcription_missing",
                                    )
                                grade = (
                                    int(config.get("grade_min", 1))
                                    + int(config.get("grade_max", 6))
                                ) // 2
                                turn_state.transition(TurnState.RESEARCHING)
                                conversation_context = await context_task if context_task else ""
                                prepared = await turn_orchestrator.prepare(
                                    resolved_user_text,
                                    grade=grade,
                                    conversation_context=conversation_context,
                                )
                                turn_state.transition(TurnState.VALIDATING)
                                ai_text = prepared.text
                                await ws_send(ws, {"type": "ai.text.delta", "text": ai_text})
                                turn_state.transition(TurnState.SYNTHESIZING)
                                first_audio = True
                                async for value in cosyvoice_synthesizer.stream(ai_text):
                                    audio_bytes += len(value)
                                    audio_chunks += 1
                                    encoded = encoder.encode(value) if encoder else value
                                    if not encoded:
                                        continue
                                    if first_audio:
                                        turn_state.transition(TurnState.PLAYING)
                                        first_audio = False
                                    if pacer:
                                        delay = pacer.delay_for(len(encoded))
                                        if delay > 0:
                                            await asyncio.sleep(delay)
                                    await asyncio.wait_for(ws.send_bytes(encoded), timeout=5)
                                    if pacer:
                                        pacer.record_sent(len(encoded))
                                    wire_bytes += len(encoded)
                                if turn_state.state == TurnState.SYNTHESIZING:
                                    turn_state.transition(TurnState.COMPLETED)
                                elif turn_state.state == TurnState.PLAYING:
                                    turn_state.transition(TurnState.COMPLETED)
                            response_output_complete.set()
                            async with SessionLocal() as turn_db:
                                await store_turn(
                                    turn_db,
                                    device.id,
                                    response_conversation_id,
                                    resolved_user_text,
                                    ai_text,
                                )
                            connections.upstream_status = "ok"
                            TURN_LATENCY.observe(time.monotonic() - response_started)
                        except asyncio.CancelledError:
                            turn_state.interrupt()
                            raise
                        except ProviderError as exc:
                            turn_state.fail()
                            if exc.code == "account_unavailable":
                                connections.upstream_status = "degraded"
                            logger.warning(
                                "provider response failed error=%s retryable=%s "
                                "after_audio_bytes=%s",
                                exc,
                                exc.retryable,
                                audio_bytes,
                                extra={
                                    "device_id": device.id,
                                    "session_id": response_conversation_id,
                                },
                            )
                            client_error = (
                                "speech_not_recognized"
                                if exc.code in {"transcription_missing", "transcription_error"}
                                else "upstream_error"
                            )
                            await ws_send(
                                ws,
                                {
                                    "type": "error",
                                    "code": client_error,
                                    "upstream_code": exc.code or "unknown",
                                    "retryable": exc.retryable,
                                },
                            )
                        finally:
                            if strict_grounding and turn_state.state not in {
                                TurnState.COMPLETED,
                                TurnState.INTERRUPTED,
                                TurnState.FAILED,
                            }:
                                turn_state.fail()
                            if producer_task:
                                producer_task.cancel()
                                await asyncio.gather(producer_task, return_exceptions=True)
                            if context_task:
                                context_task.cancel()
                                await asyncio.gather(context_task, return_exceptions=True)
                            logger.info(
                                "voice response finished codec=%s chunks=%s source_bytes=%s "
                                "wire_bytes=%s elapsed_ms=%s state=%s stages_ms=%s",
                                output_codec,
                                audio_chunks,
                                audio_bytes,
                                wire_bytes,
                                int((time.monotonic() - response_started) * 1000),
                                turn_state.state.value,
                                turn_state.durations_ms,
                                extra={
                                    "device_id": device.id,
                                    "session_id": response_conversation_id,
                                },
                            )
                            await connections.finish_response()
                            await ws_send(
                                ws,
                                {
                                    "type": "ai.response.done",
                                    "session_id": response_conversation_id,
                                },
                            )

                    response_task = asyncio.create_task(stream_response())
                elif event_type == "interrupt":
                    await provider.interrupt(provider_session)
                    if response_task and not response_task.done():
                        if not response_output_complete.is_set():
                            response_task.cancel()
                        await asyncio.gather(response_task, return_exceptions=True)
                        await ws_send(ws, {"type": "ai.response.interrupted"})
                    await provider.close(provider_session)
                    provider_session = await provider.open(config)
                    await ws_send(ws, {"type": "interrupt.ack", "event_id": event_id})
                else:
                    await ws_send(
                        ws, {"type": "error", "code": "unknown_event", "retryable": False}
                    )
        except WebSocketDisconnect as exc:
            logger.info(
                "device websocket disconnected code=%s reason=%s",
                exc.code,
                exc.reason,
                extra={"device_id": device.id, "session_id": conversation_id},
            )
            WS_SESSIONS.labels("disconnected").inc()
        except (TimeoutError, json.JSONDecodeError):
            await ws.close(code=4400, reason="invalid or idle connection")
            WS_SESSIONS.labels("protocol_error").inc()
        except Exception:
            logger.exception(
                "websocket failure", extra={"device_id": device.id, "session_id": conversation_id}
            )
            try:
                await ws_send(ws, {"type": "error", "code": "upstream_error", "retryable": True})
                await ws.close(code=4511)
            except Exception:
                logger.debug("websocket already closed", exc_info=True)
            WS_SESSIONS.labels("error").inc()
        finally:
            if response_task and not response_task.done():
                response_task.cancel()
                await asyncio.gather(response_task, return_exceptions=True)
            if provider_session:
                await provider.close(provider_session)
            await connections.remove(device.id, ws)
            device.online = False
            device.last_seen_at = datetime.now(UTC)
            await db.commit()
            await db.close()


@app.post("/v1/admin/cleanup", dependencies=[Depends(require_admin)])
async def cleanup(db: AsyncSession = Depends(get_db)):
    memory_cutoff = datetime.now(UTC)
    conversation_cutoff = datetime.now(UTC) - timedelta(hours=settings.conversation_retention_hours)
    telemetry_cutoff = datetime.now(UTC) - timedelta(days=settings.telemetry_retention_days)
    memory_result = await db.execute(
        delete(LongTermMemory).where(LongTermMemory.expires_at < memory_cutoff)
    )
    telemetry_result = await db.execute(
        delete(Telemetry).where(Telemetry.created_at < telemetry_cutoff)
    )
    message_result = await db.execute(
        delete(Message).where(Message.created_at < conversation_cutoff)
    )
    conversation_result = await db.execute(
        delete(Conversation).where(Conversation.updated_at < conversation_cutoff)
    )
    db.add(AuditLog(action="retention.cleanup"))
    await db.commit()
    return {
        "memories": memory_result.rowcount,
        "messages": message_result.rowcount,
        "conversations": conversation_result.rowcount,
        "telemetry": telemetry_result.rowcount,
    }
