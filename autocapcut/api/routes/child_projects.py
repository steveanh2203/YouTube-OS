"""CRUD endpoints for Child Projects, folder scanning & creation on disk."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from autocapcut.database.connection import get_session
from autocapcut.database.models import (
    ChildFolder,
    ChildProject,
    FolderTemplate,
    ParentProject,
)

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ChildProjectCreate(BaseModel):
    video_number: int
    description: str = ""
    base_folder_path: str | None = None


class ChildProjectUpdate(BaseModel):
    video_number: int | None = None
    title: str | None = None
    description: str | None = None
    seeding_comments: str | None = None
    base_folder_path: str | None = None
    status: str | None = None
    # Resource-prep checkmarks
    prep_title_done: bool | None = None
    prep_desc_done: bool | None = None
    prep_seed_done: bool | None = None
    prep_folder_done: bool | None = None


class ScanFolderRequest(BaseModel):
    parent_folder_path: str   # folder cha cần scan trên máy


class CreateFoldersRequest(BaseModel):
    base_path: str
    folder_names: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/")
async def list_child_projects(parent_id: int, session: SessionDep):
    children = (await session.execute(
        select(ChildProject)
        .where(ChildProject.parent_project_id == parent_id)
        .order_by(ChildProject.video_number)
    )).scalars().all()
    # Thêm display name
    return [
        {**c.model_dump(), "display_name": f"Video {c.video_number}"}
        for c in children
    ]


@router.post("/create-folders", status_code=201)
async def create_folders_on_disk(data: CreateFoldersRequest):
    """Create subfolders on disk at the given base path."""
    loop = asyncio.get_event_loop()

    def _do_create():
        base = Path(data.base_path)
        if not base.exists():
            raise HTTPException(status_code=400, detail=f"Base path does not exist: {data.base_path}")
        created = []
        already_exists = []
        for name in data.folder_names:
            folder_path = base / name.strip()
            if folder_path.exists():
                already_exists.append(str(folder_path))
            else:
                folder_path.mkdir(parents=True, exist_ok=True)
                created.append(str(folder_path))
        return {"created": created, "already_exists": already_exists}

    return await loop.run_in_executor(None, _do_create)


@router.post("/scan")
async def scan_folder(parent_id: int, data: ScanFolderRequest, session: SessionDep):
    """
    Scan a parent folder on disk.
    - Find existing subfolders
    - Compare them with child projects in the database
    - Return the folders that are not mapped to any child project
    """
    parent = await session.get(ParentProject, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent project not found")

    scan_path = Path(data.parent_folder_path)
    if not scan_path.exists():
        raise HTTPException(status_code=400, detail="Path does not exist")

    # Lấy tất cả subfolder trên disk
    disk_folders = sorted([
        f.name for f in scan_path.iterdir() if f.is_dir()
    ])

    # Lấy các base_folder_path đã có trong DB cho parent này
    existing_children = (await session.execute(
        select(ChildProject).where(ChildProject.parent_project_id == parent_id)
    )).scalars().all()
    mapped_paths = {
        Path(c.base_folder_path).name
        for c in existing_children
        if c.base_folder_path
    }

    # Folder nào chưa được map
    unmapped = [f for f in disk_folders if f not in mapped_paths]

    return {
        "total_on_disk": len(disk_folders),
        "already_mapped": len(mapped_paths),
        "unmapped_folders": unmapped,
        "scan_path": str(scan_path),
    }


@router.get("/{child_id}")
async def get_child_project(child_id: int, session: SessionDep):
    child = await session.get(ChildProject, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found")
    return {**child.model_dump(), "display_name": f"Video {child.video_number}"}


@router.post("/", status_code=201)
async def create_child_project(
    parent_id: int,
    data: ChildProjectCreate,
    session: SessionDep,
):
    parent = await session.get(ParentProject, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent project not found")

    child = ChildProject(
        parent_project_id=parent_id,
        video_number=data.video_number,
        description=data.description,
        status="draft",
        base_folder_path=data.base_folder_path,
    )
    session.add(child)
    await session.commit()
    await session.refresh(child)

    # Tự động tạo folders từ template nếu có base_folder_path
    if data.base_folder_path:
        await _create_folders_from_template(child, session)
        await session.commit()

    return {**child.model_dump(), "display_name": f"Video {child.video_number}"}


@router.patch("/{child_id}")
async def update_child_project(child_id: int, data: ChildProjectUpdate, session: SessionDep):
    child = await session.get(ChildProject, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found")

    if data.video_number is not None:
        child.video_number = data.video_number
    if data.title is not None:
        child.title = data.title
    if data.description is not None:
        child.description = data.description
    if data.seeding_comments is not None:
        child.seeding_comments = data.seeding_comments
    if data.base_folder_path is not None:
        child.base_folder_path = data.base_folder_path
    if data.status is not None:
        child.status = data.status
    if data.prep_title_done is not None:
        child.prep_title_done = data.prep_title_done
    if data.prep_desc_done is not None:
        child.prep_desc_done = data.prep_desc_done
    if data.prep_seed_done is not None:
        child.prep_seed_done = data.prep_seed_done
    if data.prep_folder_done is not None:
        child.prep_folder_done = data.prep_folder_done

    # Auto-status: if all prep checkmarks are done and status is draft → resource_prep
    if (child.prep_title_done and child.prep_desc_done
            and child.prep_seed_done and child.prep_folder_done
            and child.status == "draft"):
        child.status = "resource_prep"

    child.updated_at = datetime.utcnow()
    session.add(child)
    await session.commit()
    await session.refresh(child)
    return {**child.model_dump(), "display_name": f"Video {child.video_number}"}


@router.delete("/{child_id}", status_code=204)
async def delete_child_project(child_id: int, session: SessionDep):
    child = await session.get(ChildProject, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found")
    await session.delete(child)
    await session.commit()


# ---------------------------------------------------------------------------
# Folder management
# ---------------------------------------------------------------------------

@router.get("/{child_id}/folders")
async def get_child_folders(child_id: int, session: SessionDep):
    folders = (await session.execute(
        select(ChildFolder).where(ChildFolder.child_project_id == child_id)
    )).scalars().all()
    return folders


@router.post("/{child_id}/folders/create-from-template", status_code=201)
async def create_folders_from_template(child_id: int, session: SessionDep):
    """Create folders on disk from the parent project's folder template."""
    child = await session.get(ChildProject, child_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found")
    if not child.base_folder_path:
        raise HTTPException(status_code=400, detail="Child project does not have a base_folder_path")

    created = await _create_folders_from_template(child, session)
    await session.commit()
    return {"created": created}



# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _create_folders_from_template(child: ChildProject, session: AsyncSession) -> list[str]:
    """Create subfolders from the parent's folder templates and persist them to the database."""
    templates = (await session.execute(
        select(FolderTemplate)
        .where(FolderTemplate.parent_project_id == child.parent_project_id)
        .order_by(FolderTemplate.sort_order)
    )).scalars().all()

    base = Path(child.base_folder_path)
    created: list[str] = []

    for tpl in templates:
        folder_path = base / tpl.folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        # Kiểm tra đã lưu trong DB chưa
        existing = (await session.execute(
            select(ChildFolder)
            .where(ChildFolder.child_project_id == child.id)
            .where(ChildFolder.folder_name == tpl.folder_name)
        )).scalars().first()

        if not existing:
            session.add(ChildFolder(
                child_project_id=child.id,
                folder_name=tpl.folder_name,
                actual_path=str(folder_path),
            ))
            created.append(str(folder_path))

    return created
