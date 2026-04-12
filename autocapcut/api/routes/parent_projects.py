"""CRUD endpoints for Parent Projects + Folder Templates + Documents."""
from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from autocapcut.database.connection import get_session
from autocapcut.database.models import FolderTemplate, ParentDoc, ParentDocFolder, ParentProject

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class FolderTemplateIn(BaseModel):
    folder_name: str
    sort_order: int = 0


class ParentProjectOut(BaseModel):
    """Explicit response model — avoids lazy-loading relationship fields."""
    id: int
    name: str
    author: str | None = None
    publisher: str | None = None
    copyright: str | None = None
    keywords_raw: str | None = None
    roxy_workspace_id: int | None = None
    roxy_profile_id: str | None = None
    roxy_profile_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ParentProjectCreate(BaseModel):
    name: str
    author: str | None = None
    publisher: str | None = None
    copyright: str | None = None
    keywords_raw: str | None = None
    roxy_workspace_id: int | None = None
    roxy_profile_id: str | None = None
    roxy_profile_name: str | None = None
    folder_templates: list[FolderTemplateIn] = []


class ParentProjectUpdate(BaseModel):
    name: str | None = None
    author: str | None = None
    publisher: str | None = None
    copyright: str | None = None
    keywords_raw: str | None = None
    roxy_workspace_id: int | None = None
    roxy_profile_id: str | None = None
    roxy_profile_name: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[ParentProjectOut])
async def list_parent_projects(session: SessionDep):
    result = await session.execute(select(ParentProject))
    projects = result.scalars().all()
    return projects


@router.get("/{project_id}", response_model=ParentProjectOut)
async def get_parent_project(project_id: int, session: SessionDep):
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/", status_code=201, response_model=ParentProjectOut)
async def create_parent_project(data: ParentProjectCreate, session: SessionDep):
    project = ParentProject(
        name=data.name,
        author=data.author,
        publisher=data.publisher,
        copyright=data.copyright,
        keywords_raw=data.keywords_raw,
        roxy_workspace_id=data.roxy_workspace_id,
        roxy_profile_id=data.roxy_profile_id,
        roxy_profile_name=data.roxy_profile_name,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    # Save folder templates if provided
    for idx, tpl in enumerate(data.folder_templates):
        folder_tpl = FolderTemplate(
            parent_project_id=project.id,
            folder_name=tpl.folder_name,
            sort_order=tpl.sort_order if tpl.sort_order else idx,
        )
        session.add(folder_tpl)

    await session.commit()
    await session.refresh(project)
    return project


@router.patch("/{project_id}", response_model=ParentProjectOut)
async def update_parent_project(project_id: int, data: ParentProjectUpdate, session: SessionDep):
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if data.name is not None:
        project.name = data.name
    if data.author is not None:
        project.author = data.author
    if data.publisher is not None:
        project.publisher = data.publisher
    if data.copyright is not None:
        project.copyright = data.copyright
    if data.keywords_raw is not None:
        project.keywords_raw = data.keywords_raw
    if data.roxy_workspace_id is not None:
        project.roxy_workspace_id = data.roxy_workspace_id
    if data.roxy_profile_id is not None:
        project.roxy_profile_id = data.roxy_profile_id
    if data.roxy_profile_name is not None:
        project.roxy_profile_name = data.roxy_profile_name

    project.updated_at = datetime.utcnow()
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_parent_project(project_id: int, session: SessionDep):
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await session.delete(project)
    await session.commit()


# ---------------------------------------------------------------------------
# Folder Template endpoints
# ---------------------------------------------------------------------------

@router.get("/{project_id}/folder-templates")
async def get_folder_templates(project_id: int, session: SessionDep):
    result = await session.execute(
        select(FolderTemplate)
        .where(FolderTemplate.parent_project_id == project_id)
        .order_by(FolderTemplate.sort_order)
    )
    templates = result.scalars().all()
    return templates


@router.post("/{project_id}/folder-templates", status_code=201)
async def set_folder_templates(
    project_id: int,
    templates: list[FolderTemplateIn],
    session: SessionDep,
):
    """Replace all folder templates for one parent project."""
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete existing templates
    result = await session.execute(
        select(FolderTemplate).where(FolderTemplate.parent_project_id == project_id)
    )
    old = result.scalars().all()
    for t in old:
        await session.delete(t)

    # Create new templates
    for idx, tpl in enumerate(templates):
        session.add(FolderTemplate(
            parent_project_id=project_id,
            folder_name=tpl.folder_name,
            sort_order=tpl.sort_order if tpl.sort_order else idx,
        ))

    await session.commit()
    return {"message": f"Saved {len(templates)} folder template(s)."}


# ---------------------------------------------------------------------------
# Helper — resolve docs storage root
# ---------------------------------------------------------------------------

def _docs_dir(parent_id: int, folder_id: int | None = None) -> Path:
    base = Path.home() / ".autocapcut" / "docs" / str(parent_id)
    if folder_id is not None:
        base = base / str(folder_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# Doc Folder endpoints
# ---------------------------------------------------------------------------

class DocFolderCreate(BaseModel):
    name: str


class DocFolderOut(BaseModel):
    id: int
    parent_project_id: int
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ParentDocOut(BaseModel):
    id: int
    parent_project_id: int
    folder_id: int | None = None
    file_name: str
    file_path: str
    file_size: int
    mime_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/{project_id}/doc-folders", response_model=list[DocFolderOut])
async def list_doc_folders(project_id: int, session: SessionDep):
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    result = await session.execute(
        select(ParentDocFolder)
        .where(ParentDocFolder.parent_project_id == project_id)
        .order_by(ParentDocFolder.created_at)
    )
    return result.scalars().all()


@router.post("/{project_id}/doc-folders", status_code=201, response_model=DocFolderOut)
async def create_doc_folder(project_id: int, data: DocFolderCreate, session: SessionDep):
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    folder = ParentDocFolder(parent_project_id=project_id, name=data.name.strip())
    session.add(folder)
    await session.commit()
    await session.refresh(folder)
    # Ensure directory exists on disk
    _docs_dir(project_id, folder.id)
    return folder


@router.delete("/{project_id}/doc-folders/{folder_id}", status_code=204)
async def delete_doc_folder(project_id: int, folder_id: int, session: SessionDep):
    folder = await session.get(ParentDocFolder, folder_id)
    if not folder or folder.parent_project_id != project_id:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Delete all docs inside first
    result = await session.execute(
        select(ParentDoc).where(ParentDoc.folder_id == folder_id)
    )
    for doc in result.scalars().all():
        try:
            Path(doc.file_path).unlink(missing_ok=True)
        except Exception:
            pass
        await session.delete(doc)

    await session.delete(folder)
    await session.commit()

    # Remove directory from disk
    folder_dir = Path.home() / ".autocapcut" / "docs" / str(project_id) / str(folder_id)
    shutil.rmtree(folder_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Doc endpoints
# ---------------------------------------------------------------------------

@router.get("/{project_id}/docs", response_model=list[ParentDocOut])
async def list_docs(project_id: int, session: SessionDep, folder_id: int | None = None):
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    query = select(ParentDoc).where(ParentDoc.parent_project_id == project_id)
    if folder_id is not None:
        query = query.where(ParentDoc.folder_id == folder_id)
    result = await session.execute(query.order_by(ParentDoc.created_at))
    return result.scalars().all()


@router.post("/{project_id}/docs/upload", status_code=201, response_model=ParentDocOut)
async def upload_doc(
    project_id: int,
    file: UploadFile,
    session: SessionDep,
    folder_id: int | None = None,
):
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    dest_dir = _docs_dir(project_id, folder_id)
    # Avoid name collisions by prefixing with timestamp
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    safe_name = f"{ts}_{file.filename}"
    dest_path = dest_dir / safe_name

    content = await file.read()
    dest_path.write_bytes(content)

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    doc = ParentDoc(
        parent_project_id=project_id,
        folder_id=folder_id,
        file_name=file.filename or safe_name,
        file_path=str(dest_path),
        file_size=len(content),
        mime_type=mime,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


@router.delete("/{project_id}/docs/{doc_id}", status_code=204)
async def delete_doc(project_id: int, doc_id: int, session: SessionDep):
    doc = await session.get(ParentDoc, doc_id)
    if not doc or doc.parent_project_id != project_id:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        Path(doc.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    await session.delete(doc)
    await session.commit()


class UploadByPathRequest(BaseModel):
    paths: list[str]
    folder_id: int | None = None


@router.post("/{project_id}/docs/upload-by-path", status_code=201, response_model=list[ParentDocOut])
async def upload_docs_by_path(
    project_id: int,
    data: UploadByPathRequest,
    session: SessionDep,
):
    """Accept local file paths (e.g. from Tauri drag-and-drop) and copy them into doc storage."""
    project = await session.get(ParentProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    dest_dir = _docs_dir(project_id, data.folder_id)
    results: list[ParentDoc] = []

    for src in data.paths:
        src_path = Path(src)
        if not src_path.is_file():
            continue  # Skip directories or non-existent paths

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        safe_name = f"{ts}_{src_path.name}"
        dest_path = dest_dir / safe_name

        shutil.copy2(str(src_path), str(dest_path))
        file_size = dest_path.stat().st_size
        mime = mimetypes.guess_type(src_path.name)[0] or "application/octet-stream"

        doc = ParentDoc(
            parent_project_id=project_id,
            folder_id=data.folder_id,
            file_name=src_path.name,
            file_path=str(dest_path),
            file_size=file_size,
            mime_type=mime,
        )
        session.add(doc)
        await session.flush()
        await session.refresh(doc)
        results.append(doc)

    await session.commit()
    return results


@router.get("/{project_id}/docs/{doc_id}/open")
async def open_doc(project_id: int, doc_id: int, session: SessionDep):
    doc = await session.get(ParentDoc, doc_id)
    if not doc or doc.parent_project_id != project_id:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(doc.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File no longer exists on disk")
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return {"message": "Opening file"}
