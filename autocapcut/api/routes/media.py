"""Browser-safe managed media upload, library, preview, and download routes."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from autocapcut.database.connection import get_session
from autocapcut.database.models import MediaAsset
from autocapcut.services.media_workspace import (
    MediaTooLarge,
    MediaWorkspaceError,
    resolve_managed_path,
    store_upload,
)


router = APIRouter()


class MediaAssetOut(BaseModel):
    id: str
    reference: str
    original_name: str
    kind: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: str
    content_url: str
    download_url: str


def _asset_out(asset: MediaAsset) -> MediaAssetOut:
    return MediaAssetOut(
        id=asset.id,
        reference=f"media:{asset.id}",
        original_name=asset.original_name,
        kind=asset.kind,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        created_at=asset.created_at.isoformat(),
        content_url=f"/api/media/assets/{asset.id}/content",
        download_url=f"/api/media/assets/{asset.id}/download",
    )


async def _get_asset(asset_id: str, session: AsyncSession) -> MediaAsset:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found.")
    return asset


async def resolve_media_reference(reference: str, session: AsyncSession) -> Path:
    """Resolve an opaque ``media:<id>`` reference to a verified workspace file."""
    prefix = "media:"
    if not reference.startswith(prefix):
        raise HTTPException(status_code=422, detail="A managed media reference is required.")
    asset_id = reference[len(prefix):].strip()
    if not asset_id:
        raise HTTPException(status_code=422, detail="Invalid media reference.")
    asset = await _get_asset(asset_id, session)
    path = resolve_managed_path(asset.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file is missing from the workspace.")
    return path


@router.post("/assets", response_model=MediaAssetOut, status_code=201)
async def upload_asset(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> MediaAssetOut:
    try:
        stored = await asyncio.to_thread(
            store_upload,
            file.filename or "",
            file.file,
            file.content_type,
        )
    except MediaTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MediaWorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()

    asset = MediaAsset(
        id=stored.asset_id,
        original_name=stored.original_name,
        stored_path=stored.path,
        kind=stored.kind,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )
    try:
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
    except Exception:
        path = resolve_managed_path(stored.path)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        raise
    return _asset_out(asset)


@router.get("/assets", response_model=list[MediaAssetOut])
async def list_assets(
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[MediaAssetOut]:
    statement = select(MediaAsset).order_by(MediaAsset.created_at.desc())
    if kind:
        statement = statement.where(MediaAsset.kind == kind)
    result = await session.execute(statement)
    return [_asset_out(asset) for asset in result.scalars().all()]


@router.get("/assets/{asset_id}/content")
async def stream_asset(asset_id: str, session: AsyncSession = Depends(get_session)) -> FileResponse:
    asset = await _get_asset(asset_id, session)
    path = resolve_managed_path(asset.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file is missing from the workspace.")
    return FileResponse(path, media_type=asset.content_type, headers={"Cache-Control": "no-store"})


@router.get("/assets/{asset_id}/download")
async def download_asset(asset_id: str, session: AsyncSession = Depends(get_session)) -> FileResponse:
    asset = await _get_asset(asset_id, session)
    path = resolve_managed_path(asset.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file is missing from the workspace.")
    return FileResponse(path, media_type=asset.content_type, filename=asset.original_name)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    asset = await _get_asset(asset_id, session)
    path = resolve_managed_path(asset.stored_path)
    await session.delete(asset)
    await session.commit()
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return Response(status_code=204)
