"""Sync audio-only GUI for AutoCapcut prototype."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from loguru import logger
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
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
                automation.close_project()
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
        container = QWidget(self)
        container.setObjectName("rootWidget")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._build_tool_panel(), 1)
        layout.addWidget(self._build_project_panel(), 2)

        container.setLayout(layout)
        self.setCentralWidget(container)
        self._apply_styles()
        self._set_job_controls_state(False)

    def _build_sidebar(self) -> QFrame:
        """Build the dark left navigation sidebar."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(184)
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
            ("animation",  "🎞  Animation"),
            ("effect",     "✨  Effect"),
            ("transition", "⇌  Transition"),
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
            ("remove_bg",  "🖼  Remove BG"),
            ("rename",     "✏  Rename"),
            ("srt",        "📄  Tạo SRT"),
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

        # ── Asset Panel (shared workspace folder) ────────────────────────────
        sep = QFrame()
        sep.setObjectName("assetSep")
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)
        layout.addWidget(self._build_asset_panel())

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
            "animation":  "Animation",
            "effect":     "Effect",
            "transition": "Transition",
            "remove_bg":  "Remove Background",
            "rename":     "Rename Files",
            "srt":        "Tạo SRT",
        }
        if self._tool_stack is not None:
            self._tool_stack.setCurrentIndex(index_map.get(key, 0))
        if self._tool_title_label is not None:
            self._tool_title_label.setText(titles.get(key, ""))

    def _build_asset_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("assetPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        label = QLabel("Image Preparation")
        label.setObjectName("sectionLabel")
        grid.addWidget(label, 0, 0, 1, 3)

        image_caption = QLabel("Image Folder")
        image_caption.setObjectName("assetCaption")
        grid.addWidget(image_caption, 1, 0, 1, 3)

        self.image_path_edit = QLineEdit()
        self.image_path_edit.setPlaceholderText("Choose image folder…")
        self.image_path_edit.setReadOnly(True)
        self.image_path_edit.setObjectName("assetPath")
        grid.addWidget(self.image_path_edit, 2, 0, 1, 2)

        image_button = QPushButton("Browse Images")
        image_button.setProperty("variant", "secondary")
        image_button.setObjectName("assetBrowseButton")
        image_button.clicked.connect(self._handle_select_image_folder)
        grid.addWidget(image_button, 2, 2, 1, 1)

        self.bulk_rename_btn = QPushButton("Bulk Rename")
        self.bulk_rename_btn.setProperty("variant", "primary")
        self.bulk_rename_btn.clicked.connect(self._handle_bulk_rename)
        grid.addWidget(self.bulk_rename_btn, 3, 0, 1, 3)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 140)
        layout.addLayout(grid)

        return frame

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
        self.insert_animation_button.setProperty("variant", "success")
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

        self.rename_until_end = QCheckBox("Đến hết số file trong thư mục")
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
        srt_label = QLabel("File SRT từ CapCut")
        srt_label.setObjectName("sectionLabel")
        layout.addWidget(srt_label)

        srt_row = QHBoxLayout()
        self.srt_input_path_edit = QLineEdit()
        self.srt_input_path_edit.setPlaceholderText("Chọn file .srt…")
        self.srt_input_path_edit.setReadOnly(True)
        self.srt_input_path_edit.setObjectName("assetPath")
        srt_row.addWidget(self.srt_input_path_edit, 1)

        srt_browse_btn = QPushButton("Browse")
        srt_browse_btn.setProperty("variant", "secondary")
        srt_browse_btn.clicked.connect(self._handle_srt_input_browse)
        srt_row.addWidget(srt_browse_btn)
        layout.addLayout(srt_row)

        # ── Content Input ──────────────────────────────────────────
        content_label = QLabel("Content gốc")
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
        self.srt_content_path_edit.setPlaceholderText("Chọn file content (.txt, .rtf…)")
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
        self.srt_content_text = QTextEdit()
        self.srt_content_text.setPlaceholderText(
            "Paste nội dung vào đây (mỗi dòng = 1 câu content)\n"
            "Ví dụ:\n"
            "1. Have you noticed your hands tingling…\n"
            "2. or your legs feeling weaker…"
        )
        self.srt_content_text.setObjectName("contentPasteArea")
        paste_layout.addWidget(self.srt_content_text)

        layout.addWidget(self.srt_content_file_widget)
        layout.addWidget(self.srt_content_paste_widget, 1)

        # Connect radios → toggle visibility
        self.srt_content_file_radio.toggled.connect(self._toggle_srt_content_mode)
        self.srt_content_paste_radio.toggled.connect(self._toggle_srt_content_mode)
        self._toggle_srt_content_mode()

        # ── Generate button ────────────────────────────────────────
        self.srt_generate_btn = QPushButton("Tạo file SRT")
        self.srt_generate_btn.setProperty("variant", "success")
        self.srt_generate_btn.clicked.connect(self._handle_srt_generate)
        layout.addWidget(self.srt_generate_btn)

        # ── Result label + Save button ─────────────────────────────
        self.srt_result_label = QLabel("")
        self.srt_result_label.setObjectName("hintLabel")
        self.srt_result_label.setWordWrap(True)
        layout.addWidget(self.srt_result_label)

        self.srt_save_btn = QPushButton("Lưu file SRT output")
        self.srt_save_btn.setProperty("variant", "primary")
        self.srt_save_btn.setEnabled(False)
        self.srt_save_btn.clicked.connect(self._handle_srt_save)
        layout.addWidget(self.srt_save_btn)

        return tab

    def _build_project_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("projectPanel")
        panel.setMinimumWidth(580)
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
        self.project_table.verticalHeader().setDefaultSectionSize(36)
        self.project_table.setHorizontalHeaderLabels(
            ["Select", "Project", "Source", "Status", "Notes"]
        )
        header_view = self.project_table.horizontalHeader()
        header_view.setSectionResizeMode(self.columns.select, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.name, header_view.ResizeMode.Stretch)
        header_view.setSectionResizeMode(self.columns.source, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.status, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.notes, header_view.ResizeMode.Stretch)
        panel_layout.addWidget(self.project_table, 1)

        self.status_label = QLabel("Loading projects…")
        self.status_label.setObjectName("statusLabel")
        panel_layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        self.reload_button = QPushButton("Reload Projects")
        self.reload_button.setProperty("variant", "secondary")
        self.reload_button.clicked.connect(self.refresh_projects)

        self.sync_audio_button = QPushButton("Sync Audio")
        self.sync_audio_button.setProperty("variant", "success")
        self.sync_audio_button.clicked.connect(self._handle_sync_clicked)

        self.sync_images_button = QPushButton("Sync Images")
        self.sync_images_button.setProperty("variant", "primary")
        self.sync_images_button.clicked.connect(self._handle_sync_images_clicked)

        self.sync_caption_button = QPushButton("Sync Caption")
        self.sync_caption_button.setProperty("variant", "primary")
        self.sync_caption_button.clicked.connect(self._handle_sync_captions_clicked)

        self.auto_render_button = QPushButton("Auto Render")
        self.auto_render_button.setProperty("variant", "success")
        self.auto_render_button.clicked.connect(self._handle_auto_render_clicked)

        button_row.addWidget(self.reload_button)
        button_row.addWidget(self.sync_audio_button)
        button_row.addWidget(self.sync_images_button)
        button_row.addWidget(self.sync_caption_button)
        button_row.addWidget(self.auto_render_button)
        panel_layout.addLayout(button_row)

        return panel

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            /* ── Root ────────────────────────────────────────────────────────── */
            #rootWidget {
                background: #e8eaf0;
            }

            /* ── Sidebar ─────────────────────────────────────────────────────── */
            #sidebar {
                background: #1e293b;
            }
            #sidebarHeader {
                background: #0f172a;
            }
            #appName {
                font-size: 16px;
                font-weight: 700;
                color: #f1f5f9;
                letter-spacing: -0.2px;
            }
            #appTagline {
                font-size: 10px;
                color: #475569;
            }
            #navArea {
                background: transparent;
            }
            QLabel#navSection {
                font-size: 9px;
                font-weight: 700;
                color: #475569;
                letter-spacing: 0.12em;
                padding-left: 4px;
            }
            QPushButton#navBtn {
                background: transparent;
                color: #94a3b8;
                border: none;
                border-radius: 8px;
                padding: 9px 12px;
                text-align: left;
                font-size: 13px;
                font-weight: 500;
                min-height: 36px;
            }
            QPushButton#navBtn:hover:!checked {
                background: rgba(255, 255, 255, 0.06);
                color: #e2e8f0;
            }
            QPushButton#navBtn:checked {
                background: #6366f1;
                color: #ffffff;
            }

            /* ── Tool Panel ──────────────────────────────────────────────────── */
            #toolPanel {
                background: #ffffff;
                border-right: 1px solid #e5e7eb;
            }
            #toolHeader {
                background: #ffffff;
                border-bottom: 1px solid #f1f5f9;
            }
            #toolTitle {
                font-size: 20px;
                font-weight: 700;
                color: #0f172a;
            }
            #toolStack {
                background: #ffffff;
            }
            QFrame#assetSep {
                border: none;
                border-top: 1px solid #e5e7eb;
                max-height: 1px;
                min-height: 1px;
            }
            #assetPanel {
                background: #f8fafc;
            }

            /* ── Project Panel ───────────────────────────────────────────────── */
            #projectPanel {
                background: #ffffff;
            }
            #projectTitle {
                font-size: 20px;
                font-weight: 700;
                color: #0f172a;
            }

            /* ── Buttons ─────────────────────────────────────────────────────── */
            QPushButton {
                min-height: 32px;
                padding: 4px 12px;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton[variant="primary"] {
                background: #6366f1;
                color: white;
                border: none;
            }
            QPushButton[variant="primary"]:hover:!disabled {
                background: #4f46e5;
            }
            QPushButton[variant="primary"]:disabled {
                background: #c7d2fe;
                color: #e0e7ff;
            }
            QPushButton[variant="success"] {
                background: #10b981;
                color: white;
                border: none;
            }
            QPushButton[variant="success"]:hover:!disabled {
                background: #059669;
            }
            QPushButton[variant="secondary"] {
                background: transparent;
                color: #374151;
                border: 1px solid #d1d5db;
            }
            QPushButton[variant="secondary"]:hover:!disabled {
                border-color: #9ca3af;
                color: #111827;
            }
            QPushButton[variant="pill"] {
                background: #e5e7eb;
                color: #1f2937;
                border-radius: 10px;
                border: none;
                padding: 4px 12px;
                min-width: 80px;
            }
            QPushButton[variant="pill"]:checked {
                background: #6366f1;
                color: white;
            }
            QPushButton[variant="danger"] {
                background: #ef4444;
                color: white;
                border: none;
            }
            QPushButton[variant="danger"]:hover:!disabled {
                background: #dc2626;
            }
            QPushButton[variant="danger"]:disabled {
                background: #fca5a5;
                color: #fee2e2;
            }

            /* ── Inputs ──────────────────────────────────────────────────────── */
            QLineEdit#searchField {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                background: #ffffff;
                font-size: 13px;
            }
            QLineEdit#searchField:focus {
                border-color: #6366f1;
            }
            QLineEdit#assetPath {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                background: #f9fafb;
                font-size: 13px;
            }
            QLineEdit#assetPath:focus {
                border-color: #6366f1;
                background: #ffffff;
            }

            /* ── Labels ──────────────────────────────────────────────────────── */
            QLabel#sectionLabel {
                font-size: 11px;
                font-weight: 600;
                color: #64748b;
                letter-spacing: 0.06em;
            }
            QLabel#assetCaption {
                font-size: 11px;
                font-weight: 600;
                color: #475569;
                letter-spacing: 0.04em;
            }
            QLabel#hintLabel {
                font-size: 12px;
                color: #6b7280;
            }
            #statusLabel {
                font-size: 12px;
                color: #4b5563;
                padding-left: 2px;
            }

            /* ── Duration Frame ──────────────────────────────────────────────── */
            #durationFrame {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
            }

            /* ── Preset Lists ────────────────────────────────────────────────── */
            QListWidget#presetList {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: #ffffff;
                outline: none;
                font-size: 13px;
            }
            QListWidget#presetList::item {
                padding: 6px 10px;
                border-radius: 4px;
            }
            QListWidget#presetList::item:selected {
                background: #e0e7ff;
                color: #3730a3;
            }
            QListWidget#presetList::item:hover:!selected {
                background: #f5f3ff;
            }

            /* ── Project Table ───────────────────────────────────────────────── */
            #projectTable {
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                background: #ffffff;
                gridline-color: #f1f5f9;
            }
            #projectTable::item {
                padding: 6px;
                font-size: 13px;
            }
            QTableWidget QTableCornerButton::section {
                background: #f8fafc;
                border: none;
            }
            QHeaderView::section {
                background: #f8fafc;
                color: #374151;
                font-weight: 600;
                font-size: 12px;
                border: none;
                border-right: 1px solid #e5e7eb;
                border-bottom: 1px solid #e5e7eb;
                padding: 8px;
            }
            QHeaderView::section:last {
                border-right: none;
            }

            /* ── Misc ────────────────────────────────────────────────────────── */
            QPushButton#assetBrowseButton {
                min-width: 116px;
            }
            QMessageBox {
                background: #ffffff;
            }
            """
        )

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
            getattr(self, "bulk_rename_btn", None),
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

    def refresh_projects(self) -> None:
        previously_selected = {p.name for p in self.projects if p.is_selected}
        self.projects = discover_projects()
        for project in self.projects:
            project.is_selected = project.name in previously_selected
            self._refresh_project_metadata(project)
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
            checkbox.setChecked(project.is_selected)
            checkbox.stateChanged.connect(self._make_checkbox_handler(project))
            checkbox_container = QWidget()
            container_layout = QHBoxLayout(checkbox_container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            container_layout.addWidget(checkbox)
            self.project_table.setCellWidget(row, self.columns.select, checkbox_container)

            name_item = QTableWidgetItem(project.name)
            source_item = QTableWidgetItem(project.source.label())
            status_item = QTableWidgetItem(project.status.label())
            notes_item = QTableWidgetItem(project.notes)

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
            project.is_selected = Qt.CheckState(state) == Qt.CheckState.Checked
            logger.debug("Project %s selection=%s", project.name, project.is_selected)
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
            if notes_item is not None:
                notes_item.setText(project.notes)
        if self._status_message_override is None:
            self._update_status_label()
        else:
            self._update_ffmpeg_button_state()

    # endregion

    def _set_image_folder(self, folder: Path) -> None:
        self.image_folder = folder
        if getattr(self, "image_path_edit", None):
            self.image_path_edit.setText(str(folder))
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

    def _toggle_srt_content_mode(self) -> None:
        paste_mode = getattr(self, "srt_content_paste_radio", None) and self.srt_content_paste_radio.isChecked()
        if hasattr(self, "srt_content_file_widget"):
            self.srt_content_file_widget.setVisible(not paste_mode)
        if hasattr(self, "srt_content_paste_widget"):
            self.srt_content_paste_widget.setVisible(bool(paste_mode))

    def _handle_srt_input_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file SRT từ CapCut", str(Path.home()), "SRT files (*.srt);;All files (*)"
        )
        if path:
            self.srt_input_path = Path(path)
            if hasattr(self, "srt_input_path_edit"):
                self.srt_input_path_edit.setText(path)

    def _handle_content_file_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file content", str(Path.home()),
            "Text files (*.txt *.rtf *.md);;All files (*)"
        )
        if path:
            self.srt_content_file_path = Path(path)
            if hasattr(self, "srt_content_path_edit"):
                self.srt_content_path_edit.setText(path)

    def _handle_srt_generate(self) -> None:
        # Validate SRT input
        if not self.srt_input_path:
            QMessageBox.information(self, "Thiếu input", "Vui lòng chọn file .srt từ CapCut.")
            return

        # Get content text
        paste_mode = hasattr(self, "srt_content_paste_radio") and self.srt_content_paste_radio.isChecked()
        if paste_mode:
            content_text = self.srt_content_text.toPlainText().strip() if hasattr(self, "srt_content_text") else ""
            if not content_text:
                QMessageBox.information(self, "Thiếu content", "Vui lòng paste nội dung vào ô text.")
                return
        else:
            if not self.srt_content_file_path:
                QMessageBox.information(self, "Thiếu file", "Vui lòng chọn file content.")
                return
            try:
                from autocapcut.services.srt_generator import _read_file_text
                content_text = _read_file_text(self.srt_content_file_path)
            except OSError as exc:
                QMessageBox.warning(self, "Lỗi đọc file", str(exc))
                return

        # Generate
        try:
            self._generated_srt = generate_merged_srt(self.srt_input_path, content_text)
            entry_count = self._generated_srt.count("\n\n") + 1
            if hasattr(self, "srt_result_label"):
                self.srt_result_label.setText(f"✅ Tạo thành công {entry_count} entries!")
            if hasattr(self, "srt_save_btn"):
                self.srt_save_btn.setEnabled(True)
        except SRTGeneratorError as exc:
            self._generated_srt = None
            if hasattr(self, "srt_result_label"):
                self.srt_result_label.setText(f"❌ Lỗi: {exc}")
            if hasattr(self, "srt_save_btn"):
                self.srt_save_btn.setEnabled(False)
            QMessageBox.warning(self, "Lỗi tạo SRT", str(exc))

    def _handle_srt_save(self) -> None:
        if not self._generated_srt:
            return
        default_name = "output.srt"
        if self.srt_input_path:
            default_name = self.srt_input_path.stem + "_merged.srt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu file SRT", str(Path.home() / default_name), "SRT files (*.srt)"
        )
        if path:
            try:
                Path(path).write_text(self._generated_srt, encoding="utf-8")
                QMessageBox.information(self, "Lưu thành công", f"Đã lưu:\n{path}")
            except OSError as exc:
                QMessageBox.warning(self, "Lỗi lưu file", str(exc))

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

    def _handle_bulk_rename(self) -> None:
        if not self.image_folder:
            QMessageBox.information(self, "No folder", "Select an image folder before renaming.")
            return
        try:
            summary = bulk_rename(self.image_folder, "image")
        except BulkRenameError as exc:
            QMessageBox.warning(self, "Bulk rename failed", str(exc))
            return
        message = (
            f"{summary.folder}: renamed {summary.renamed}, skipped {summary.skipped}\n"
            f"Range: {summary.start:0{summary.width}d} \u2192 {summary.end:0{summary.width}d}"
        )
        QMessageBox.information(self, "Bulk rename", message)

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
        selected = [p for p in self.projects if p.is_selected]
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
