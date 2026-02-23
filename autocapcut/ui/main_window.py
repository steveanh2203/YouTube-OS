"""Sync audio-only GUI for AutoCapcut prototype."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import List

from loguru import logger
from PySide6.QtCore import QSettings, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QDesktopServices, QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import QGraphicsDropShadowEffect
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus
from autocapcut.services.project_loader import discover_projects
from autocapcut.utils.permissions import check_screen_recording_permission, open_screen_recording_settings
from autocapcut.services.sync_audio import SyncAudioError, SyncSummary, sync_project_audio
from autocapcut.services.sync_images import SyncImageError, ImageSyncSummary, sync_project_images
from autocapcut.services.sync_captions import (
    CaptionSyncSummary,
    SyncCaptionError,
    sync_project_captions,
)
from autocapcut.services.animation import (
    AnimationError,
    EffectError,
    TransitionError,
    apply_animations,
    apply_effect,
    apply_transition,
    clear_transitions,
    remove_animations,
    remove_effects,
)
from autocapcut.services.animation_presets import (
    ANIMATION_PRESETS,
    EFFECT_PRESETS,
    TRANSITION_PRESETS,
)
from autocapcut.services.bulk_rename import BulkRenameError, bulk_rename
from autocapcut.services.background_removal import BackgroundRemovalError, remove_white_background
from autocapcut.services.srt_generator import SRTGeneratorError, generate_merged_srt


class SyncWorker(QThread):
    """Background worker used for sync operations."""

    status_updated = Signal()
    job_finished = Signal()

    def __init__(self, projects: List[ProjectItem], operation, summary_formatter, error_types) -> None:
        super().__init__()
        self.projects = projects
        self.operation = operation
        self.summary_formatter = summary_formatter
        self.error_types = error_types
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # pragma: no cover - thread execution
        logger.info("Sync worker started (%d project(s))", len(self.projects))
        for project in self.projects:
            if self._cancelled:
                logger.info("Sync worker cancelled before project %s", project.name)
                project.status = ProjectStatus.failed
                project.notes = "Cancelled by user"
                self.status_updated.emit()
                break

            project.status = ProjectStatus.processing
            project.notes = "Synchronising…"
            self.status_updated.emit()

            try:
                summary = self.operation(project)
                project.status = ProjectStatus.done
                project.notes = self.summary_formatter(summary)
            except self.error_types as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.exception("Unexpected error while syncing %s", project.name)
                project.status = ProjectStatus.failed
                project.notes = f"Unexpected error: {exc}"

            self.status_updated.emit()

        logger.info("Sync worker finished")
        self.job_finished.emit()


class RenderWorker(QThread):
    """Background worker for Auto Render operations."""

    status_updated = Signal()
    job_finished = Signal(int, int)  # completed, total
    progress_updated = Signal(int, int)
    log_message = Signal(str)

    def __init__(self, projects: List[ProjectItem]) -> None:
        super().__init__()
        self.projects = projects
        self._cancelled = False
        self._completed = 0
        self._failed = 0

    def cancel(self) -> None:
        self._cancelled = True

    def _emit_log(self, message: str) -> None:
        logger.info(message)
        self.log_message.emit(message)

    def run(self) -> None:
        from autocapcut.automation.capcut_automation import MacCapCutAutomation
        from autocapcut.config import APP_CONFIG

        self._emit_log("========== RENDER WORKER STARTED ==========")
        self._emit_log(f"Total projects to render: {len(self.projects)}")
        automation = MacCapCutAutomation()
        self.progress_updated.emit(0, len(self.projects))

        for idx, project in enumerate(self.projects, 1):
            self._emit_log("")
            self._emit_log(f"------ PROJECT {idx}/{len(self.projects)}: {project.name} ------")
            
            if self._cancelled:
                self._emit_log(f"[CANCELLED] Render cancelled before project {project.name}")
                project.status = ProjectStatus.failed
                project.notes = "Cancelled by user"
                self.status_updated.emit()
                break

            project.status = ProjectStatus.processing
            project.notes = "Opening project..."
            self.status_updated.emit()

            try:
                # Step 1: Open project
                self._emit_log("[STEP 1/4] Opening project in CapCut...")
                if not automation.open_project(project):
                    raise RuntimeError("Could not open project")
                self._emit_log("[STEP 1/4] ✓ Project opened")

                project.notes = "Exporting..."
                self.status_updated.emit()

                # Step 2: Start render (dismiss dialogs + Cmd+M + Enter)
                self._emit_log("[STEP 2/4] Starting export (Cmd+M + Enter)...")
                if not automation.start_render(project.name):
                    raise RuntimeError("Could not start render")
                self._emit_log("[STEP 2/4] ✓ Export started")

                project.notes = "Rendering..."
                self.status_updated.emit()

                # Step 3: Wait for render to complete
                self._emit_log("[STEP 3/4] Waiting for export to complete...")
                export_folder = getattr(automation, "last_export_folder", None)
                export_name = getattr(automation, "last_export_name", None)
                if export_folder or export_name:
                    self._emit_log(f"  Monitoring export: folder={export_folder} name={export_name}")
                else:
                    self._emit_log("  Monitoring export via process activity (fallback)")
                if not automation.wait_for_render_complete(
                    APP_CONFIG.render_timeout_sec,
                    export_folder,
                    export_name,
                ):
                    raise RuntimeError("Render timeout")
                self._emit_log("[STEP 3/4] ✓ Export completed")

                # Step 4: Close project
                self._emit_log("[STEP 4/4] Closing project...")
                if not automation.close_project():
                    raise RuntimeError("Could not close project and return to dashboard")
                self._emit_log("[STEP 4/4] ✓ Project closed, back to dashboard")

                project.status = ProjectStatus.done
                project.notes = "Exported successfully"
                self._completed += 1
                self._emit_log(f"[SUCCESS] Project '{project.name}' rendered successfully!")

            except Exception as exc:
                logger.exception("[ERROR] Render failed for %s: %s", project.name, exc)
                self.log_message.emit(f"[ERROR] Render failed for {project.name}: {exc}")
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                self._failed += 1
                # Try to close project even on failure
                try:
                    self._emit_log("[CLEANUP] Attempting to close project...")
                    automation.close_project()
                except Exception:
                    pass

            self.status_updated.emit()
            self.progress_updated.emit(self._completed + self._failed, len(self.projects))

        self._emit_log("")
        self._emit_log("========== RENDER WORKER FINISHED ==========")
        self._emit_log(f"Completed: {self._completed}, Failed: {self._failed}")
        self.job_finished.emit(self._completed, len(self.projects))
class PasteAwareTextEdit(QTextEdit):
    """QTextEdit that emits a signal whenever content is pasted."""

    pasted = Signal()

    def insertFromMimeData(self, source) -> None:  # pragma: no cover - Qt callback
        super().insertFromMimeData(source)
        self.pasted.emit()


def _format_summary(summary: SyncSummary) -> str:
    seconds = summary.audio_duration / 1_000_000
    return f"Updated {summary.updated_segments} segment(s), audio length {seconds:.2f}s"


def _format_image_summary(summary: ImageSyncSummary) -> str:
    note = f"Images synced: {summary.paired}"
    extras = []
    if summary.unmatched_audio:
        extras.append(f"missing image for {len(summary.unmatched_audio)} audio(s)")
    if summary.unmatched_images:
        extras.append(f"missing audio for {len(summary.unmatched_images)} image(s)")
    if extras:
        note += " (" + ", ".join(extras) + ")"
    return note


def _format_caption_summary(summary: CaptionSyncSummary) -> str:
    note = f"Synced {summary.paired} image(s) to captions"
    extras = []
    extra_captions = summary.caption_total - summary.paired
    extra_images = summary.image_total - summary.paired
    if extra_captions > 0:
        extras.append(f"unused captions: {extra_captions}")
    if extra_images > 0:
        extras.append(f"unchanged images: {extra_images}")
    if extras:
        note += " (" + ", ".join(extras) + ")"
    return note


@dataclass
class TableColumns:
    select: int = 0
    name: int = 1
    source: int = 2
    status: int = 3
    notes: int = 4


class MainWindow(QMainWindow):
    """Main application window focused on the Sync Audio feature."""

    columns = TableColumns()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoCapCut")
        self.resize(1280, 740)

        self.projects: list[ProjectItem] = []
        self.project_table: QTableWidget | None = None
        self._worker: SyncWorker | None = None
        self._render_worker: RenderWorker | None = None
        self._last_job_projects: list[ProjectItem] = []
        self.image_folder: Path | None = None
        self.status_label: QLabel | None = None
        self._status_message_override: str | None = None
        self._render_log_dialog: QDialog | None = None
        self._render_log_text: QTextEdit | None = None
        self._render_progress_bar: QProgressBar | None = None
        self._render_progress_label: QLabel | None = None
        self.srt_input_path: Path | None = None
        self.srt_content_file_path: Path | None = None
        self._generated_srt: str | None = None
        self._nav_buttons: dict[str, QPushButton] = {}
        self._nav_group: QButtonGroup | None = None
        self._tool_stack: QStackedWidget | None = None
        self._tool_title_label: QLabel | None = None
        self._font_family: str = "Space Grotesk"
        self._updating_srt_paste_text: bool = False
        self._settings = QSettings("AutoCapCut", "AutoCapCut")
        self._last_srt_output_dir: Path = self._load_last_srt_output_dir()

        self._build_ui()
        self.refresh_projects()

    # region Qt overrides
    def closeEvent(self, event: QCloseEvent) -> None:  # pragma: no cover - GUI callback
        if self._render_worker and self._render_worker.isRunning():
            choice = QMessageBox.question(
                self,
                "Render in progress",
                "Auto Render is still running. Stop and exit?",
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._render_worker.cancel()
                self._render_worker.wait(2000)
                super().closeEvent(event)
            else:
                event.ignore()
            return
        if self._worker and self._worker.isRunning():
            choice = QMessageBox.question(
                self,
                "Sync in progress",
                "Audio synchronisation is still running. Stop and exit?",
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._worker.cancel()
                self._worker.wait(2000)
                super().closeEvent(event)
            else:
                event.ignore()
        else:
            super().closeEvent(event)

    # endregion

    def _build_ui(self) -> None:
        self._font_family = self._load_fonts()

        container = QWidget(self)
        container.setObjectName("rootWidget")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        sidebar = self._build_sidebar()
        tool_panel = self._build_tool_panel()
        project_panel = self._build_project_panel()

        # Subtle drop shadows for depth
        for widget, blur, opacity in (
            (tool_panel, 24, 18),
            (project_panel, 24, 18),
        ):
            fx = QGraphicsDropShadowEffect()
            fx.setBlurRadius(blur)
            fx.setXOffset(0)
            fx.setYOffset(2)
            fx.setColor(QColor(0, 0, 0, opacity))
            widget.setGraphicsEffect(fx)

        layout.addWidget(sidebar)
        layout.addWidget(tool_panel, 5)
        layout.addWidget(project_panel, 8)

        container.setLayout(layout)
        self.setCentralWidget(container)
        self._apply_styles()
        self._set_job_controls_state(False)

    def _load_fonts(self) -> str:
        """Load Space Grotesk from resources/fonts/. Returns family name."""
        here = Path(__file__).resolve().parent.parent.parent
        font_path = here / "resources" / "fonts" / "SpaceGrotesk.ttf"
        family = "Space Grotesk"
        if font_path.exists():
            fid = QFontDatabase.addApplicationFont(str(font_path))
            if fid != -1:
                loaded = QFontDatabase.applicationFontFamilies(fid)
                if loaded:
                    family = loaded[0]
        app = QApplication.instance()
        if app:
            app.setFont(QFont(family, 14))
        return family

    def _build_sidebar(self) -> QFrame:
        """Build the dark left navigation sidebar."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(216)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── App Header ──────────────────────────────────────────────────────
        header = QWidget()
        header.setObjectName("sidebarHeader")
        hdr_layout = QVBoxLayout(header)
        hdr_layout.setContentsMargins(20, 24, 20, 18)
        hdr_layout.setSpacing(3)
        app_name = QLabel("AutoCapCut")
        app_name.setObjectName("appName")
        app_tagline = QLabel("CapCut Automation")
        app_tagline.setObjectName("appTagline")
        hdr_layout.addWidget(app_name)
        hdr_layout.addWidget(app_tagline)
        layout.addWidget(header)

        # ── Navigation ───────────────────────────────────────────────────────
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        nav_area = QWidget()
        nav_area.setObjectName("navArea")
        nav_layout = QVBoxLayout(nav_area)
        nav_layout.setContentsMargins(12, 4, 12, 4)
        nav_layout.setSpacing(2)

        # Section: Project Tools
        sec1 = QLabel("PROJECT TOOLS")
        sec1.setObjectName("navSection")
        nav_layout.addSpacing(14)
        nav_layout.addWidget(sec1)
        nav_layout.addSpacing(4)

        for key, label in (
            ("animation", "Animation"),
            ("effect", "Effects"),
            ("transition", "Transitions"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("navBtn")
            btn.clicked.connect(lambda _checked, k=key: self._switch_tool(k))
            self._nav_buttons[key] = btn
            self._nav_group.addButton(btn)
            nav_layout.addWidget(btn)

        # Section: File Tools
        sec2 = QLabel("FILE TOOLS")
        sec2.setObjectName("navSection")
        nav_layout.addSpacing(14)
        nav_layout.addWidget(sec2)
        nav_layout.addSpacing(4)

        for key, label in (
            ("remove_bg", "Background Removal"),
            ("rename", "File Rename"),
            ("srt", "SRT Generator"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("navBtn")
            btn.clicked.connect(lambda _checked, k=key: self._switch_tool(k))
            self._nav_buttons[key] = btn
            self._nav_group.addButton(btn)
            nav_layout.addWidget(btn)

        layout.addWidget(nav_area)
        layout.addStretch(1)

        # Select first nav item by default
        if "animation" in self._nav_buttons:
            self._nav_buttons["animation"].setChecked(True)

        return sidebar

    def _build_tool_panel(self) -> QFrame:
        """Build the centre tool panel (replaces old QTabWidget)."""
        panel = QFrame()
        panel.setObjectName("toolPanel")
        panel.setMinimumWidth(320)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Panel Header ─────────────────────────────────────────────────────
        tool_header = QWidget()
        tool_header.setObjectName("toolHeader")
        th_layout = QHBoxLayout(tool_header)
        th_layout.setContentsMargins(20, 18, 20, 14)
        self._tool_title_label = QLabel("Animation")
        self._tool_title_label.setObjectName("toolTitle")
        th_layout.addWidget(self._tool_title_label)
        th_layout.addStretch(1)
        layout.addWidget(tool_header)

        # ── Stacked Tool Pages ───────────────────────────────────────────────
        self._tool_stack = QStackedWidget()
        self._tool_stack.setObjectName("toolStack")
        # Index must match _switch_tool's index_map
        self._tool_stack.addWidget(self._build_animation_tab())          # 0
        self._tool_stack.addWidget(self._build_effect_tab())              # 1
        self._tool_stack.addWidget(self._build_transition_tab())          # 2
        self._tool_stack.addWidget(self._build_remove_background_tab())   # 3
        self._tool_stack.addWidget(self._build_rename_tab())              # 4
        self._tool_stack.addWidget(self._build_srt_generator_tab())       # 5
        layout.addWidget(self._tool_stack, 1)

        return panel

    def _switch_tool(self, key: str) -> None:
        """Switch the visible tool panel page and update the header title."""
        index_map = {
            "animation": 0,
            "effect":    1,
            "transition": 2,
            "remove_bg": 3,
            "rename":    4,
            "srt":       5,
        }
        titles = {
            "animation": "Animation",
            "effect": "Effects",
            "transition": "Transitions",
            "remove_bg":  "Remove Background",
            "rename": "Rename Files",
            "srt": "SRT Generator",
        }
        if self._tool_stack is not None:
            self._tool_stack.setCurrentIndex(index_map.get(key, 0))
        if self._tool_title_label is not None:
            self._tool_title_label.setText(titles.get(key, ""))

    def _build_animation_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        mode_label = QLabel("Animation Type")
        mode_label.setObjectName("sectionLabel")
        layout.addWidget(mode_label)

        mode_container = QWidget()
        mode_row = QHBoxLayout(mode_container)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(16)
        mode_row.addStretch(1)
        self.animation_mode_group = QButtonGroup(self)
        self.animation_mode_group.setExclusive(True)
        self.animation_mode_buttons: dict[str, QPushButton] = {}
        for order, (key, caption) in enumerate((
            ("in", "In"),
            ("out", "Out"),
            ("combo", "Combo"),
        )):
            button = QPushButton(caption)
            button.setCheckable(True)
            button.setProperty("variant", "pill")
            if key == "in":
                button.setChecked(True)
            self.animation_mode_group.addButton(button, order)
            self.animation_mode_buttons[key] = button
            button.toggled.connect(self._populate_animation_list)
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        layout.addWidget(mode_container)

        search_label = QLabel("Search")
        search_label.setObjectName("sectionLabel")
        layout.addWidget(search_label)

        self.animation_search = QLineEdit()
        self.animation_search.setPlaceholderText("Type to search animations…")
        self.animation_search.setObjectName("searchField")
        self.animation_search.textChanged.connect(self._populate_animation_list)
        layout.addWidget(self.animation_search)

        self.animation_list = QListWidget()
        self.animation_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.animation_list.setObjectName("presetList")
        self._populate_animation_list()
        layout.addWidget(self.animation_list, 1)

        duration_frame = QFrame()
        duration_frame.setObjectName("durationFrame")
        duration_layout = QHBoxLayout(duration_frame)
        duration_layout.setContentsMargins(12, 10, 12, 10)
        duration_layout.setSpacing(8)

        duration_label = QLabel("Duration")
        duration_label.setObjectName("sectionLabel")
        duration_layout.addWidget(duration_label)

        self.animation_duration_spin = QDoubleSpinBox()
        self.animation_duration_spin.setRange(0.0, 10.0)
        self.animation_duration_spin.setDecimals(2)
        self.animation_duration_spin.setSingleStep(0.1)
        self.animation_duration_spin.setSuffix(" s")
        self.animation_duration_spin.setValue(0.0)
        self.animation_duration_spin.setToolTip("0s = use CapCut default duration")
        duration_layout.addWidget(self.animation_duration_spin)

        duration_layout.addStretch(1)
        layout.addWidget(duration_frame)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        self.insert_animation_button = QPushButton("Insert Animation")
        self.insert_animation_button.setProperty("variant", "primary")
        self.insert_animation_button.clicked.connect(self._handle_animation_insert)
        self.remove_animation_button = QPushButton("Remove Animation")
        self.remove_animation_button.setProperty("variant", "danger")
        self.remove_animation_button.clicked.connect(self._handle_animation_remove)
        button_row.addWidget(self.insert_animation_button)
        button_row.addWidget(self.remove_animation_button)
        layout.addLayout(button_row)

        return tab

    def _build_effect_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        info = QLabel("Select visual effects to overlay on your clips.")
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.effect_search = QLineEdit()
        self.effect_search.setPlaceholderText("Type to search effects…")
        self.effect_search.setObjectName("searchField")
        self.effect_search.textChanged.connect(self._populate_effect_list)
        layout.addWidget(self.effect_search)

        self.effect_list = QListWidget()
        self.effect_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.effect_list.setObjectName("presetList")
        self._populate_effect_list()
        layout.addWidget(self.effect_list, 1)

        effect_buttons = QHBoxLayout()
        effect_buttons.setSpacing(12)
        self.insert_effect_button = QPushButton("Insert Effect")
        self.insert_effect_button.setProperty("variant", "primary")
        self.insert_effect_button.clicked.connect(self._handle_effect_insert)
        self.remove_effect_button = QPushButton("Remove Effect")
        self.remove_effect_button.setProperty("variant", "danger")
        self.remove_effect_button.clicked.connect(self._handle_effect_remove)
        effect_buttons.addWidget(self.insert_effect_button)
        effect_buttons.addWidget(self.remove_effect_button)
        layout.addLayout(effect_buttons)

        return tab

    def _build_transition_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.transition_list = QListWidget()
        self.transition_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.transition_list.setObjectName("presetList")
        black_fade_item = QListWidgetItem("Black Fade")
        black_fade_item.setData(Qt.ItemDataRole.UserRole, "black_fade")
        self.transition_list.addItem(black_fade_item)
        black_fade_item.setSelected(True)
        layout.addWidget(self.transition_list, 1)

        duration_frame = QFrame()
        duration_frame.setObjectName("durationFrame")
        duration_layout = QHBoxLayout(duration_frame)
        duration_layout.setContentsMargins(12, 10, 12, 10)
        duration_layout.setSpacing(8)

        duration_label = QLabel("Duration")
        duration_label.setObjectName("sectionLabel")
        duration_layout.addWidget(duration_label)

        self.transition_duration_spin = QDoubleSpinBox()
        self.transition_duration_spin.setRange(0.1, 3.0)
        self.transition_duration_spin.setDecimals(2)
        self.transition_duration_spin.setSingleStep(0.1)
        self.transition_duration_spin.setSuffix(" s")
        self.transition_duration_spin.setValue(0.5)
        duration_layout.addWidget(self.transition_duration_spin)

        duration_layout.addStretch(1)
        layout.addWidget(duration_frame)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        self.transition_apply_btn = QPushButton("Insert Transition")
        self.transition_apply_btn.setProperty("variant", "primary")
        self.transition_apply_btn.clicked.connect(self._handle_transition_apply)
        self.transition_clear_btn = QPushButton("Remove Transition")
        self.transition_clear_btn.setProperty("variant", "danger")
        self.transition_clear_btn.clicked.connect(self._handle_transition_clear)
        button_row.addWidget(self.transition_apply_btn)
        button_row.addWidget(self.transition_clear_btn)
        layout.addLayout(button_row)

        return tab

    def _build_remove_background_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        info = QLabel(
            "Convert all JPG/JPEG images in the selected folder to PNG and remove backgrounds."
        )
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        folder_label = QLabel("Image Folder")
        folder_label.setObjectName("sectionLabel")
        layout.addWidget(folder_label)

        folder_row = QHBoxLayout()
        self.bg_path_edit = QLineEdit()
        self.bg_path_edit.setPlaceholderText("Choose image folder…")
        self.bg_path_edit.setReadOnly(True)
        self.bg_path_edit.setObjectName("assetPath")
        folder_row.addWidget(self.bg_path_edit, 1)

        bg_browse_btn = QPushButton("Browse Folder")
        bg_browse_btn.setProperty("variant", "secondary")
        bg_browse_btn.clicked.connect(self._handle_select_image_folder)
        folder_row.addWidget(bg_browse_btn)
        layout.addLayout(folder_row)

        # Mode selection
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Mode:")
        mode_layout.addWidget(mode_label)

        self.bg_removal_mode = QComboBox()
        self.bg_removal_mode.addItem("Auto (Smart detection)", "auto")
        self.bg_removal_mode.addItem("Fast (White background only)", "fast")
        self.bg_removal_mode.addItem("AI (Any background, accurate)", "ai")
        self.bg_removal_mode.setCurrentIndex(0)  # Default to Auto
        self.bg_removal_mode.setToolTip(
            "Auto: Automatically detects white backgrounds and uses fast mode, "
            "otherwise uses AI mode.\n"
            "Fast: Quick processing for white backgrounds only (~0.05s/image).\n"
            "AI: High accuracy for any background type (~0.8-1.5s/image)."
        )
        mode_layout.addWidget(self.bg_removal_mode)
        mode_layout.addStretch(1)
        layout.addLayout(mode_layout)

        remove_button = QPushButton("Remove Background")
        remove_button.setProperty("variant", "primary")
        remove_button.clicked.connect(self._handle_remove_background)
        layout.addWidget(remove_button)

        layout.addStretch(1)
        return tab

    def _build_rename_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        info = QLabel("Rename files to image_### while keeping their extensions.")
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        path_row = QHBoxLayout()
        self.rename_path_edit = QLineEdit()
        self.rename_path_edit.setPlaceholderText("Choose folder to rename…")
        self.rename_path_edit.setReadOnly(True)
        self.rename_path_edit.setObjectName("assetPath")
        path_row.addWidget(self.rename_path_edit, 1)

        rename_browse_btn = QPushButton("Browse Folder")
        rename_browse_btn.setProperty("variant", "secondary")
        rename_browse_btn.clicked.connect(self._handle_select_rename_folder)
        self.rename_browse_btn = rename_browse_btn
        path_row.addWidget(rename_browse_btn)
        layout.addLayout(path_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        start_label = QLabel("Start number")
        start_label.setObjectName("sectionLabel")
        grid.addWidget(start_label, 0, 0)

        self.rename_start_spin = QSpinBox()
        self.rename_start_spin.setRange(1, 999_999)
        self.rename_start_spin.setValue(1)
        self.rename_start_spin.valueChanged.connect(self._update_rename_preview)
        grid.addWidget(self.rename_start_spin, 1, 0)

        end_label = QLabel("End number")
        end_label.setObjectName("sectionLabel")
        grid.addWidget(end_label, 0, 1)

        self.rename_end_spin = QSpinBox()
        self.rename_end_spin.setRange(1, 999_999)
        self.rename_end_spin.setValue(1)
        self.rename_end_spin.valueChanged.connect(self._update_rename_preview)
        grid.addWidget(self.rename_end_spin, 1, 1)

        self.rename_until_end = QCheckBox("Use all files in folder")
        self.rename_until_end.setChecked(True)
        self.rename_until_end.toggled.connect(self._toggle_rename_end_state)
        grid.addWidget(self.rename_until_end, 2, 0, 1, 2)

        layout.addLayout(grid)

        self.rename_hint_label = QLabel("Format: image_001, image_002…")
        self.rename_hint_label.setObjectName("hintLabel")
        self.rename_hint_label.setWordWrap(True)
        layout.addWidget(self.rename_hint_label)

        self.rename_run_btn = QPushButton("Rename Files")
        self.rename_run_btn.setProperty("variant", "primary")
        self.rename_run_btn.clicked.connect(self._handle_bulk_rename_range)
        layout.addWidget(self.rename_run_btn)

        layout.addStretch(1)

        self._toggle_rename_end_state(self.rename_until_end.isChecked())
        self._update_rename_preview()
        return tab

    def _build_srt_generator_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── SRT Input ──────────────────────────────────────────────
        srt_label = QLabel("CapCut SRT File")
        srt_label.setObjectName("sectionLabel")
        layout.addWidget(srt_label)

        srt_row = QHBoxLayout()
        self.srt_input_path_edit = QLineEdit()
        self.srt_input_path_edit.setPlaceholderText("Select .srt file…")
        self.srt_input_path_edit.setReadOnly(True)
        self.srt_input_path_edit.setObjectName("assetPath")
        srt_row.addWidget(self.srt_input_path_edit, 1)

        srt_browse_btn = QPushButton("Browse")
        srt_browse_btn.setProperty("variant", "secondary")
        srt_browse_btn.clicked.connect(self._handle_srt_input_browse)
        srt_row.addWidget(srt_browse_btn)
        layout.addLayout(srt_row)

        # ── Content Input ──────────────────────────────────────────
        content_label = QLabel("Source Content")
        content_label.setObjectName("sectionLabel")
        layout.addWidget(content_label)

        # Mode toggle: File vs Paste
        mode_row = QHBoxLayout()
        self.srt_content_file_radio = QRadioButton("Upload file")
        self.srt_content_paste_radio = QRadioButton("Paste text")
        self.srt_content_paste_radio.setChecked(True)
        mode_row.addWidget(self.srt_content_file_radio)
        mode_row.addWidget(self.srt_content_paste_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        # Widget: upload file
        self.srt_content_file_widget = QWidget()
        file_row = QHBoxLayout(self.srt_content_file_widget)
        file_row.setContentsMargins(0, 0, 0, 0)
        self.srt_content_path_edit = QLineEdit()
        self.srt_content_path_edit.setPlaceholderText("Select content file (.txt, .rtf…)")
        self.srt_content_path_edit.setReadOnly(True)
        self.srt_content_path_edit.setObjectName("assetPath")
        file_row.addWidget(self.srt_content_path_edit, 1)
        content_browse_btn = QPushButton("Browse")
        content_browse_btn.setProperty("variant", "secondary")
        content_browse_btn.clicked.connect(self._handle_content_file_browse)
        file_row.addWidget(content_browse_btn)

        # Widget: paste text
        self.srt_content_paste_widget = QWidget()
        paste_layout = QVBoxLayout(self.srt_content_paste_widget)
        paste_layout.setContentsMargins(0, 0, 0, 0)
        self.srt_content_text = PasteAwareTextEdit()
        self.srt_content_text.setPlaceholderText(
            "Paste content here (one line = one caption)\n"
            "Example:\n"
            "1. Have you noticed your hands tingling…\n"
            "2. or your legs feeling weaker…"
        )
        self.srt_content_text.setObjectName("contentPasteArea")
        self.srt_content_text.pasted.connect(self._auto_number_srt_paste_content)
        self.srt_content_text.textChanged.connect(self._handle_srt_content_text_changed)
        paste_layout.addWidget(self.srt_content_text)

        layout.addWidget(self.srt_content_file_widget)
        layout.addWidget(self.srt_content_paste_widget, 1)

        self.srt_content_count_label = QLabel("Total content lines: 0")
        self.srt_content_count_label.setObjectName("hintLabel")
        layout.addWidget(self.srt_content_count_label)

        # Connect radios → toggle visibility
        self.srt_content_file_radio.toggled.connect(self._toggle_srt_content_mode)
        self.srt_content_paste_radio.toggled.connect(self._toggle_srt_content_mode)
        self._toggle_srt_content_mode()

        # ── Generate button ────────────────────────────────────────
        self.srt_generate_btn = QPushButton("Generate SRT")
        self.srt_generate_btn.setProperty("variant", "primary")
        self.srt_generate_btn.clicked.connect(self._handle_srt_generate)
        layout.addWidget(self.srt_generate_btn)

        # ── Result label ───────────────────────────────────────────
        self.srt_result_label = QLabel("")
        self.srt_result_label.setObjectName("hintLabel")
        self.srt_result_label.setWordWrap(True)
        layout.addWidget(self.srt_result_label)

        return tab

    def _build_project_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("projectPanel")
        panel.setMinimumWidth(640)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 24, 24, 20)
        panel_layout.setSpacing(16)

        title = QLabel("Projects")
        title.setObjectName("projectTitle")
        panel_layout.addWidget(title)

        self.project_table = QTableWidget(panel)
        self.project_table.setObjectName("projectTable")
        self.project_table.setColumnCount(5)
        self.project_table.setAlternatingRowColors(True)
        self.project_table.setShowGrid(False)
        self.project_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.project_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.project_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.project_table.verticalHeader().setVisible(False)
        self.project_table.verticalHeader().setDefaultSectionSize(40)
        self.project_table.setHorizontalHeaderLabels(
            ["Select", "Project", "Source", "Status", "Notes"]
        )
        header_view = self.project_table.horizontalHeader()
        header_view.setHighlightSections(False)
        header_view.setSectionsClickable(False)
        header_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header_view.setSectionResizeMode(self.columns.select, header_view.ResizeMode.Fixed)
        header_view.setMinimumSectionSize(40)
        header_view.setSectionResizeMode(self.columns.name, header_view.ResizeMode.Stretch)
        header_view.setSectionResizeMode(self.columns.source, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.status, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.notes, header_view.ResizeMode.Stretch)
        self.project_table.setColumnWidth(self.columns.select, 78)
        panel_layout.addWidget(self.project_table, 1)

        self.status_label = QLabel("Loading projects…")
        self.status_label.setObjectName("statusLabel")
        panel_layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        self.reload_button = QPushButton("Reload Projects")
        self.reload_button.setProperty("variant", "reload")
        self.reload_button.clicked.connect(self.refresh_projects)

        self.sync_audio_button = QPushButton("Sync Audio")
        self.sync_audio_button.setProperty("variant", "audio")
        self.sync_audio_button.clicked.connect(self._handle_sync_clicked)

        self.sync_images_button = QPushButton("Sync Images")
        self.sync_images_button.setProperty("variant", "images")
        self.sync_images_button.clicked.connect(self._handle_sync_images_clicked)

        self.sync_caption_button = QPushButton("Sync Captions")
        self.sync_caption_button.setProperty("variant", "captions")
        self.sync_caption_button.clicked.connect(self._handle_sync_captions_clicked)

        self.auto_render_button = QPushButton("Auto Render")
        self.auto_render_button.setProperty("variant", "primary")
        self.auto_render_button.clicked.connect(self._handle_auto_render_clicked)

        button_row.addWidget(self.reload_button)
        button_row.addWidget(self.sync_audio_button)
        button_row.addWidget(self.sync_images_button)
        button_row.addWidget(self.sync_caption_button)
        button_row.addStretch(1)
        button_row.addWidget(self.auto_render_button)
        panel_layout.addLayout(button_row)

        return panel

    def _apply_styles(self) -> None:
        import qdarktheme  # light theme base provided by pyqtdarktheme plugin

        base = qdarktheme.load_stylesheet("light")
        ff = self._font_family

        custom = f"""
            * {{
                font-family: "{ff}";
                color: #0F172A;
            }}

            #rootWidget {{
                background: #F1F5F9;
            }}

            #sidebar, #toolPanel, #projectPanel {{
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 14px;
            }}

            #sidebarHeader {{
                background: transparent;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}

            #sidebarHeader QLabel, #navArea QLabel {{
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
            }}

            #appName {{
                font-size: 17px;
                font-weight: 700;
                color: #0F172A;
                letter-spacing: -0.02em;
            }}

            #appTagline {{
                font-size: 11px;
                color: #64748B;
            }}

            #navArea {{
                background: transparent;
                border: none;
            }}

            QLabel#navSection {{
                font-size: 10px;
                font-weight: 700;
                color: #94A3B8;
                letter-spacing: 0.08em;
                background: transparent;
                border: none;
            }}

            QPushButton#navBtn {{
                background: transparent;
                border: none;
                border-radius: 9px;
                padding: 9px 14px;
                min-height: 38px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
                color: #475569;
            }}

            QPushButton#navBtn:hover:!checked {{
                background: #F8FAFC;
                color: #0F172A;
            }}

            QPushButton#navBtn:checked {{
                background: #EFF6FF;
                color: #1D4ED8;
                border-left: 3px solid #2563EB;
                padding-left: 11px;
            }}

            #toolHeader {{
                background: #F8FAFC;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}

            #toolTitle, #projectTitle {{
                font-size: 18px;
                font-weight: 700;
                color: #0F172A;
                letter-spacing: -0.02em;
            }}

            #projectTitle {{
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                margin: 0px;
            }}

            #toolStack {{
                background: #FFFFFF;
                border: none;
            }}

            QPushButton {{
                min-height: 36px;
                padding: 5px 16px;
                border-radius: 10px;
                font-size: 13px;
                font-weight: 600;
            }}

            QPushButton:disabled {{
                background: #E2E8F0;
                color: #94A3B8;
                border: 1px solid #CBD5E1;
            }}

            QPushButton[variant="primary"] {{
                background: #2563EB;
                color: #FFFFFF;
                border: 1px solid #2563EB;
            }}

            QPushButton[variant="primary"]:hover:!disabled {{
                background: #1D4ED8;
                border-color: #1D4ED8;
            }}

            QPushButton[variant="primary"]:pressed:!disabled {{
                background: #1E40AF;
                border-color: #1E40AF;
            }}

            QPushButton[variant="secondary"] {{
                background: #FFFFFF;
                color: #0F172A;
                border: 1px solid #CBD5E1;
            }}

            QPushButton[variant="secondary"]:hover:!disabled {{
                background: #F8FAFC;
                border-color: #94A3B8;
            }}

            QPushButton[variant="reload"] {{
                background: #FFFFFF;
                color: #334155;
                border: 1px solid #CBD5E1;
            }}

            QPushButton[variant="reload"]:hover:!disabled {{
                background: #F8FAFC;
                border-color: #94A3B8;
                color: #0F172A;
            }}

            QPushButton[variant="audio"] {{
                background: #EFF6FF;
                color: #1D4ED8;
                border: 1px solid #BFDBFE;
            }}

            QPushButton[variant="audio"]:hover:!disabled {{
                background: #DBEAFE;
                border-color: #93C5FD;
                color: #1E40AF;
            }}

            QPushButton[variant="images"] {{
                background: #ECFDF5;
                color: #047857;
                border: 1px solid #A7F3D0;
            }}

            QPushButton[variant="images"]:hover:!disabled {{
                background: #D1FAE5;
                border-color: #6EE7B7;
                color: #065F46;
            }}

            QPushButton[variant="captions"] {{
                background: #EEF2FF;
                color: #4338CA;
                border: 1px solid #C7D2FE;
            }}

            QPushButton[variant="captions"]:hover:!disabled {{
                background: #E0E7FF;
                border-color: #A5B4FC;
                color: #3730A3;
            }}

            QPushButton[variant="pill"] {{
                min-width: 86px;
                border-radius: 11px;
                background: #E2E8F0;
                border: 1px solid #E2E8F0;
                color: #334155;
                font-size: 12px;
                font-weight: 600;
            }}

            QPushButton[variant="pill"]:checked {{
                background: #1D4ED8;
                border-color: #1D4ED8;
                color: #FFFFFF;
            }}

            QPushButton[variant="success"] {{
                background: #059669;
                border: 1px solid #059669;
                color: #FFFFFF;
            }}

            QPushButton[variant="success"]:hover:!disabled {{
                background: #047857;
                border-color: #047857;
            }}

            QPushButton[variant="danger"] {{
                background: #DC2626;
                border: 1px solid #DC2626;
                color: #FFFFFF;
            }}

            QPushButton[variant="danger"]:hover:!disabled {{
                background: #B91C1C;
                border-color: #B91C1C;
            }}

            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit {{
                font-size: 13px;
                border: 1px solid #CBD5E1;
                border-radius: 10px;
                background: #FFFFFF;
                padding: 8px 11px;
                color: #0F172A;
                selection-background-color: #DBEAFE;
                selection-color: #1E3A8A;
            }}

            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QTextEdit:focus {{
                border: 1px solid #2563EB;
                background: #FFFFFF;
            }}

            QLineEdit#assetPath {{
                background: #F8FAFC;
                color: #334155;
            }}

            QLabel#sectionLabel, QLabel#assetCaption {{
                font-size: 11px;
                font-weight: 700;
                color: #64748B;
                letter-spacing: 0.06em;
            }}

            QLabel#hintLabel {{
                font-size: 13px;
                color: #64748B;
            }}

            #statusLabel {{
                font-size: 13px;
                color: #475569;
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
            }}

            #durationFrame {{
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
            }}

            QListWidget#presetList {{
                font-size: 13px;
                border: 1px solid #CBD5E1;
                border-radius: 10px;
                background: #FFFFFF;
            }}

            QListWidget#presetList::item {{
                padding: 8px 12px;
                border-radius: 6px;
            }}

            QListWidget#presetList::item:hover:!selected {{
                background: #F8FAFC;
            }}

            QListWidget#presetList::item:selected {{
                background: #DBEAFE;
                color: #1E3A8A;
                font-weight: 600;
            }}

            #projectTable {{
                font-size: 13px;
                border: 1px solid #CBD5E1;
                border-radius: 12px;
                background: #FFFFFF;
                alternate-background-color: #F8FAFC;
                gridline-color: #E2E8F0;
            }}

            #projectTable::item {{
                padding: 7px 8px;
                color: #334155;
            }}

            #projectTable::item:selected {{
                background: #DBEAFE;
                color: #1E3A8A;
            }}

            QTableWidget QTableCornerButton::section {{
                background: #F1F5F9;
                border: none;
            }}

            QHeaderView::section {{
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.04em;
                background: #F1F5F9;
                color: #475569;
                border: none;
                border-right: 1px solid #E2E8F0;
                border-bottom: 1px solid #E2E8F0;
                padding: 10px 8px;
            }}

            QHeaderView::section:checked,
            QHeaderView::section:selected,
            QHeaderView::section:pressed,
            QHeaderView::section:focus {{
                background: #F1F5F9;
                color: #475569;
                border: none;
                border-right: 1px solid #E2E8F0;
                border-bottom: 1px solid #E2E8F0;
                outline: none;
            }}

            QHeaderView::section:last {{
                border-right: none;
            }}

            QHeaderView::section:last:checked,
            QHeaderView::section:last:selected,
            QHeaderView::section:last:pressed,
            QHeaderView::section:last:focus {{
                border-right: none;
            }}

            QTableWidget QWidget, QTableWidget QCheckBox {{
                background: transparent;
                border: none;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox {{
                spacing: 0px;
                margin: 0px;
                padding: 0px;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox::indicator {{
                width: 16px;
                height: 16px;
                border: 1px solid #94A3B8;
                border-radius: 4px;
                background: #FFFFFF;
                image: none;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox::indicator:hover {{
                border-color: #2563EB;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox::indicator:unchecked {{
                image: none;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox::indicator:checked {{
                background: #2563EB;
                border-color: #2563EB;
                image: none;
            }}

            QTableWidget QLabel#rowSelectOrderLabel {{
                min-width: 18px;
                max-width: 18px;
                color: #1D4ED8;
                font-size: 11px;
                font-weight: 700;
                background: transparent;
                border: none;
            }}

            QMessageBox, QProgressDialog, QDialog#renderLogDialog {{
                background: #FFFFFF;
                border: 1px solid #D5DEE9;
                border-radius: 12px;
                font-family: "{ff}";
            }}

            QMessageBox QLabel, QProgressDialog QLabel, QDialog#renderLogDialog QLabel {{
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                color: #334155;
            }}

            QMessageBox QLabel#qt_msgbox_label {{
                min-width: 280px;
            }}

            QMessageBox QPushButton, QProgressDialog QPushButton {{
                min-height: 34px;
                padding: 4px 14px;
                border-radius: 9px;
                background: #FFFFFF;
                color: #0F172A;
                border: 1px solid #CBD5E1;
            }}

            QMessageBox QPushButton:hover:!disabled, QProgressDialog QPushButton:hover:!disabled {{
                background: #F8FAFC;
                border-color: #94A3B8;
            }}

            QProgressDialog QProgressBar, QDialog#renderLogDialog QProgressBar#renderProgressBar {{
                min-height: 12px;
                max-height: 12px;
                border-radius: 6px;
                border: 1px solid #D6DEE9;
                background: #EEF2F7;
                text-align: center;
                color: #334155;
            }}

            QProgressDialog QProgressBar::chunk, QDialog#renderLogDialog QProgressBar#renderProgressBar::chunk {{
                border-radius: 5px;
                background: #2563EB;
            }}

            QDialog#renderLogDialog QLabel#renderProgressLabel {{
                font-size: 13px;
                font-weight: 600;
                color: #334155;
            }}

            QDialog#renderLogDialog QTextEdit#renderLogText {{
                background: #F8FAFC;
                border: 1px solid #D6DEE9;
                border-radius: 10px;
                color: #0F172A;
                padding: 8px 10px;
            }}

            QDialog#renderLogDialog QPushButton#renderCloseButton {{
                min-width: 88px;
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 2px;
            }}

            QScrollBar::handle:vertical {{
                background: #94A3B8;
                border-radius: 4px;
                min-height: 42px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: #64748B;
            }}

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """
        self.setStyleSheet(base + "\n" + custom)

    def _current_animation_mode(self) -> str:
        for key, button in (self.animation_mode_buttons or {}).items():
            if button.isChecked():
                return key
        return "in"

    def _populate_animation_list(self) -> None:
        if not hasattr(self, "animation_list"):
            return
        current_selection = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.animation_list.selectedItems()
        }
        mode = self._current_animation_mode()
        query = (self.animation_search.text() or "").strip().lower()

        self.animation_list.blockSignals(True)
        self.animation_list.clear()
        for preset in ANIMATION_PRESETS.values():
            if preset.category != mode:
                continue
            if query and query not in preset.name.lower():
                continue
            item = QListWidgetItem(preset.name)
            item.setData(Qt.ItemDataRole.UserRole, preset.key)
            if preset.key in current_selection:
                item.setSelected(True)
            self.animation_list.addItem(item)
        self.animation_list.blockSignals(False)

    def _populate_effect_list(self) -> None:
        if not hasattr(self, "effect_list"):
            return
        current_selection = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.effect_list.selectedItems()
        }
        query = (self.effect_search.text() or "").strip().lower()

        self.effect_list.blockSignals(True)
        self.effect_list.clear()
        for preset in EFFECT_PRESETS.values():
            if query and query not in preset.name.lower():
                continue
            item = QListWidgetItem(preset.name)
            item.setData(Qt.ItemDataRole.UserRole, preset.key)
            if preset.key in current_selection:
                item.setSelected(True)
            self.effect_list.addItem(item)
        self.effect_list.blockSignals(False)

    def _selected_animation_keys(self) -> List[str]:
        if not hasattr(self, "animation_list"):
            return []
        keys: List[str] = []
        for item in self.animation_list.selectedItems():
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                keys.append(str(key))
        return keys

    def _selected_effect_keys(self) -> List[str]:
        if not hasattr(self, "effect_list"):
            return []
        keys: List[str] = []
        for item in self.effect_list.selectedItems():
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                keys.append(str(key))
        return keys

    def _set_job_controls_state(self, busy: bool) -> None:
        controls = (
            getattr(self, "reload_button", None),
            getattr(self, "sync_audio_button", None),
            getattr(self, "sync_images_button", None),
            getattr(self, "sync_caption_button", None),
            getattr(self, "rename_run_btn", None),
            getattr(self, "rename_browse_btn", None),
            getattr(self, "insert_animation_button", None),
            getattr(self, "remove_animation_button", None),
            getattr(self, "insert_effect_button", None),
            getattr(self, "remove_effect_button", None),
            getattr(self, "transition_apply_btn", None),
            getattr(self, "transition_clear_btn", None),
            getattr(self, "srt_generate_btn", None),
        )
        for button in controls:
            if button is not None:
                button.setEnabled(not busy)

    def _update_status_label(self, message: str | None = None) -> None:
        if self.status_label is None:
            return
        if message is not None:
            self._status_message_override = message
            self.status_label.setText(message)
            return

        self._status_message_override = None
        total = len(self.projects)
        selected = sum(1 for p in self.projects if p.is_selected)
        completed = sum(1 for p in self.projects if p.status == ProjectStatus.done)
        self.status_label.setText(
            f"Loaded {total} project(s) · Selected {selected} · Completed {completed}"
        )

    def _update_ffmpeg_button_state(self) -> None:
        """Compatibility shim for legacy FFmpeg controls (currently no-op)."""
        return

    # region project table helpers
    @staticmethod
    def _status_color(status: ProjectStatus) -> QColor:
        palette = {
            ProjectStatus.pending: QColor("#64748B"),
            ProjectStatus.processing: QColor("#2563EB"),
            ProjectStatus.done: QColor("#059669"),
            ProjectStatus.failed: QColor("#DC2626"),
        }
        return palette.get(status, QColor("#374151"))

    def _refresh_project_metadata(self, project: ProjectItem) -> None:
        info = project.refresh_metadata()
        project.status = ProjectStatus.pending
        if not info:
            project.notes = "Metadata unavailable"
            return

        parts: list[str] = []
        duration = info.get("duration_s")
        if duration:
            parts.append(f"{duration:.2f}s")
        track_count = info.get("track_count")
        if track_count:
            parts.append(f"{track_count} track(s)")
        video_segments = info.get("video_segments")
        if video_segments:
            parts.append(f"{video_segments} video segment(s)")

        project.notes = " · ".join(parts)

    def _selected_projects_in_order(self) -> list[ProjectItem]:
        selected = [p for p in self.projects if p.is_selected]
        selected.sort(
            key=lambda p: p.selection_order if p.selection_order is not None else 1_000_000
        )
        return selected

    def _normalize_selection_orders(self) -> None:
        order = 1
        for project in self._selected_projects_in_order():
            project.selection_order = order
            order += 1
        for project in self.projects:
            if not project.is_selected:
                project.selection_order = None

    def _refresh_selection_order_widgets(self) -> None:
        if self.project_table is None:
            return
        for row, project in enumerate(self.projects):
            container = self.project_table.cellWidget(row, self.columns.select)
            if container is None:
                continue
            label = container.findChild(QLabel, "rowSelectOrderLabel")
            if label is None:
                continue
            if project.is_selected and project.selection_order is not None:
                label.setText(str(project.selection_order))
            else:
                label.setText("")

    def refresh_projects(self) -> None:
        previously_selected = self._selected_projects_in_order()
        selected_order_by_path = {p.path: idx + 1 for idx, p in enumerate(previously_selected)}
        self.projects = discover_projects()
        for project in self.projects:
            previous_order = selected_order_by_path.get(project.path)
            project.is_selected = previous_order is not None
            project.selection_order = previous_order
            self._refresh_project_metadata(project)
        self._normalize_selection_orders()
        if not self.projects:
            logger.warning("No CapCut projects found. Check your CapCut library path.")
        self._populate_table()
        if self._status_message_override is None:
            self._update_status_label()

    def _populate_table(self) -> None:
        if self.project_table is None:
            return
        self.project_table.setRowCount(len(self.projects))
        for row, project in enumerate(self.projects):
            checkbox = QCheckBox()
            checkbox.setObjectName("rowSelectCheckbox")
            checkbox.setChecked(project.is_selected)
            checkbox.stateChanged.connect(self._make_checkbox_handler(project))
            checkbox_container = QWidget()
            container_layout = QHBoxLayout(checkbox_container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            container_layout.setSpacing(6)
            container_layout.addWidget(checkbox)
            order_label = QLabel("")
            order_label.setObjectName("rowSelectOrderLabel")
            order_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            order_label.setFixedWidth(18)
            if project.is_selected and project.selection_order is not None:
                order_label.setText(str(project.selection_order))
            container_layout.addWidget(order_label)
            self.project_table.setCellWidget(row, self.columns.select, checkbox_container)

            name_item = QTableWidgetItem(project.name)
            source_item = QTableWidgetItem(project.source.label())
            status_item = QTableWidgetItem(project.status.label())
            notes_item = QTableWidgetItem(project.notes)
            status_item.setForeground(QBrush(self._status_color(project.status)))

            for column, item in (
                (self.columns.name, name_item),
                (self.columns.source, source_item),
                (self.columns.status, status_item),
                (self.columns.notes, notes_item),
            ):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.project_table.setItem(row, column, item)

        if self._status_message_override is None:
            self._update_status_label()

    def _make_checkbox_handler(self, project: ProjectItem):
        def handler(state: int) -> None:
            checked = Qt.CheckState(state) == Qt.CheckState.Checked
            if checked and not project.is_selected:
                next_order = max((p.selection_order or 0) for p in self.projects) + 1
                project.selection_order = next_order
            if not checked:
                project.selection_order = None
            project.is_selected = checked
            self._normalize_selection_orders()
            self._refresh_selection_order_widgets()
            logger.debug(
                "Project %s selection=%s order=%s",
                project.name,
                project.is_selected,
                project.selection_order,
            )
            if self._status_message_override is None:
                self._update_status_label()
            else:
                self._update_ffmpeg_button_state()
        return handler

    def _refresh_status_cells(self) -> None:
        if self.project_table is None:
            return
        for row, project in enumerate(self.projects):
            status_item = self.project_table.item(row, self.columns.status)
            notes_item = self.project_table.item(row, self.columns.notes)
            if status_item is not None:
                status_item.setText(project.status.label())
                status_item.setForeground(QBrush(self._status_color(project.status)))
            if notes_item is not None:
                notes_item.setText(project.notes)
        if self._status_message_override is None:
            self._update_status_label()
        else:
            self._update_ffmpeg_button_state()

    # endregion

    def _set_image_folder(self, folder: Path) -> None:
        self.image_folder = folder
        if getattr(self, "bg_path_edit", None):
            self.bg_path_edit.setText(str(folder))
        if getattr(self, "rename_path_edit", None):
            self.rename_path_edit.setText(str(folder))

    def _handle_select_image_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", str(self.image_folder or Path.home()))
        if folder:
            self._set_image_folder(Path(folder))

    def _handle_select_rename_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Rename", str(self.image_folder or Path.home()))
        if folder:
            self._set_image_folder(Path(folder))

    # ── SRT Generator handlers ──────────────────────────────────────────────

    def _load_last_srt_output_dir(self) -> Path:
        raw = self._settings.value("srt/last_output_dir", "", type=str)
        if raw:
            candidate = Path(raw)
            if candidate.exists() and candidate.is_dir():
                return candidate
        return Path.home()

    def _remember_srt_output_dir(self, folder: Path) -> None:
        self._last_srt_output_dir = folder
        self._settings.setValue("srt/last_output_dir", str(folder))
        self._settings.sync()

    def _build_default_srt_output_name(self) -> str:
        if self.srt_input_path:
            return f"{self.srt_input_path.stem}_merged.srt"
        return "output.srt"

    def _prompt_srt_output_path(self) -> Path | None:
        initial_dir = self._last_srt_output_dir if self._last_srt_output_dir.exists() else Path.home()
        suggested_path = initial_dir / self._build_default_srt_output_name()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save output SRT",
            str(suggested_path),
            "SRT files (*.srt)",
        )
        if not path:
            return None
        selected_path = Path(path)
        final_path = selected_path
        if selected_path.suffix.lower() != ".srt":
            final_path = selected_path.with_suffix(".srt")
        self._remember_srt_output_dir(final_path.parent)
        return final_path

    def _show_srt_success_dialog(self, output_path: Path, entry_count: int) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle("SRT created")
        dialog.setText(f"Created successfully: {entry_count} entries.")
        dialog.setInformativeText(str(output_path))
        open_folder_btn = dialog.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        close_btn = dialog.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
        dialog.setDefaultButton(close_btn)
        dialog.exec()

        if dialog.clickedButton() == open_folder_btn:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path.parent)))
            if not opened:
                QMessageBox.warning(self, "Open folder failed", f"Could not open folder:\n{output_path.parent}")

    def _toggle_srt_content_mode(self) -> None:
        paste_mode = getattr(self, "srt_content_paste_radio", None) and self.srt_content_paste_radio.isChecked()
        if hasattr(self, "srt_content_file_widget"):
            self.srt_content_file_widget.setVisible(not paste_mode)
        if hasattr(self, "srt_content_paste_widget"):
            self.srt_content_paste_widget.setVisible(bool(paste_mode))
        if hasattr(self, "srt_content_count_label"):
            self.srt_content_count_label.setVisible(bool(paste_mode))
        if paste_mode:
            self._handle_srt_content_text_changed()

    @staticmethod
    def _extract_srt_content_lines(raw_text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in raw_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Remove existing numbering prefixes such as "1. ", "2) ", "3: ".
            match = re.match(r"^\d+\s*[.):-]\s*(.+)$", line)
            if match:
                line = match.group(1).strip()
            if line:
                lines.append(line)
        return lines

    def _set_srt_content_count(self, count: int) -> None:
        if not hasattr(self, "srt_content_count_label"):
            return
        noun = "line" if count == 1 else "lines"
        self.srt_content_count_label.setText(f"Total content {noun}: {count}")

    def _handle_srt_content_text_changed(self) -> None:
        if not hasattr(self, "srt_content_text"):
            return
        if self._updating_srt_paste_text:
            return
        lines = self._extract_srt_content_lines(self.srt_content_text.toPlainText())
        self._set_srt_content_count(len(lines))

    def _auto_number_srt_paste_content(self) -> None:
        if not hasattr(self, "srt_content_text"):
            return
        if self._updating_srt_paste_text:
            return

        lines = self._extract_srt_content_lines(self.srt_content_text.toPlainText())
        numbered_text = "\n".join(f"{idx}. {line}" for idx, line in enumerate(lines, 1))

        self._updating_srt_paste_text = True
        try:
            self.srt_content_text.setPlainText(numbered_text)
            cursor = self.srt_content_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.srt_content_text.setTextCursor(cursor)
        finally:
            self._updating_srt_paste_text = False

        self._set_srt_content_count(len(lines))

    def _handle_srt_input_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CapCut SRT file", str(Path.home()), "SRT files (*.srt);;All files (*)"
        )
        if path:
            self.srt_input_path = Path(path)
            if hasattr(self, "srt_input_path_edit"):
                self.srt_input_path_edit.setText(path)

    def _handle_content_file_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select content file", str(Path.home()),
            "Text files (*.txt *.rtf *.md);;All files (*)"
        )
        if path:
            self.srt_content_file_path = Path(path)
            if hasattr(self, "srt_content_path_edit"):
                self.srt_content_path_edit.setText(path)

    def _handle_srt_generate(self) -> None:
        # Validate SRT input
        if not self.srt_input_path:
            QMessageBox.information(self, "Missing input", "Please select a CapCut .srt file.")
            return

        # Get content text
        paste_mode = hasattr(self, "srt_content_paste_radio") and self.srt_content_paste_radio.isChecked()
        if paste_mode:
            content_text = self.srt_content_text.toPlainText().strip() if hasattr(self, "srt_content_text") else ""
            if not content_text:
                QMessageBox.information(self, "Missing content", "Please paste content into the text area.")
                return
        else:
            if not self.srt_content_file_path:
                QMessageBox.information(self, "Missing file", "Please select a content file.")
                return
            try:
                from autocapcut.services.srt_generator import _read_file_text
                content_text = _read_file_text(self.srt_content_file_path)
            except OSError as exc:
                QMessageBox.warning(self, "File read error", str(exc))
                return

        output_path = self._prompt_srt_output_path()
        if output_path is None:
            return

        progress = QProgressDialog("Preparing SRT generation...", None, 0, 100, self)
        progress.setWindowTitle("Generating SRT")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        def update_progress(percent: int, message: str) -> None:
            progress.setValue(max(0, min(100, percent)))
            progress.setLabelText(message)
            QApplication.processEvents()

        # Generate
        try:
            update_progress(4, "Preparing generation inputs...")
            self._generated_srt = generate_merged_srt(
                self.srt_input_path,
                content_text,
                progress_cb=update_progress,
            )
            update_progress(96, "Saving output SRT file...")
            output_path.write_text(self._generated_srt, encoding="utf-8")
            entry_count = self._generated_srt.count("\n\n") + 1
            if hasattr(self, "srt_result_label"):
                self.srt_result_label.setText(f"Generated and saved: {output_path}")
            update_progress(100, "SRT generated successfully.")
            progress.close()
            self._show_srt_success_dialog(output_path, entry_count)
        except (SRTGeneratorError, OSError) as exc:
            self._generated_srt = None
            if hasattr(self, "srt_result_label"):
                self.srt_result_label.setText(f"Error: {exc}")
            QMessageBox.warning(self, "SRT generation failed", str(exc))
        finally:
            progress.close()
            progress.deleteLater()

    def _handle_remove_background(self) -> None:
        if not self.image_folder:
            QMessageBox.information(
                self,
                "No image folder",
                "Select an image folder before removing backgrounds.",
            )
            return

        # Get selected mode from ComboBox
        selected_mode = self.bg_removal_mode.currentData()  # Returns "auto", "fast", or "ai"

        progress = QProgressDialog("Preparing images…", None, 0, 0, self)
        progress.setWindowTitle("Removing Background")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        def update_progress(done: int, total: int, current: Path | None) -> None:
            if total and progress.maximum() != total:
                progress.setMaximum(total)
            progress.setValue(done)
            if current is not None:
                progress.setLabelText(f"Processing {current.name} ({done}/{total})")
            else:
                label_total = f"{total} image(s)" if total else "images"
                progress.setLabelText(f"Preparing {label_total}…")
            QApplication.processEvents()

        try:
            summary = remove_white_background(
                self.image_folder,
                delete_original=True,
                mode=selected_mode,
                progress_callback=update_progress,
            )
        except BackgroundRemovalError as exc:
            progress.close()
            progress = None
            QMessageBox.warning(self, "Background removal failed", str(exc))
            return
        finally:
            if progress is not None:
                progress.close()

        message = [
            f"Processed {summary.processed} image(s).",
            f"Background removed: {summary.removed_background}",
            f"Mode used: {summary.mode_used}",
        ]
        if summary.kept:
            message.append(f"Unchanged: {summary.kept}")
        if summary.converted_to_png:
            message.append(f"Converted to PNG: {summary.converted_to_png}")
        if summary.errors:
            message.append("Errors:")
            message.extend(summary.errors)

        if summary.errors:
            QMessageBox.warning(
                self,
                "Background removal complete with issues",
                "\n".join(message),
            )
        else:
            QMessageBox.information(
                self,
                "Background removal complete",
                "\n".join(message),
            )

    def _toggle_rename_end_state(self, checked: bool) -> None:
        if hasattr(self, "rename_end_spin"):
            self.rename_end_spin.setEnabled(not checked)
        self._update_rename_preview()

    def _update_rename_preview(self) -> None:
        if not hasattr(self, "rename_hint_label"):
            return
        start = self.rename_start_spin.value() if hasattr(self, "rename_start_spin") else 1
        end_value = (
            None
            if not hasattr(self, "rename_until_end") or self.rename_until_end.isChecked()
            else self.rename_end_spin.value()
        )
        preview_max = end_value if end_value is not None else start + 1
        width = max(3, len(str(preview_max)))
        second = min(start + 1, preview_max)
        if second == start:
            text = f"Format: image_{start:0{width}d}"
        else:
            text = f"Format: image_{start:0{width}d}, image_{second:0{width}d}…"
        self.rename_hint_label.setText(text)

    def on_auto_render(self) -> None:
        """Handle auto-render button click."""
        if not self.image_folder: # Assuming image_folder is used as project_folder_path
            QMessageBox.warning(self, "Error", "Please select a project folder first.")
            return

        # [NEW] Check for Screen Recording Permission
        if not check_screen_recording_permission():
            response = QMessageBox.warning(
                self,
                "Permission Required",
                "Auto Render needs 'Screen Recording' permission to detect when CapCut finishes exporting.\n\n"
                "Please enable it for your Terminal/IDE in System Settings, then restart the app.",
                QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
            )
            
            # Change "Open" button text to "Open Settings" if possible, 
            # but StandardButton usually has fixed text.
            # We treat "Open" (first button) as confirm.
            
            if response == QMessageBox.StandardButton.Open:
                open_screen_recording_settings()
            
            return

        # projects = self.project_table.get_projects() # This line assumes self.project_table is a ProjectTable instance, but it's QTableWidget
        # For now, we'll use the existing self.projects list and filter selected ones.
        projects = [p for p in self.projects if p.is_selected]
        if not projects:
            QMessageBox.information(self, "No Project Selected", "Select at least one project to render.")
            return

        self._set_job_controls_state(True)
        self._update_status_label("Starting auto render…")

        self._render_worker = RenderWorker(projects)
        self._render_worker.status_updated.connect(self._refresh_status_cells)
        self._render_worker.job_finished.connect(self._handle_render_finished)
        self._render_worker.progress_updated.connect(self._update_render_progress)
        self._render_worker.log_message.connect(self._append_render_log)
        self._render_worker.start()

        self._show_render_log_dialog()

    def _handle_bulk_rename_range(self) -> None:
        if not self.image_folder:
            QMessageBox.information(self, "No folder", "Select a folder before renaming.")
            return

        start = self.rename_start_spin.value()
        end_value: int | None = None
        if hasattr(self, "rename_until_end") and not self.rename_until_end.isChecked():
            end_value = self.rename_end_spin.value()
            if end_value < start:
                QMessageBox.warning(self, "Invalid range", "End number must be greater than or equal to start number.")
                return

        self._set_job_controls_state(True)
        try:
            summary = bulk_rename(self.image_folder, "image", start=start, end=end_value)
        except BulkRenameError as exc:
            QMessageBox.warning(self, "Bulk rename failed", str(exc))
            return
        finally:
            self._set_job_controls_state(False)

        final_end = summary.end if summary.end is not None else summary.start + summary.total_files - 1
        message_lines = [
            f"Folder: {summary.folder}",
            f"Renamed: {summary.renamed}",
            f"Skipped (already in place): {summary.skipped}",
            f"Range used: {summary.start:0{summary.width}d} \u2192 {final_end:0{summary.width}d}",
        ]
        QMessageBox.information(self, "Bulk rename", "\n".join(message_lines))

    # region actions
    def _handle_animation_insert(self) -> None:
        selected_projects = [p for p in self.projects if p.is_selected]
        if not selected_projects:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before inserting animations.")
            return

        preset_keys = self._selected_animation_keys()
        if not preset_keys:
            QMessageBox.information(self, "No Preset Selected", "Choose at least one animation preset from the list.")
            return

        self._set_job_controls_state(True)
        self._update_status_label("Applying animations…")
        duration_seconds = max(0.0, self.animation_duration_spin.value())
        default_duration = ANIMATION_PRESETS[preset_keys[0]].default_duration_us / 1_000_000

        succeeded = 0
        failed: list[str] = []
        for project in selected_projects:
            try:
                summary = apply_animations(
                    project,
                    preset_keys=preset_keys,
                    duration_seconds=duration_seconds,
                )
                duration_display = duration_seconds if duration_seconds > 0 else default_duration
                project.status = ProjectStatus.done
                project.notes = (
                    f"Animations {'/'.join(preset_keys)} applied to {summary.segments_updated} segment(s)"
                    f" · duration {duration_display:.2f}s"
                )
                if summary.removed_existing:
                    project.notes += f" · replaced {summary.removed_existing} existing"
                succeeded += 1
            except AnimationError as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                failed.append(f"{project.name}: {exc}")
        self._refresh_status_cells()
        self._set_job_controls_state(False)
        self._update_status_label()

        summary_lines = [
            f"Applied animations to {succeeded} project(s).",
        ]
        if failed:
            summary_lines.append("Failed:")
            summary_lines.extend(failed)
        QMessageBox.information(self, "Animations complete", "\n".join(summary_lines))

    def _handle_animation_remove(self) -> None:
        selected_projects = [p for p in self.projects if p.is_selected]
        if not selected_projects:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before removing animations.")
            return

        preset_keys = self._selected_animation_keys()

        self._set_job_controls_state(True)
        self._update_status_label("Removing animations…")
        succeeded = 0
        failed: list[str] = []
        for project in selected_projects:
            try:
                summary = remove_animations(project, preset_keys=preset_keys)
                project.status = ProjectStatus.done
                project.notes = (
                    f"Removed {summary.removed_entries} animation entrie(s)"
                    f" across {summary.affected_segments} segment(s)"
                )
                succeeded += 1
            except AnimationError as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                failed.append(f"{project.name}: {exc}")
        self._refresh_status_cells()
        self._set_job_controls_state(False)
        self._update_status_label()

        summary_lines = [
            f"Removed animations in {succeeded} project(s).",
        ]
        if failed:
            summary_lines.append("Failed:")
            summary_lines.extend(failed)
        QMessageBox.information(self, "Animations removed", "\n".join(summary_lines))

    def _handle_effect_insert(self) -> None:
        selected_projects = [p for p in self.projects if p.is_selected]
        if not selected_projects:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before inserting effects.")
            return

        preset_keys = self._selected_effect_keys()
        if not preset_keys:
            QMessageBox.information(self, "No Preset Selected", "Choose at least one effect preset from the list.")
            return

        self._set_job_controls_state(True)
        self._update_status_label("Applying effects…")
        succeeded = 0
        failed: list[str] = []
        for project in selected_projects:
            applied_any = False
            notes: List[str] = []
            for key in preset_keys:
                try:
                    summary = apply_effect(project, preset_key=key)
                    applied_any = True
                    note = f"Effect {key} applied"
                    if summary.removed_existing:
                        note += f" · replaced {summary.removed_existing}"
                    notes.append(note)
                    project.status = ProjectStatus.done
                except EffectError as exc:
                    project.status = ProjectStatus.failed
                    notes.append(f"{key}: {exc}")
                    failed.append(f"{project.name}: {exc}")
            project.notes = " | ".join(notes) if notes else ("No effect applied" if not applied_any else "Effect applied")
            if applied_any:
                succeeded += 1
        self._refresh_status_cells()
        self._set_job_controls_state(False)
        self._update_status_label()

        summary_lines = [f"Applied effects to {succeeded} project(s)."]
        if failed:
            summary_lines.append("Failed:")
            summary_lines.extend(failed)
        QMessageBox.information(self, "Effects complete", "\n".join(summary_lines))

    def _handle_effect_remove(self) -> None:
        selected_projects = [p for p in self.projects if p.is_selected]
        if not selected_projects:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before removing effects.")
            return

        preset_keys = self._selected_effect_keys()

        self._set_job_controls_state(True)
        self._update_status_label("Removing effects…")
        succeeded = 0
        failed: list[str] = []
        for project in selected_projects:
            try:
                summary = remove_effects(project, preset_keys=preset_keys)
                project.status = ProjectStatus.done
                project.notes = (
                    f"Removed {summary.removed_entries} effect(s)"
                    f" · removed segments: {summary.removed_segments}"
                )
                succeeded += 1
            except EffectError as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                failed.append(f"{project.name}: {exc}")
        self._refresh_status_cells()
        self._set_job_controls_state(False)
        self._update_status_label()

        summary_lines = [
            f"Removed effects in {succeeded} project(s).",
        ]
        if failed:
            summary_lines.append("Failed:")
            summary_lines.extend(failed)
        QMessageBox.information(self, "Effects removed", "\n".join(summary_lines))

    def _handle_sync_clicked(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(
                self,
                "No Project Selected",
                "Please tick at least one project before syncing audio.",
            )
            return

        if self._worker and self._worker.isRunning():
            QMessageBox.warning(
                self,
                "Sync in progress",
                "Another synchronisation is already running.",
            )
            return

        for project in selected:
            project.status = ProjectStatus.pending
            project.notes = ""
        self._refresh_status_cells()

        self._last_job_projects = list(selected)
        self._worker = SyncWorker(
            selected,
            sync_project_audio,
            _format_summary,
            (SyncAudioError,)
        )
        self._worker.status_updated.connect(self._refresh_status_cells)
        self._worker.job_finished.connect(self._on_worker_finished)
        self._set_job_controls_state(True)
        self._update_status_label(f"Syncing audio… ({len(selected)} project(s))")
        self._worker.start()

    def _handle_sync_images_clicked(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(
                self,
                "No Project Selected",
                "Please tick at least one project before syncing images.",
            )
            return

        if self._worker and self._worker.isRunning():
            QMessageBox.warning(
                self,
                "Sync in progress",
                "Another synchronisation is already running.",
            )
            return

        for project in selected:
            project.status = ProjectStatus.pending
            project.notes = ""
        self._refresh_status_cells()

        self._last_job_projects = list(selected)
        self._worker = SyncWorker(
            selected,
            sync_project_images,
            _format_image_summary,
            (SyncImageError,)
        )
        self._worker.status_updated.connect(self._refresh_status_cells)
        self._worker.job_finished.connect(self._on_worker_finished)
        self._set_job_controls_state(True)
        self._update_status_label(f"Syncing images… ({len(selected)} project(s))")
        self._worker.start()

    def _handle_sync_captions_clicked(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(
                self,
                "No Project Selected",
                "Please tick at least one project before syncing captions.",
            )
            return

        if self._worker and self._worker.isRunning():
            QMessageBox.warning(
                self,
                "Sync in progress",
                "Another synchronisation is already running.",
            )
            return

        for project in selected:
            project.status = ProjectStatus.pending
            project.notes = ""
        self._refresh_status_cells()

        self._last_job_projects = list(selected)
        self._worker = SyncWorker(
            selected,
            sync_project_captions,
            _format_caption_summary,
            (SyncCaptionError,)
        )
        self._worker.status_updated.connect(self._refresh_status_cells)
        self._worker.job_finished.connect(self._on_worker_finished)
        self._set_job_controls_state(True)
        self._update_status_label(f"Syncing captions… ({len(selected)} project(s))")
        self._worker.start()

    def _handle_auto_render_clicked(self) -> None:
        """Handle Auto Render button click."""
        selected = self._selected_projects_in_order()
        if not selected:
            QMessageBox.information(
                self,
                "No Project Selected",
                "Please tick at least one project before auto-rendering.",
            )
            return

        if self._render_worker and self._render_worker.isRunning():
            QMessageBox.warning(
                self,
                "Render in progress",
                "Another render job is already running.",
            )
            return

        if self._worker and self._worker.isRunning():
            QMessageBox.warning(
                self,
                "Sync in progress",
                "Please wait for the current sync operation to complete.",
            )
            return

        # Confirm with user
        reply = QMessageBox.question(
            self,
            "Start Auto Render",
            f"This will render {len(selected)} project(s) using CapCut.\n\n"
            "Make sure CapCut is open and you're on the Projects dashboard.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Reset project statuses
        for project in selected:
            project.status = ProjectStatus.pending
            project.notes = ""
        self._refresh_status_cells()

        # Start render worker
        self._last_job_projects = list(selected)
        self._render_worker = RenderWorker(selected)
        self._render_worker.status_updated.connect(self._refresh_status_cells)
        self._render_worker.job_finished.connect(self._on_render_finished)
        self._render_worker.log_message.connect(self._append_render_log)
        self._render_worker.progress_updated.connect(self._update_render_progress)
        self._set_job_controls_state(True)
        self._update_status_label(f"Auto Rendering… ({len(selected)} project(s))")
        self._show_render_log_dialog(len(selected))
        self._render_worker.start()

    def _on_render_finished(self, completed: int, total: int) -> None:
        """Handle render worker completion."""
        self._status_message_override = None
        self._refresh_status_cells()
        self._set_job_controls_state(False)
        
        # Wait for thread to fully finish before cleanup
        if self._render_worker is not None:
            try:
                self._render_worker.wait(2000)  # Wait up to 2 seconds
            except Exception:
                pass
            self._render_worker = None
        
        self._update_status_label()

        # Show completion notification
        if completed == total:
            QMessageBox.information(
                self,
                "Auto Render Complete",
                f"✅ Successfully rendered all {completed} project(s)!",
            )
        else:
            failed = total - completed
            QMessageBox.warning(
                self,
                "Auto Render Complete",
                f"Rendered {completed}/{total} project(s).\n\n"
                f"⚠️ {failed} project(s) failed. Check the Notes column for details.",
            )
        if self._render_log_text is not None:
            self._append_render_log(f"Render summary: completed {completed}/{total}.")

    def _handle_stop_clicked(self) -> None:
        if self._render_worker and self._render_worker.isRunning():
            self._render_worker.cancel()
            self._update_status_label("Stopping render…")
            return
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._update_status_label("Stopping sync…")
            return
        QMessageBox.information(self, "No Job Running", "There is no active job to stop.")

    def _selected_transition_keys(self) -> List[str]:
        keys: List[str] = []
        if not hasattr(self, "transition_list"):
            return keys
        for index in range(self.transition_list.count()):
            item = self.transition_list.item(index)
            if item.isSelected():
                key = item.data(Qt.ItemDataRole.UserRole)
                if key:
                    keys.append(str(key))
        logger.debug("Selected transition keys: %s", keys)
        return keys

    def _on_worker_finished(self) -> None:  # pragma: no cover - GUI callback
        self._status_message_override = None
        self._refresh_status_cells()
        self._set_job_controls_state(False)
        self._worker = None
        summary = self._summarise_last_job()
        if summary:
            QMessageBox.information(self, "Sync complete", summary)
        self._update_status_label()

    def _handle_transition_apply(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before applying transitions.")
            return
        preset_keys = self._selected_transition_keys()
        if not preset_keys:
            QMessageBox.information(
                self,
                "No Transition Selected",
                "Choose at least one transition preset from the list."
            )
            return
        unsupported = [key for key in preset_keys if key != "black_fade"]
        if unsupported:
            QMessageBox.warning(
                self,
                "Not yet supported",
                "Only the Black Fade transition is supported in this build."
            )
        duration = max(self.transition_duration_spin.value(), 0.1)
        logger.info(
            "Applying transitions: keys=%s duration=%.2fs projects=%d",
            preset_keys,
            duration,
            len(selected),
        )
        applied = 0
        failures: list[str] = []
        for project in selected:
            try:
                summary = apply_transition(
                    project,
                    preset_key='black_fade',
                    duration_seconds=duration,
                )
                project.status = ProjectStatus.done
                project.notes = (
                    f"Transitions: {summary.applied_transitions} applied "
                    f"(Black Fade · {duration:.2f}s)"
                )
                applied += summary.applied_transitions
            except TransitionError as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                logger.warning("Transition error for %s: %s", project.name, exc)
                failures.append(project.name)
        self._refresh_status_cells()
        result_lines = [f"Applied Black Fade ({duration:.2f}s) to {len(selected)} project(s)."]
        result_lines.append(f"Total transitions inserted: {applied}")
        if failures:
            result_lines.append("Failed projects: " + ", ".join(failures))
        QMessageBox.information(self, "Transitions complete", "\n".join(result_lines))

    def _ensure_render_log_dialog(self) -> None:
        if self._render_log_dialog is not None:
            return
        dialog = QDialog(self)
        dialog.setObjectName("renderLogDialog")
        dialog.setWindowTitle("Auto Render Progress")
        dialog.setModal(False)
        dialog.resize(640, 420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._render_progress_label = QLabel("Progress: 0/0")
        self._render_progress_label.setObjectName("renderProgressLabel")
        layout.addWidget(self._render_progress_label)

        self._render_progress_bar = QProgressBar()
        self._render_progress_bar.setObjectName("renderProgressBar")
        self._render_progress_bar.setRange(0, 0)
        self._render_progress_bar.setValue(0)
        layout.addWidget(self._render_progress_bar)

        self._render_log_text = QTextEdit()
        self._render_log_text.setObjectName("renderLogText")
        self._render_log_text.setReadOnly(True)
        layout.addWidget(self._render_log_text, 1)

        close_button = QPushButton("Close")
        close_button.setProperty("variant", "secondary")
        close_button.setObjectName("renderCloseButton")
        close_button.clicked.connect(dialog.hide)
        layout.addWidget(close_button)

        self._render_log_dialog = dialog

    def _show_render_log_dialog(self, total: int) -> None:
        self._ensure_render_log_dialog()
        if self._render_log_dialog is None:
            return
        if self._render_log_text is not None:
            self._render_log_text.clear()
        if self._render_progress_bar is not None:
            self._render_progress_bar.setRange(0, max(total, 1))
            self._render_progress_bar.setValue(0)
        if self._render_progress_label is not None:
            self._render_progress_label.setText(f"Progress: 0/{total}")
        self._render_log_dialog.show()
        self._render_log_dialog.raise_()
        self._render_log_dialog.activateWindow()

    def _append_render_log(self, message: str) -> None:
        if self._render_log_text is None:
            self._ensure_render_log_dialog()
        if self._render_log_text is None:
            return
        self._render_log_text.append(message)

    def _update_render_progress(self, completed: int, total: int) -> None:
        if self._render_progress_bar is None or self._render_progress_label is None:
            self._ensure_render_log_dialog()
        if self._render_progress_bar is None or self._render_progress_label is None:
            return
        self._render_progress_bar.setRange(0, max(total, 1))
        self._render_progress_bar.setValue(min(completed, total))
        self._render_progress_label.setText(f"Progress: {completed}/{total}")

    def _handle_transition_clear(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before removing transitions.")
            return
        cleared_total = 0
        failures: list[str] = []
        for project in selected:
            try:
                removed = clear_transitions(project)
                project.status = ProjectStatus.done
                project.notes = f"Removed {removed} transitions"
                cleared_total += removed
            except TransitionError as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                logger.warning("Transition clear error for %s: %s", project.name, exc)
                failures.append(project.name)
        self._refresh_status_cells()
        result = [f"Cleared transitions from {len(selected)} project(s)."]
        result.append(f"Total transitions removed: {cleared_total}")
        if failures:
            result.append("Failed projects: " + ", ".join(failures))
        QMessageBox.information(self, "Transitions cleared", "\n".join(result))

    def _summarise_last_job(self) -> str | None:
        if not self._last_job_projects:
            return None
        success = sum(1 for p in self._last_job_projects if p.status == ProjectStatus.done)
        failed = sum(1 for p in self._last_job_projects if p.status == ProjectStatus.failed)
        total = len(self._last_job_projects)
        self._last_job_projects = []
        parts = [f"Processed {total} project(s).", f"Success: {success}"]
        if failed:
            parts.append(f"Failed: {failed}")
            parts.append("Check notes for details.")
        else:
            parts.append("All projects synced successfully.")
        return "\n".join(parts)

    # endregion
