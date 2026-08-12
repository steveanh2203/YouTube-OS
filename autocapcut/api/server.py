"""Single-user self-hosted FastAPI server for the MasterOS web app."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from autocapcut.api.routes import srt, seo, roxy, competitors, audio
from autocapcut.api.routes import parent_projects, child_projects
from autocapcut.api.routes import ai_gen
from autocapcut.api.routes import livestream
from autocapcut.api.routes import fast_edit
from autocapcut.api.routes import cut_automate
from autocapcut.api.routes import extension
from autocapcut.api.routes import sora
from autocapcut.api.routes import youtube_reply
from autocapcut.api.routes import account_connect
from autocapcut.api.routes import community
from autocapcut.api.routes import media
from autocapcut.api.routes import niche_research
from autocapcut.database.connection import init_db

_log = logging.getLogger("autocapcut.api")

app = FastAPI(title="MasterOS API", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    # Pre-warm Real-ESRGAN binary cache in background thread so first upscale is instant
    threading.Thread(target=_prewarm_upscale, daemon=True).start()


def _prewarm_upscale() -> None:
    """Cache Real-ESRGAN binary path at startup so upscale calls skip filesystem scan."""
    try:
        from autocapcut.services.upscale_engine import pre_warm_realesrgan
        pre_warm_realesrgan(log_callback=lambda msg: _log.info("[upscale] %s", msg))
    except Exception as exc:
        _log.warning("Real-ESRGAN pre-warm failed (upscale will still work on first call): %s", exc)

# Production is same-origin; only the local Vite development server needs CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:1420", "http://localhost:1420"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(srt.router,             prefix="/api/srt",              tags=["srt"])
app.include_router(seo.router,             prefix="/api/seo",              tags=["seo"])
app.include_router(competitors.router,     prefix="/api/competitors",      tags=["competitors"])
app.include_router(audio.router,           prefix="/api/audio",            tags=["audio"])
app.include_router(roxy.router,            prefix="/api/roxy",             tags=["roxy"])
app.include_router(parent_projects.router, prefix="/api/parent-projects",  tags=["parent-projects"])
app.include_router(child_projects.router,  prefix="/api/child-projects",   tags=["child-projects"])
app.include_router(ai_gen.router,          prefix="/api/ai-gen",           tags=["ai-gen"])
app.include_router(livestream.router,      prefix="/api/livestream",        tags=["livestream"])
app.include_router(fast_edit.router,       prefix="/api/fast-edit",         tags=["fast-edit"])
app.include_router(cut_automate.router,    prefix="/api/cut-automate",      tags=["cut-automate"])
app.include_router(extension.router,       prefix="/api/extension",          tags=["extension"])
app.include_router(sora.router,            prefix="/api/sora",               tags=["sora"])
app.include_router(youtube_reply.router,   prefix="/api/youtube-reply",      tags=["youtube-reply"])
app.include_router(account_connect.router, prefix="/api/account-connect",    tags=["account-connect"])
app.include_router(community.router,       prefix="/api/community",          tags=["community"])
app.include_router(media.router,           prefix="/api/media",              tags=["media"])
app.include_router(niche_research.router,  prefix="/api/niche-research",     tags=["niche-research"])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "masteros-api"}


def mount_spa(target_app: FastAPI, dist_dir: Path) -> bool:
    """Mount a built Vite app and preserve JSON 404s below ``/api``."""
    dist = dist_dir.resolve()
    index = dist / "index.html"
    if not index.is_file():
        return False

    assets = dist / "assets"
    if assets.is_dir():
        target_app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @target_app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found.")

        requested = (dist / full_path).resolve()
        if requested.is_relative_to(dist) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(index)

    return True


mount_spa(app, Path(__file__).resolve().parents[2] / "frontend" / "dist")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    try:
        from granian import Granian
        from granian.constants import Interfaces

        Granian(
            "autocapcut.api.server:app",
            address=args.host,
            port=args.port,
            interface=Interfaces.ASGI,
            log_level="info",
            workers=1,
        ).serve()
    except ImportError:
        import uvicorn  # fallback if granian not installed
        uvicorn.run(
            "autocapcut.api.server:app",
            host=args.host,
            port=args.port,
            log_level="info",
            reload=False,
        )


if __name__ == "__main__":
    main()
