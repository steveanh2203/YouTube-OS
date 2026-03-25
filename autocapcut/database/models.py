"""SQLModel table definitions for the MasterOS local database."""

from datetime import date, datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


# ---------------------------------------------------------------------------
# Parent Project
# ---------------------------------------------------------------------------

class ParentProject(SQLModel, table=True):
    __tablename__ = "parent_projects"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    author: Optional[str] = Field(default=None)
    publisher: Optional[str] = Field(default=None)
    copyright: Optional[str] = Field(default=None)
    keywords_raw: Optional[str] = Field(default=None)   # "kw1, kw2, kw3"
    roxy_workspace_id: Optional[int] = Field(default=None)
    roxy_profile_id: Optional[str] = Field(default=None)
    roxy_profile_name: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    folder_templates: List["FolderTemplate"] = Relationship(back_populates="parent_project")
    child_projects: List["ChildProject"] = Relationship(back_populates="parent_project")
    doc_folders: List["ParentDocFolder"] = Relationship(back_populates="parent_project")
    docs: List["ParentDoc"] = Relationship(back_populates="parent_project")


# ---------------------------------------------------------------------------
# Folder Template  (per parent project, defined once)
# ---------------------------------------------------------------------------

class FolderTemplate(SQLModel, table=True):
    __tablename__ = "folder_templates"

    id: Optional[int] = Field(default=None, primary_key=True)
    parent_project_id: int = Field(foreign_key="parent_projects.id", nullable=False)
    folder_name: str = Field(nullable=False)
    sort_order: int = Field(default=0)

    parent_project: Optional[ParentProject] = Relationship(back_populates="folder_templates")


# ---------------------------------------------------------------------------
# Child Project
# ---------------------------------------------------------------------------

class ChildProject(SQLModel, table=True):
    __tablename__ = "child_projects"

    id: Optional[int] = Field(default=None, primary_key=True)
    parent_project_id: int = Field(foreign_key="parent_projects.id", nullable=False)
    video_number: int = Field(nullable=False)       # hiển thị "Video {video_number}"
    status: str = Field(default="draft")            # draft | resource_prep | editing | published
    title: Optional[str] = Field(default=None)      # YouTube video title
    description: str = Field(nullable=False)
    seeding_comments: Optional[str] = Field(default=None)  # newline-separated seed comments
    base_folder_path: Optional[str] = Field(default=None)  # đường dẫn trên máy Mac
    planning_stage: str = Field(default="backlog")         # backlog | ready | editing | published
    priority: str = Field(default="medium")                # low | medium | high | urgent
    deadline: Optional[date] = Field(default=None)
    planning_note: str = Field(default="")
    roxy_workspace_id: Optional[int] = Field(default=None)
    roxy_profile_id: Optional[str] = Field(default=None)
    roxy_profile_name: Optional[str] = Field(default=None)
    # Resource-prep checkmarks
    prep_title_done: bool = Field(default=False)
    prep_desc_done: bool = Field(default=False)
    prep_seed_done: bool = Field(default=False)
    prep_folder_done: bool = Field(default=False)
    # Sora Gen
    sora_prompt: Optional[str] = Field(default=None)
    sora_status: Optional[str] = Field(default=None)   # pending | generating | done | failed
    sora_progress: int = Field(default=0)               # 0-100
    sora_video_path: Optional[str] = Field(default=None)  # local path sau khi download
    sora_generation_id: Optional[str] = Field(default=None)
    sora_permalink: Optional[str] = Field(default=None)
    sora_ratio: Optional[str] = Field(default="16:9")   # "16:9" | "9:16" | "1:1"
    sora_duration: Optional[int] = Field(default=5)     # 5 | 10 | 20 (seconds)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    parent_project: Optional[ParentProject] = Relationship(back_populates="child_projects")
    child_folders: List["ChildFolder"] = Relationship(back_populates="child_project")
    captions: List["Caption"] = Relationship(back_populates="child_project")
    seo_metadata: List["SeoMetadata"] = Relationship(back_populates="child_project")
    render_history: List["RenderHistory"] = Relationship(back_populates="child_project")
    thumbnails: List["Thumbnail"] = Relationship(back_populates="child_project")
    competitor_videos: List["CompetitorVideo"] = Relationship(back_populates="child_project")


# ---------------------------------------------------------------------------
# Child Folders  (actual folders created on disk)
# ---------------------------------------------------------------------------

class ChildFolder(SQLModel, table=True):
    __tablename__ = "child_folders"

    id: Optional[int] = Field(default=None, primary_key=True)
    child_project_id: int = Field(foreign_key="child_projects.id", nullable=False)
    folder_name: str = Field(nullable=False)
    actual_path: str = Field(nullable=False)        # full path on Mac
    created_at: datetime = Field(default_factory=datetime.utcnow)

    child_project: Optional[ChildProject] = Relationship(back_populates="child_folders")


# ---------------------------------------------------------------------------
# Captions / SRT
# ---------------------------------------------------------------------------

class Caption(SQLModel, table=True):
    __tablename__ = "captions"

    id: Optional[int] = Field(default=None, primary_key=True)
    child_project_id: int = Field(foreign_key="child_projects.id", nullable=False)
    content: str = Field(nullable=False)            # nội dung SRT
    language: str = Field(default="vi")
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    child_project: Optional[ChildProject] = Relationship(back_populates="captions")


# ---------------------------------------------------------------------------
# SEO Metadata
# ---------------------------------------------------------------------------

class SeoMetadata(SQLModel, table=True):
    __tablename__ = "seo_metadata"

    id: Optional[int] = Field(default=None, primary_key=True)
    child_project_id: int = Field(foreign_key="child_projects.id", nullable=False)
    title: str = Field(nullable=False)
    description: str = Field(nullable=False)
    tags: str = Field(default="")                   # "tag1, tag2, tag3"
    status: str = Field(default="pending")          # pending / applied / failed
    applied_at: Optional[datetime] = Field(default=None)

    child_project: Optional[ChildProject] = Relationship(back_populates="seo_metadata")


# ---------------------------------------------------------------------------
# Render History
# ---------------------------------------------------------------------------

class RenderHistory(SQLModel, table=True):
    __tablename__ = "render_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    child_project_id: int = Field(foreign_key="child_projects.id", nullable=False)
    status: str = Field(default="pending")          # pending / running / done / failed
    output_path: Optional[str] = Field(default=None)
    error_log: Optional[str] = Field(default=None)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)

    child_project: Optional[ChildProject] = Relationship(back_populates="render_history")


# ---------------------------------------------------------------------------
# Thumbnail
# ---------------------------------------------------------------------------

class Thumbnail(SQLModel, table=True):
    __tablename__ = "thumbnails"

    id: Optional[int] = Field(default=None, primary_key=True)
    child_project_id: int = Field(foreign_key="child_projects.id", nullable=False)
    local_path: Optional[str] = Field(default=None)
    r2_url: Optional[str] = Field(default=None)    # NULL cho đến khi migrate sang R2
    created_at: datetime = Field(default_factory=datetime.utcnow)

    child_project: Optional[ChildProject] = Relationship(back_populates="thumbnails")


# ---------------------------------------------------------------------------
# Competitor Video
# ---------------------------------------------------------------------------

class CompetitorVideo(SQLModel, table=True):
    __tablename__ = "competitor_videos"

    id: Optional[int] = Field(default=None, primary_key=True)
    child_project_id: Optional[int] = Field(default=None, foreign_key="child_projects.id")
    parent_project_id: Optional[int] = Field(default=None, foreign_key="parent_projects.id")
    url: str = Field(nullable=False)
    normalized_url: str = Field(nullable=False, index=True)
    video_id: Optional[str] = Field(default=None, index=True)
    title: str = Field(nullable=False)
    channel: str = Field(default="")
    thumbnail_url: str = Field(default="")
    notes: str = Field(default="")
    purpose: str = Field(default="reference")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    child_project: Optional[ChildProject] = Relationship(back_populates="competitor_videos")


# ---------------------------------------------------------------------------
# Parent Doc Folder  (named folder inside a parent project's documents area)
# ---------------------------------------------------------------------------

class ParentDocFolder(SQLModel, table=True):
    __tablename__ = "parent_doc_folders"

    id: Optional[int] = Field(default=None, primary_key=True)
    parent_project_id: int = Field(foreign_key="parent_projects.id", nullable=False)
    name: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    parent_project: Optional[ParentProject] = Relationship(back_populates="doc_folders")
    docs: List["ParentDoc"] = Relationship(back_populates="folder")


# ---------------------------------------------------------------------------
# Parent Doc  (a file attached to a parent project, optionally inside a folder)
# ---------------------------------------------------------------------------

class ParentDoc(SQLModel, table=True):
    __tablename__ = "parent_docs"

    id: Optional[int] = Field(default=None, primary_key=True)
    parent_project_id: int = Field(foreign_key="parent_projects.id", nullable=False)
    folder_id: Optional[int] = Field(default=None, foreign_key="parent_doc_folders.id")
    file_name: str = Field(nullable=False)          # original filename
    file_path: str = Field(nullable=False)          # absolute path on disk
    file_size: int = Field(default=0)               # bytes
    mime_type: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    parent_project: Optional[ParentProject] = Relationship(back_populates="docs")
    folder: Optional[ParentDocFolder] = Relationship(back_populates="docs")
