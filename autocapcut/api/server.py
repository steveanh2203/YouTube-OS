"""FastAPI sidecar server — bridge between Tauri UI and Python services."""
from __future__ import annotations

import argparse
import logging
import sys
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from autocapcut.api.routes import projects, sync, srt, seo, animation, roxy, competitors, audio
from autocapcut.api.routes import parent_projects, child_projects
from autocapcut.api.routes import ai_gen
from autocapcut.api.routes import livestream
from autocapcut.api.routes import fast_edit
from autocapcut.api.routes import cut_automate
from autocapcut.api.routes import extension
from autocapcut.api.routes import sora
from autocapcut.api.routes import audio_visualizer
from autocapcut.api.routes import youtube_reply
from autocapcut.api.routes import account_connect
from autocapcut.api.routes import community
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

# Allow Tauri WebView (null origin) + Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router,         prefix="/api/projects",         tags=["projects"])
app.include_router(sync.router,             prefix="/api/sync",             tags=["sync"])
app.include_router(srt.router,             prefix="/api/srt",              tags=["srt"])
app.include_router(seo.router,             prefix="/api/seo",              tags=["seo"])
app.include_router(animation.router,       prefix="/api/animation",        tags=["animation"])
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
app.include_router(audio_visualizer.router, prefix="/api/audio-visualizer",  tags=["audio-visualizer"])
app.include_router(youtube_reply.router,   prefix="/api/youtube-reply",      tags=["youtube-reply"])
app.include_router(account_connect.router, prefix="/api/account-connect",    tags=["account-connect"])
app.include_router(community.router,       prefix="/api/community",          tags=["community"])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "masteros-api"}


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
