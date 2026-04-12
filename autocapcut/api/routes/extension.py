"""Extension bridge — nhận input từ Chrome extension và broadcast real-time qua WebSocket."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel

router = APIRouter()


# ── Connection Manager ─────────────────────────────────────────────────────────

class ConnectionManager:
    """Quản lý tất cả WebSocket clients đang kết nối từ frontend."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("[extension] WS client connected — total: %d", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("[extension] WS client disconnected — total: %d", len(self._clients))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Gửi data đến tất cả clients, tự động dọn dẹp dead connections."""
        dead: set[WebSocket] = set()
        for client in list(self._clients):
            try:
                await client.send_json(data)
            except Exception:
                dead.add(client)
        for ws in dead:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


manager = ConnectionManager()


# ── Schemas ────────────────────────────────────────────────────────────────────

class ExtensionPushRequest(BaseModel):
    feature: str           # "ai_gen" | "ai_audio" | "srt_gen" | "raw_seo" | "resource_prep" | "account_connect"
    content: str
    parent_project_id: int | None = None
    child_project_id: int | None = None


class ExtensionPushResponse(BaseModel):
    ok: bool
    message: str
    clients_notified: int


class FeatureInfo(BaseModel):
    id: str
    label: str
    description: str


class FeaturesResponse(BaseModel):
    features: list[FeatureInfo]


# ── Feature Registry ───────────────────────────────────────────────────────────

AVAILABLE_FEATURES: list[FeatureInfo] = [
    FeatureInfo(
        id="ai_gen",
        label="AIGen — Gen Ảnh",
        description="Tự động fill prompt vào ô Gen Ảnh",
    ),
    FeatureInfo(
        id="ai_audio",
        label="AIAudio — Text to Speech",
        description="Tự động fill script vào ô TTS",
    ),
    FeatureInfo(
        id="srt_gen",
        label="SRTGen — Tạo SRT",
        description="Tự động fill nội dung vào ô tạo phụ đề",
    ),
    FeatureInfo(
        id="account_connect",
        label="YTB Connect — YouTube Pairing",
        description="Nhận trạng thái pair channel từ YTB Connect extension",
    ),
]


# ── HTTP Routes ────────────────────────────────────────────────────────────────

@router.get("/features", response_model=FeaturesResponse)
async def get_features() -> FeaturesResponse:
    """Trả về danh sách features có thể nhận data từ extension."""
    return FeaturesResponse(features=AVAILABLE_FEATURES)


@router.post("/push", response_model=ExtensionPushResponse)
async def push_from_extension(req: ExtensionPushRequest) -> ExtensionPushResponse:
    """
    Nhận content từ Chrome extension và broadcast real-time đến tất cả
    frontend clients đang kết nối qua WebSocket.
    """
    logger.info(
        "[extension] Push nhận được — feature=%s, content_len=%d, clients=%d",
        req.feature,
        len(req.content),
        manager.client_count,
    )

    clients_before = manager.client_count

    await manager.broadcast({
        "type": "extension_push",
        "feature": req.feature,
        "content": req.content,
        "parent_project_id": req.parent_project_id,
        "child_project_id": req.child_project_id,
    })

    return ExtensionPushResponse(
        ok=True,
        message=f"Đã broadcast đến {clients_before} client(s)",
        clients_notified=clients_before,
    )


# ── WebSocket Endpoint ─────────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """
    WebSocket endpoint để frontend lắng nghe real-time push từ extension.
    URL: ws://127.0.0.1:8765/api/extension/ws
    """
    await manager.connect(ws)
    try:
        while True:
            # Giữ connection alive — frontend chỉ cần lắng nghe, không cần gửi gì
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        logger.warning("[extension] WebSocket lỗi: %s", exc)
        manager.disconnect(ws)
