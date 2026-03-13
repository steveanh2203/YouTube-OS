"""Flet desktop UI for AutoCapCut."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import plistlib
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import uuid4

import flet as ft
from loguru import logger

from autocapcut.models import ProjectItem, ProjectStatus
from autocapcut.services.project_loader import discover_projects
from autocapcut.services.sync_audio import SyncAudioError, sync_project_audio
from autocapcut.services.sync_captions import SyncCaptionError, sync_project_captions
from autocapcut.services.sync_images import SyncImageError, sync_project_images
from autocapcut.ui.flet_components import (
    app_card,
    app_dropdown,
    app_input,
    app_text,
    app_textarea,
    danger_button,
    ghost_button,
    primary_button,
    secondary_button,
    section_header,
    sidebar_item,
    status_badge,
    topbar,
)
from autocapcut.ui.flet_theme import TOKENS, configure_page, page_padding, text_style

try:
    from AppKit import NSScreen
except ImportError:  # pragma: no cover - non-macOS fallback
    NSScreen = None


QT_SETTINGS_DOMAIN = "com.AutoCapCut.AutoCapCut"
STATE_FILE = Path.home() / ".autocapcut_flet_state.json"

WORKSPACE_PROJECTS = "projects"
WORKSPACE_RENDER = "render"
WORKSPACE_ANALYTICS = "analytics"

CHILD_VIEW_LIST = "child_list"
CHILD_VIEW_TOOLS = "child_tools"

CHILD_TOOL_AI_GEN = "ai_gen"
CHILD_TOOL_AI_AUDIO = "ai_audio"
CHILD_TOOL_LIVESTREAM = "livestream"


@dataclass
class ParentProjectPreset:
    id: str
    name: str
    keywords_raw: str = ""
    author: str = ""
    publisher: str = ""
    copyright: str = ""


@dataclass
class ChildProjectRecord:
    id: str
    name: str
    status: str = "Draft"
    title: str = ""
    description: str = ""
    seeding_comments_raw: str = ""
    folder_path: str = ""
    ai_audio_notes: str = ""
    livestream_notes: str = ""

    @property
    def parsed_comments(self) -> list[str]:
        return [line.strip() for line in self.seeding_comments_raw.splitlines() if line.strip()]

    @property
    def number(self) -> int:
        try:
            return int(self.name.replace("Project", "").strip())
        except ValueError:
            return 0


@dataclass
class PersistedState:
    presets: list[ParentProjectPreset] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)
    manual_child_projects: dict[str, list[ChildProjectRecord]] = field(default_factory=dict)


def _macos_visible_frame() -> tuple[int, int, int, int] | None:
    if sys.platform != "darwin" or NSScreen is None:
        return None

    screen = NSScreen.mainScreen()
    if screen is None:
        return None

    frame = screen.frame()
    visible = screen.visibleFrame()
    top_inset = int(round((frame.origin.y + frame.size.height) - (visible.origin.y + visible.size.height)))
    return (
        int(round(visible.origin.x)),
        top_inset,
        int(round(visible.size.width)),
        int(round(visible.size.height)),
    )


def _fit_page_to_workarea(page: ft.Page) -> None:
    bounds = _macos_visible_frame()
    if bounds is None:
        page.window.width = max(page.window.width or 1280, 1280)
        page.window.height = max(page.window.height or 820, 820)
        return

    left, top, width, height = bounds
    inset = 8
    page.window.left = left + inset
    page.window.top = top + inset
    page.window.width = max(width - (inset * 2), 1200)
    page.window.height = max(height - (inset * 2), 760)


def _parse_json_setting(payload: Any, default: Any) -> Any:
    if payload in (None, ""):
        return default
    try:
        return json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return default


def _load_qt_settings_snapshot() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["defaults", "export", QT_SETTINGS_DOMAIN, "-"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {}

    try:
        return plistlib.loads(result.stdout)
    except Exception:
        logger.exception("Failed to parse Qt settings plist export")
        return {}


def _migrate_qt_state() -> PersistedState:
    snapshot = _load_qt_settings_snapshot()
    state = PersistedState()

    preset_payload = _parse_json_setting(snapshot.get("project_dashboard.presets"), [])
    for item in preset_payload:
        if not isinstance(item, dict):
            continue
        preset_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        if not preset_id or not name:
            continue
        state.presets.append(
            ParentProjectPreset(
                id=preset_id,
                name=name,
                keywords_raw=str(item.get("keywords_raw", "")),
                author=str(item.get("author", "")),
                publisher=str(item.get("publisher", "")),
                copyright=str(item.get("copyright", "")),
            )
        )

    assignment_payload = _parse_json_setting(snapshot.get("project_dashboard.assignments"), {})
    if isinstance(assignment_payload, dict):
        state.assignments = {
            str(path): str(parent_id)
            for path, parent_id in assignment_payload.items()
            if str(path).strip() and str(parent_id).strip()
        }

    child_payload = _parse_json_setting(snapshot.get("project_dashboard.manual_child_projects"), {})
    if isinstance(child_payload, dict):
        for parent_id, items in child_payload.items():
            normalized: list[ChildProjectRecord] = []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                child_id = str(item.get("id", "")).strip() or uuid4().hex[:12]
                name = str(item.get("name", "")).strip() or f"Project {len(normalized) + 1}"
                normalized.append(
                    ChildProjectRecord(
                        id=child_id,
                        name=name,
                        status=str(item.get("status", "Draft")).capitalize() or "Draft",
                        title=str(item.get("title", "")),
                        description=str(item.get("description", "")),
                        seeding_comments_raw=str(item.get("seeding_comments_raw", "")),
                        folder_path=str(item.get("folder_path", "")),
                    )
                )
            state.manual_child_projects[str(parent_id)] = normalized

    return state


def load_persisted_state() -> PersistedState:
    if STATE_FILE.exists():
        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.exception("Invalid Flet state file, falling back to migration")
        else:
            presets = [
                ParentProjectPreset(**item)
                for item in payload.get("presets", [])
                if isinstance(item, dict)
            ]
            child_projects: dict[str, list[ChildProjectRecord]] = {}
            raw_children = payload.get("manual_child_projects", {})
            if isinstance(raw_children, dict):
                for parent_id, items in raw_children.items():
                    child_projects[str(parent_id)] = [
                        ChildProjectRecord(**item)
                        for item in items
                        if isinstance(item, dict)
                    ]
            assignments = {
                str(path): str(parent_id)
                for path, parent_id in payload.get("assignments", {}).items()
            }
            return PersistedState(
                presets=presets,
                assignments=assignments,
                manual_child_projects=child_projects,
            )

    state = _migrate_qt_state()
    save_persisted_state(state)
    return state


def save_persisted_state(state: PersistedState) -> None:
    payload = {
        "presets": [asdict(item) for item in state.presets],
        "assignments": state.assignments,
        "manual_child_projects": {
            parent_id: [asdict(item) for item in items]
            for parent_id, items in state.manual_child_projects.items()
        },
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AutoCapCutFletApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.state = load_persisted_state()
        self.projects: list[ProjectItem] = []
        self.current_workspace = WORKSPACE_PROJECTS
        self.child_view_mode = ""
        self.active_parent_id = self.state.presets[0].id if self.state.presets else ""
        self.active_child_id = ""
        self.active_child_tool = CHILD_TOOL_AI_GEN
        self.child_bulk_mode = False
        self.bulk_child_selection: set[str] = set()
        self.project_selection_counter = 0

    # ---------- bootstrap ----------
    def start(self) -> None:
        self.page.title = "AutoCapCut"
        configure_page(self.page)
        self.page.padding = 0
        self.page.spacing = 0
        self.page.window.min_width = 1200
        self.page.window.min_height = 760
        self.page.window.title_bar_hidden = False
        self.page.window.maximizable = True
        self.page.window.resizable = True
        self.page.window.prevent_close = False
        _fit_page_to_workarea(self.page)
        self.refresh_projects(initial=True)
        self.render()

    # ---------- state helpers ----------
    def persist(self) -> None:
        save_persisted_state(self.state)

    def notify(self, message: str, *, error: bool = False) -> None:
        self.page.snack_bar = ft.SnackBar(
            app_text(message, "small", color=TOKENS.text_inverse),
            bgcolor=TOKENS.danger if error else TOKENS.text_primary,
            open=True,
        )
        self.page.update()

    def refresh_projects(self, initial: bool = False) -> None:
        self.projects = discover_projects()
        for project in self.projects:
            assigned = self.state.assignments.get(project.path, "").strip()
            if assigned:
                project.assigned_project_id = assigned
        if self.projects and initial:
            self.project_selection_counter = 0

    def sorted_selected_projects(self) -> list[ProjectItem]:
        return sorted(
            [item for item in self.projects if item.is_selected],
            key=lambda item: item.selection_order or 0,
        )

    def parent_presets(self) -> list[ParentProjectPreset]:
        return sorted(self.state.presets, key=lambda item: item.name.lower())

    def selected_parent(self) -> ParentProjectPreset | None:
        return next((item for item in self.state.presets if item.id == self.active_parent_id), None)

    def child_projects_for_active_parent(self) -> list[ChildProjectRecord]:
        if not self.active_parent_id:
            return []
        return self.state.manual_child_projects.setdefault(self.active_parent_id, [])

    def selected_child(self) -> ChildProjectRecord | None:
        return next((item for item in self.child_projects_for_active_parent() if item.id == self.active_child_id), None)

    # ---------- mutations ----------
    def set_workspace(self, workspace: str) -> None:
        self.current_workspace = workspace
        if workspace != WORKSPACE_PROJECTS:
            self.child_view_mode = CHILD_VIEW_LIST
        self.render()

    def set_project_selected(self, project: ProjectItem, selected: bool) -> None:
        project.is_selected = selected
        if selected:
            self.project_selection_counter += 1
            project.selection_order = self.project_selection_counter
        else:
            project.selection_order = None
        self.render()

    def assign_project(self, project: ProjectItem, parent_id: str) -> None:
        project.assigned_project_id = parent_id
        if parent_id:
            self.state.assignments[project.path] = parent_id
        else:
            self.state.assignments.pop(project.path, None)
        self.persist()
        self.render()

    def _next_project_number(self) -> int:
        numbers = [child.number for child in self.child_projects_for_active_parent()]
        current = 1
        while current in numbers:
            current += 1
        return current

    def create_parent_project(self, name: str) -> None:
        cleaned = name.strip()
        if not cleaned:
            self.notify("Tên project cha không được để trống.", error=True)
            return
        parent = ParentProjectPreset(id=uuid4().hex[:12], name=cleaned)
        self.state.presets.append(parent)
        self.state.manual_child_projects[parent.id] = []
        self.active_parent_id = parent.id
        self.persist()
        self.render()

    def update_parent_project(self, parent_id: str, name: str) -> None:
        cleaned = name.strip()
        if not cleaned:
            self.notify("Tên project cha không được để trống.", error=True)
            return
        for item in self.state.presets:
            if item.id == parent_id:
                item.name = cleaned
                break
        self.persist()
        self.render()

    def delete_parent_project(self, parent_id: str) -> None:
        self.state.presets = [item for item in self.state.presets if item.id != parent_id]
        self.state.manual_child_projects.pop(parent_id, None)
        self.state.assignments = {
            path: assigned
            for path, assigned in self.state.assignments.items()
            if assigned != parent_id
        }
        self.active_parent_id = self.state.presets[0].id if self.state.presets else ""
        self.active_child_id = ""
        self.persist()
        self.render()

    def create_child_project(self) -> None:
        parent = self.selected_parent()
        if parent is None:
            self.notify("Chọn project cha trước đã.", error=True)
            return
        number = self._next_project_number()
        child = ChildProjectRecord(
            id=uuid4().hex[:12],
            name=f"Project {number}",
        )
        items = self.child_projects_for_active_parent()
        items.append(child)
        self.active_child_id = child.id
        self.persist()
        self.render()

    def delete_selected_children(self) -> None:
        items = self.child_projects_for_active_parent()
        if not items:
            return
        selected_ids = set(self.bulk_child_selection) if self.child_bulk_mode else ({self.active_child_id} if self.active_child_id else set())
        if not selected_ids:
            self.notify("Chọn project con để xoá.", error=True)
            return
        self.state.manual_child_projects[self.active_parent_id] = [item for item in items if item.id not in selected_ids]
        self.bulk_child_selection.clear()
        self.active_child_id = ""
        self.persist()
        self.render()

    def open_child_projects(self) -> None:
        parent = self.selected_parent()
        if parent is None:
            self.notify("Chọn project cha trước đã.", error=True)
            return
        children = self.child_projects_for_active_parent()
        self.child_view_mode = CHILD_VIEW_LIST
        self.active_child_id = children[0].id if children else ""
        self.current_workspace = WORKSPACE_PROJECTS
        self.render()

    def open_child_tools(self, child_id: str) -> None:
        self.active_child_id = child_id
        self.child_view_mode = CHILD_VIEW_TOOLS
        self.active_child_tool = CHILD_TOOL_AI_GEN
        self.render()

    def update_child_details(
        self,
        child_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        seeding_comments_raw: str | None = None,
        ai_audio_notes: str | None = None,
        livestream_notes: str | None = None,
    ) -> None:
        child = next((item for item in self.child_projects_for_active_parent() if item.id == child_id), None)
        if child is None:
            return
        if title is not None:
            child.title = title
        if description is not None:
            child.description = description
        if seeding_comments_raw is not None:
            child.seeding_comments_raw = seeding_comments_raw
        if ai_audio_notes is not None:
            child.ai_audio_notes = ai_audio_notes
        if livestream_notes is not None:
            child.livestream_notes = livestream_notes
        self.persist()

    # ---------- dialogs ----------
    def open_parent_dialog(self, *, existing: ParentProjectPreset | None = None) -> None:
        title_field = app_input(
            label="Project name",
            value=existing.name if existing else "",
            autofocus=True,
            on_change=lambda e: None,
            hint_text="Enter parent project name",
        )

        def submit(_: ft.ControlEvent) -> None:
            value = getattr(title_field, "value", "")
            if existing is None:
                self.create_parent_project(value)
            else:
                self.update_parent_project(existing.id, value)
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=app_text("Edit parent project" if existing else "Create parent project", "title"),
            content=ft.Container(width=420, content=title_field),
            actions=[
                self._ghost_button("Cancel", lambda e: self._close_dialog(dialog)),
                self._filled_button("Save", submit),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def open_confirm_delete_parent_dialog(self, parent: ParentProjectPreset) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title=app_text("Delete parent project", "title"),
            content=app_text(f"Xoá '{parent.name}' và toàn bộ project con của nó?", "body", color=TOKENS.text_secondary),
            actions=[
                self._ghost_button("Cancel", lambda e: self._close_dialog(dialog)),
                self._danger_button(
                    "Delete",
                    on_click=lambda e: self._confirm_delete_parent(dialog, parent.id),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _confirm_delete_parent(self, dialog: ft.AlertDialog, parent_id: str) -> None:
        dialog.open = False
        self.page.update()
        self.delete_parent_project(parent_id)

    def _close_dialog(self, dialog: ft.AlertDialog) -> None:
        dialog.open = False
        self.page.update()

    # ---------- actions ----------
    def run_sync(self, runner: Any, action_label: str) -> None:
        selected = self.sorted_selected_projects()
        if not selected:
            self.notify("Chọn ít nhất một project CapCut trước đã.", error=True)
            return

        for project in selected:
            project.status = ProjectStatus.processing
        self.render()

        failures: list[str] = []
        for project in selected:
            try:
                summary = runner(project)
                project.status = ProjectStatus.done
                if hasattr(summary, "updated_segments"):
                    project.notes = f"{action_label}: {summary.updated_segments} segment(s)"
                elif hasattr(summary, "paired"):
                    project.notes = f"{action_label}: {summary.paired} matched"
                else:
                    project.notes = action_label
            except (SyncAudioError, SyncImageError, SyncCaptionError) as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                failures.append(f"{project.name}: {exc}")
            self.render()

        if failures:
            self.notify(f"{action_label} hoàn tất, có lỗi ở {len(failures)} project.", error=True)
        else:
            self.notify(f"{action_label} hoàn tất cho {len(selected)} project.")

    def auto_render_placeholder(self) -> None:
        self.notify("Auto Render sẽ được nối tiếp ở pha Flet tiếp theo.", error=False)

    def automate_placeholder(self) -> None:
        self.notify("Automate sẽ được nối tiếp ở pha Flet tiếp theo.", error=False)

    # ---------- render ----------
    def render(self) -> None:
        self.page.clean()
        self.page.add(
            ft.Container(
                expand=True,
                bgcolor=TOKENS.canvas,
                padding=page_padding(),
                content=ft.Row(
                    [
                        self._build_sidebar(),
                        ft.Container(width=TOKENS.gap_6),
                        ft.Container(
                            expand=True,
                            content=ft.Column(
                                [
                                    topbar("AutoCapCut", self._workspace_label()),
                                    ft.Container(height=TOKENS.gap_5),
                                    ft.Container(expand=True, content=self._build_workspace_body()),
                                ],
                                expand=True,
                                spacing=0,
                            ),
                        ),
                    ],
                    expand=True,
                    spacing=0,
                ),
            )
        )
        self.page.update()

    def _build_sidebar(self) -> ft.Control:
        destinations = [
            ("Projects", WORKSPACE_PROJECTS),
            ("Render Dashboard", WORKSPACE_RENDER),
            ("Analytics", WORKSPACE_ANALYTICS),
        ]
        return ft.Container(
            width=TOKENS.sidebar_width,
            content=app_card(
                ft.Column(
                    [
                        ft.Container(height=4),
                        app_text("Desktop Control", "utility", color=TOKENS.primary),
                        app_text("AutoCapCut", "section_title"),
                        app_text("Operational workspace for project control.", "body", color=TOKENS.text_secondary),
                        ft.Container(height=TOKENS.gap_4),
                        app_text("Core Workspaces", "utility", color=TOKENS.text_muted),
                        *[
                            sidebar_item(
                                label,
                                active=self.current_workspace == key,
                                on_click=lambda e, selected=key: self.set_workspace(selected),
                            )
                            for label, key in destinations
                        ],
                        ft.Container(expand=True),
                    ],
                    spacing=TOKENS.gap_3,
                ),
                padding=20,
            ),
        )

    def _build_workspace_body(self) -> ft.Control:
        if self.current_workspace == WORKSPACE_RENDER:
            return self._build_render_workspace()
        if self.current_workspace == WORKSPACE_ANALYTICS:
            return self._build_analytics_workspace()
        return self._build_projects_workspace()

    def _workspace_label(self) -> str:
        return {
            WORKSPACE_PROJECTS: "Projects",
            WORKSPACE_RENDER: "Render Dashboard",
            WORKSPACE_ANALYTICS: "Analytics",
        }.get(self.current_workspace, "Workspace")

    def _build_projects_workspace(self) -> ft.Control:
        if self.child_view_mode == CHILD_VIEW_TOOLS and self.selected_child() is not None:
            return self._build_child_tools_workspace()
        if self.child_view_mode == CHILD_VIEW_LIST and self.selected_parent() is not None:
            return self._build_child_projects_workspace()

        parent = self.selected_parent()
        parent_list = self.parent_presets()
        return ft.Column(
            [
                section_header(
                    "Projects",
                    "Manage parent projects and their child workspaces.",
                    actions=[
                        self._filled_button("Add", lambda e: self.open_parent_dialog(), icon=ft.Icons.ADD_ROUNDED),
                        self._outline_button(
                            "Edit",
                            lambda e: self.open_parent_dialog(existing=parent) if parent else None,
                            icon=ft.Icons.EDIT_OUTLINED,
                        ),
                        self._danger_button(
                            "Delete",
                            lambda e: self.open_confirm_delete_parent_dialog(parent) if parent else None,
                            icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                        ),
                        self._outline_button(
                            "Open child projects",
                            lambda e: self.open_child_projects(),
                            icon=ft.Icons.ARROW_FORWARD_ROUNDED,
                        ),
                    ],
                ),
                ft.Row(
                    [
                        self._card(
                            ft.Column(
                                [
                                    app_text("Parent projects", "card_title"),
                                    app_text("Manage top-level project groups.", "small", color=TOKENS.text_secondary),
                                    ft.Container(height=2),
                                    ft.ListView(
                                        expand=True,
                                        spacing=TOKENS.gap_2,
                                        padding=0,
                                        controls=[
                                            ft.Container(
                                                border_radius=TOKENS.radius_lg,
                                                bgcolor=TOKENS.surface_tint if parent and item.id == parent.id else TOKENS.surface,
                                                border=ft.border.all(1, TOKENS.primary_tint if parent and item.id == parent.id else TOKENS.border_subtle),
                                                padding=ft.padding.symmetric(horizontal=18, vertical=16),
                                                ink=True,
                                                on_click=lambda e, parent_id=item.id: self._select_parent(parent_id),
                                                content=ft.Column(
                                                    [
                                                        app_text(item.name, "title"),
                                                        app_text(
                                                            f"{len(self.state.manual_child_projects.get(item.id, []))} child project(s)",
                                                            "small",
                                                            color=TOKENS.text_secondary,
                                                        ),
                                                    ],
                                                    spacing=2,
                                                ),
                                            )
                                            for item in parent_list
                                        ],
                                    ),
                                ],
                                spacing=TOKENS.gap_3,
                                expand=True,
                            ),
                            expand=5,
                        ),
                        self._card(
                            ft.Column(
                                [
                                    app_text("Details", "card_title"),
                                    app_text(parent.name if parent else "No parent selected", "section_title"),
                                    ft.ResponsiveRow(
                                        [
                                            self._detail_stat("Author", parent.author or "(empty)"),
                                            self._detail_stat("Publisher", parent.publisher or "(empty)"),
                                            self._detail_stat(
                                                "Child projects",
                                                str(len(self.state.manual_child_projects.get(parent.id, [])) if parent else 0),
                                                accent=True,
                                            ),
                                        ],
                                        columns=12,
                                        run_spacing=TOKENS.gap_3,
                                        spacing=TOKENS.gap_3,
                                    ),
                                    ft.Container(expand=True),
                                    app_text(
                                        "Use this area to inspect the selected parent preset before opening child workspaces.",
                                        "small",
                                        color=TOKENS.text_muted,
                                    ),
                                ],
                                spacing=TOKENS.gap_4,
                            ),
                            expand=4,
                        ),
                    ],
                    expand=True,
                    spacing=TOKENS.gap_4,
                ),
            ],
            expand=True,
            spacing=TOKENS.gap_4,
        )

    def _build_child_projects_workspace(self) -> ft.Control:
        parent = self.selected_parent()
        children = self.child_projects_for_active_parent()
        selected_child = self.selected_child()
        return ft.Column(
            [
                section_header(
                    f"{parent.name} · {len(children)} child project(s)",
                    "Create and manage child projects under this parent.",
                    actions=[
                        self._ghost_button("Back", lambda e: self._back_to_parents(), icon=ft.Icons.ARROW_BACK_ROUNDED),
                        self._filled_button("Create project", lambda e: self.create_child_project(), icon=ft.Icons.ADD_ROUNDED),
                        self._danger_button(
                            f"Delete ({len(self.bulk_child_selection)})" if self.child_bulk_mode and self.bulk_child_selection else "Delete",
                            lambda e: self.delete_selected_children(),
                            icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                        ),
                    ],
                ),
                ft.Row(
                    [
                        self._card(
                            ft.Column(
                                [
                                    section_header(
                                        "Child projects",
                                        "Double click a row to open the tool workspace.",
                                        compact=True,
                                    ),
                                    ft.Row(
                                        [
                                            ft.TextButton(
                                                "All" if self.child_bulk_mode else "No.",
                                                on_click=lambda e: self._toggle_child_bulk_mode(),
                                            ),
                                            app_text("Project", "label", weight=ft.FontWeight.W_600),
                                            ft.Container(expand=True),
                                            app_text("Status", "label", weight=ft.FontWeight.W_600),
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    ft.Divider(height=1, color=TOKENS.border_subtle),
                                    ft.ListView(
                                        expand=True,
                                        spacing=8,
                                        controls=[self._build_child_row(item) for item in children],
                                    ),
                                ],
                                expand=True,
                                spacing=8,
                            ),
                            expand=5,
                        ),
                        self._card(
                            ft.Column(
                                [
                                    app_text("Project details", "card_title"),
                                    app_text(selected_child.name if selected_child else "Select a child project", "section_title"),
                                    app_text(
                                        "Double click a row to open the tool workspace.",
                                        "small",
                                        color=TOKENS.text_secondary,
                                    ),
                                    ft.Container(height=TOKENS.gap_2),
                                    self._detail_stat("Status", selected_child.status if selected_child else "Draft"),
                                    self._detail_stat("Seeding comments", str(len(selected_child.parsed_comments)) if selected_child else "0"),
                                ],
                                spacing=TOKENS.gap_3,
                            ),
                            expand=4,
                        ),
                    ],
                    expand=True,
                    spacing=TOKENS.gap_4,
                ),
            ],
            expand=True,
            spacing=TOKENS.gap_4,
        )

    def _build_render_workspace(self) -> ft.Control:
        summary = ft.Text(
            f"Loaded {len(self.projects)} video(s) · Selected {len(self.sorted_selected_projects())}",
            style=text_style("small", color=TOKENS.text_secondary),
        )

        return ft.Column(
            [
                section_header(
                    "CapCut projects",
                    "Track queue status and run sync actions directly from the project list.",
                    actions=[
                        self._outline_button("Reload", lambda e: self._reload_render_projects(), icon=ft.Icons.REFRESH_ROUNDED),
                    ],
                    compact=True,
                ),
                self._card(
                    ft.Column(
                        [
                            self._build_render_table_header(),
                            ft.Divider(height=1, color=TOKENS.border_subtle),
                            ft.ListView(
                                expand=True,
                                spacing=0,
                                controls=[self._build_render_row(project) for project in self.projects],
                            ),
                            ft.Container(height=TOKENS.gap_3),
                            app_text(
                                f"Loaded {len(self.projects)} video(s) · Selected {len(self.sorted_selected_projects())} · Completed {len([p for p in self.projects if p.status is ProjectStatus.done])}",
                                "small",
                                color=TOKENS.text_secondary,
                            ),
                            ft.Container(height=TOKENS.gap_3),
                            ft.ResponsiveRow(
                                [
                                    ft.Container(col={"sm": 6, "md": 4}, content=self._outline_button("Reload Projects", lambda e: self._reload_render_projects(), expand=True)),
                                    ft.Container(col={"sm": 6, "md": 4}, content=self._outline_button("Sync Audio", lambda e: self.run_sync(sync_project_audio, "Sync Audio"), expand=True)),
                                    ft.Container(col={"sm": 6, "md": 4}, content=self._outline_button("Sync Images", lambda e: self.run_sync(sync_project_images, "Sync Images"), expand=True)),
                                    ft.Container(col={"sm": 6, "md": 4}, content=self._outline_button("Sync Captions", lambda e: self.run_sync(sync_project_captions, "Sync Captions"), expand=True)),
                                    ft.Container(col={"sm": 6, "md": 4}, content=self._filled_button("Auto Render", lambda e: self.auto_render_placeholder(), expand=True)),
                                    ft.Container(col={"sm": 6, "md": 4}, content=self._filled_button("Automate", lambda e: self.automate_placeholder(), expand=True)),
                                ],
                                columns=12,
                                spacing=TOKENS.gap_3,
                                run_spacing=TOKENS.gap_3,
                            ),
                        ],
                        expand=True,
                        spacing=0,
                    ),
                    expand=True,
                ),
            ],
            expand=True,
            spacing=14,
        )

    def _build_analytics_workspace(self) -> ft.Control:
        child_count = sum(len(items) for items in self.state.manual_child_projects.values())
        return ft.Column(
            [
                section_header(
                    "Analytics",
                    "High-level operational summary for parent presets, child workspaces, and CapCut inventory.",
                    compact=True,
                ),
                ft.Row(
                    [
                        self._metric_card("Parent projects", str(len(self.state.presets))),
                        self._metric_card("Child projects", str(child_count)),
                        self._metric_card("CapCut projects", str(len(self.projects))),
                    ],
                    spacing=TOKENS.gap_4,
                ),
            ],
            spacing=TOKENS.gap_4,
        )

    def _build_child_tools_workspace(self) -> ft.Control:
        child = self.selected_child()
        if child is None:
            self.child_view_mode = CHILD_VIEW_LIST
            return self._build_child_projects_workspace()

        tool_map = [
            ("AI Gen", CHILD_TOOL_AI_GEN),
            ("AI Audio", CHILD_TOOL_AI_AUDIO),
            ("Livestream", CHILD_TOOL_LIVESTREAM),
        ]
        return ft.Row(
            [
                ft.Container(
                    width=240,
                    content=app_card(
                        ft.Column(
                            [
                                app_text("Child tools", "utility", color=TOKENS.text_muted),
                                *[
                                    sidebar_item(
                                        label,
                                        active=self.active_child_tool == key,
                                        on_click=lambda e, selected=key: self._set_child_tool(selected),
                                    )
                                    for label, key in tool_map
                                ],
                            ],
                            spacing=TOKENS.gap_2,
                        ),
                        padding=16,
                    ),
                ),
                ft.Container(
                    expand=True,
                    content=ft.Column(
                        [
                            section_header(
                                child.name,
                                "Workspace for child project tools and detailed editing.",
                                actions=[
                                    self._ghost_button("Back", lambda e: self._back_to_child_list(), icon=ft.Icons.ARROW_BACK_ROUNDED),
                                ],
                            ),
                            self._card(self._build_child_tool_content(child), expand=True),
                        ],
                        expand=True,
                        spacing=TOKENS.gap_4,
                    ),
                ),
            ],
            expand=True,
            spacing=TOKENS.gap_4,
        )

    def _build_child_tool_content(self, child: ChildProjectRecord) -> ft.Control:
        if self.active_child_tool == CHILD_TOOL_AI_AUDIO:
            return ft.Column(
                [
                    app_text("AI Audio", "card_title"),
                    app_text("Define notes and generation cues for the audio workflow.", "small", color=TOKENS.text_secondary),
                    app_textarea(
                        label="Audio notes",
                        value=child.ai_audio_notes,
                        min_lines=10,
                        max_lines=12,
                        on_change=lambda e: self.update_child_details(child.id, ai_audio_notes=e.control.value),
                    ),
                ],
                spacing=TOKENS.gap_4,
            )

        if self.active_child_tool == CHILD_TOOL_LIVESTREAM:
            return ft.Column(
                [
                    app_text("Livestream", "card_title"),
                    app_text("Capture script, scene notes, and stream operations for this child project.", "small", color=TOKENS.text_secondary),
                    app_textarea(
                        label="Livestream plan",
                        value=child.livestream_notes,
                        min_lines=10,
                        max_lines=12,
                        on_change=lambda e: self.update_child_details(child.id, livestream_notes=e.control.value),
                    ),
                ],
                spacing=TOKENS.gap_4,
            )

        return ft.Column(
            [
                section_header(
                    "AI Gen",
                    "Control title, description, and comment seeding for the selected child project.",
                    compact=True,
                ),
                app_input(
                    label="Title",
                    value=child.title,
                    on_change=lambda e: self.update_child_details(child.id, title=e.control.value),
                    hint_text="Enter video title",
                ),
                app_textarea(
                    label="Description",
                    value=child.description,
                    min_lines=6,
                    max_lines=8,
                    on_change=lambda e: self.update_child_details(child.id, description=e.control.value),
                    hint_text="Enter description for this child project...",
                ),
                ft.Row(
                    [
                        ft.Container(
                            expand=2,
                            content=app_textarea(
                                label="Comment seeding",
                                value=child.seeding_comments_raw,
                                min_lines=8,
                                max_lines=10,
                                hint_text="Paste comment, mỗi dòng một comment",
                                on_change=lambda e: self._handle_seeding_change(child.id, e.control.value),
                            ),
                        ),
                        ft.Container(
                            expand=1,
                            content=self._card(
                                ft.Column(
                                    [
                                        app_text(f"Parsed comments: {len(child.parsed_comments)}", "title"),
                                        ft.Divider(height=1, color=TOKENS.border_subtle),
                                        ft.ListView(
                                            expand=True,
                                            spacing=TOKENS.gap_2,
                                            controls=[app_text(item, "small") for item in child.parsed_comments]
                                            or [app_text("Chưa có comment nào.", "small", color=TOKENS.text_muted)],
                                        ),
                                    ],
                                    spacing=TOKENS.gap_3,
                                    expand=True,
                                ),
                                padding=18,
                                expand=True,
                            ),
                        ),
                    ],
                    spacing=TOKENS.gap_4,
                    expand=True,
                ),
            ],
            spacing=TOKENS.gap_4,
            expand=True,
        )

    # ---------- small builders ----------
    def _build_render_table_header(self) -> ft.Control:
        return ft.Container(
            padding=ft.padding.symmetric(horizontal=18, vertical=14),
            bgcolor=TOKENS.surface_muted,
            content=ft.Row(
                [
                    ft.Container(width=92, content=app_text("Select", "label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_600)),
                    ft.Container(expand=3, content=app_text("Video", "label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_600)),
                    ft.Container(width=110, content=app_text("Source", "label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_600)),
                    ft.Container(width=120, content=app_text("Status", "label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_600)),
                    ft.Container(expand=2, content=app_text("Project", "label", color=TOKENS.text_secondary, weight=ft.FontWeight.W_600)),
                ],
                spacing=TOKENS.gap_3,
            ),
        )

    def _build_render_row(self, project: ProjectItem) -> ft.Control:
        options = [ft.dropdown.Option("", "Select project")] + [
            ft.dropdown.Option(item.id, item.name) for item in self.parent_presets()
        ]
        status_tone = {
            ProjectStatus.pending: "pending",
            ProjectStatus.processing: "processing",
            ProjectStatus.done: "done",
            ProjectStatus.failed: "failed",
        }.get(project.status, "pending")
        return ft.Container(
            padding=ft.padding.symmetric(horizontal=18, vertical=10),
            border=ft.border.only(bottom=ft.border.BorderSide(1, TOKENS.border_subtle)),
            bgcolor=TOKENS.surface_tint if project.is_selected else TOKENS.surface,
            content=ft.Row(
                [
                    ft.Container(
                        width=92,
                        alignment=ft.Alignment(-1, 0),
                        content=ft.Checkbox(
                            value=project.is_selected,
                            on_change=lambda e, current=project: self.set_project_selected(current, bool(e.control.value)),
                        ),
                    ),
                    ft.Container(expand=3, content=app_text(project.name, "body")),
                    ft.Container(width=110, content=app_text(project.source.label(), "small", color=TOKENS.text_secondary)),
                    ft.Container(
                        width=120,
                        content=status_badge(project.status.label(), status_tone),
                    ),
                    ft.Container(
                        expand=2,
                        content=app_dropdown(
                            value=project.assigned_project_id or None,
                            options=options,
                            on_select=lambda e, current=project: self.assign_project(current, e.control.value or ""),
                        ),
                    ),
                ],
                spacing=TOKENS.gap_3,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_child_row(self, child: ChildProjectRecord) -> ft.Control:
        checked = child.id in self.bulk_child_selection
        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_double_tap=lambda e, child_id=child.id: self.open_child_tools(child_id),
            on_tap=lambda e, child_id=child.id: self._select_child(child_id),
            content=ft.Container(
                border_radius=TOKENS.radius_lg,
                bgcolor=TOKENS.surface_tint if self.active_child_id == child.id else TOKENS.surface,
                border=ft.border.all(1, TOKENS.primary_tint if self.active_child_id == child.id else TOKENS.border_subtle),
                padding=ft.padding.symmetric(horizontal=16, vertical=14),
                content=ft.Row(
                    [
                        ft.Container(
                            width=72,
                            content=ft.Checkbox(
                                value=checked,
                                visible=self.child_bulk_mode,
                                on_change=lambda e, child_id=child.id: self._toggle_bulk_child(child_id, bool(e.control.value)),
                            )
                            if self.child_bulk_mode
                            else app_text(str(child.number), "body", weight=ft.FontWeight.W_600),
                        ),
                        ft.Container(expand=True, content=app_text(child.name, "body", weight=ft.FontWeight.W_600)),
                        self._status_chip(child.status),
                    ],
                    spacing=TOKENS.gap_3,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        )

    def _metric_card(self, label: str, value: str) -> ft.Control:
        return self._card(
            ft.Column(
                [
                    app_text(label, "small", color=TOKENS.text_secondary),
                    app_text(value, "section_title"),
                ],
                spacing=TOKENS.gap_1,
            ),
            expand=True,
        )

    def _status_chip(self, value: str) -> ft.Control:
        tone = {
            "Draft": "pending",
            "Pending": "pending",
            "Processing": "processing",
            "Done": "done",
            "Failed": "failed",
        }.get(value, "neutral")
        return status_badge(value, tone)

    def _card(self, content: ft.Control, *, expand: bool | int = False, padding: int = 22) -> ft.Control:
        return app_card(content, expand=expand, padding=padding)

    def _filled_button(self, label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
        return primary_button(label, on_click, expand=expand, icon=icon)

    def _outline_button(self, label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
        return secondary_button(label, on_click, expand=expand, icon=icon)

    def _ghost_button(self, label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
        return ghost_button(label, on_click, expand=expand, icon=icon)

    def _danger_button(self, label: str, on_click: Any, *, expand: bool = False, icon: ft.IconData | None = None) -> ft.Control:
        return danger_button(label, on_click, expand=expand, icon=icon)

    def _detail_stat(self, label: str, value: str, *, accent: bool = False) -> ft.Control:
        return ft.Container(
            col={"sm": 12, "md": 6},
            padding=ft.padding.all(16),
            border_radius=TOKENS.radius_lg,
            bgcolor=TOKENS.surface_subtle,
            border=ft.border.all(1, TOKENS.border_subtle),
            content=ft.Column(
                [
                    app_text(label, "caption", color=TOKENS.text_muted),
                    app_text(
                        value,
                        "title",
                        color=TOKENS.primary if accent else TOKENS.text_primary,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=4,
            ),
        )

    # ---------- local ui state ----------
    def _reload_render_projects(self) -> None:
        self.refresh_projects()
        self.render()
        self.notify("Đã reload danh sách project CapCut.")

    def _select_parent(self, parent_id: str) -> None:
        self.active_parent_id = parent_id
        children = self.child_projects_for_active_parent()
        self.active_child_id = children[0].id if children else ""
        self.render()

    def _back_to_parents(self) -> None:
        self.child_view_mode = ""
        self.active_child_id = ""
        self.render()

    def _back_to_child_list(self) -> None:
        self.child_view_mode = CHILD_VIEW_LIST
        self.render()

    def _select_child(self, child_id: str) -> None:
        self.active_child_id = child_id
        self.render()

    def _set_child_tool(self, tool_key: str) -> None:
        self.active_child_tool = tool_key
        self.render()

    def _handle_seeding_change(self, child_id: str, raw_text: str) -> None:
        self.update_child_details(child_id, seeding_comments_raw=raw_text)
        self.render()

    def _toggle_child_bulk_mode(self) -> None:
        self.child_bulk_mode = not self.child_bulk_mode
        if not self.child_bulk_mode:
            self.bulk_child_selection.clear()
        self.render()

    def _toggle_bulk_child(self, child_id: str, selected: bool) -> None:
        if selected:
            self.bulk_child_selection.add(child_id)
        else:
            self.bulk_child_selection.discard(child_id)
        self.render()


def main(page: ft.Page) -> None:
    app = AutoCapCutFletApp(page)
    app.start()


def launch() -> None:
    assets_dir = Path(__file__).resolve().parents[2] / "resources"
    ft.app(target=main, view=ft.AppView.FLET_APP, assets_dir=str(assets_dir))
