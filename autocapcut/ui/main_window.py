"""Sync audio-only GUI for AutoCapcut prototype."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from loguru import logger
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus
from autocapcut.services.project_loader import discover_projects
from autocapcut.services.sync_audio import SyncAudioError, SyncSummary, sync_project_audio
from autocapcut.services.sync_images import SyncImageError, ImageSyncSummary, sync_project_images
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
from autocapcut.services.ffmpeg_render import FFmpegRenderError, render_project_with_ffmpeg


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

def _format_summary(summary: SyncSummary) -> str:
    seconds = summary.audio_duration / 1_000_000
    return (
        f"Updated {summary.updated_segments} segment(s), audio length {seconds:.2f}s"
    )


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
        self.setWindowTitle("AutoCapcut – Sync Audio")
        self.resize(960, 620)

        self.projects: list[ProjectItem] = []
        self.project_table: QTableWidget | None = None
        self._worker: SyncWorker | None = None
        self._last_job_projects: list[ProjectItem] = []
        self.audio_folder: Path | None = None
        self.image_folder: Path | None = None
        self.status_label: QLabel | None = None
        self._status_message_override: str | None = None

        self._build_ui()
        self.refresh_projects()

    # region Qt overrides
    def closeEvent(self, event: QCloseEvent) -> None:  # pragma: no cover - GUI callback
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
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(24)

        layout.addWidget(self._build_control_panel(), 1)
        layout.addWidget(self._build_project_panel(), 1)

        container.setLayout(layout)
        self.setCentralWidget(container)
        self._apply_styles()
        self._set_job_controls_state(False)

    def _build_control_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("controlPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 20, 20, 20)
        panel_layout.setSpacing(18)

        title = QLabel("Control Panel")
        title.setObjectName("controlTitle")
        panel_layout.addWidget(title)

        self.control_tabs = QTabWidget()
        self.control_tabs.setObjectName("controlTabs")
        self.control_tabs.setContentsMargins(0, 0, 0, 0)
        self.control_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.control_tabs.tabBar().setObjectName("controlTabBar")
        self.control_tabs.addTab(self._build_animation_tab(), "Animation")
        self.control_tabs.addTab(self._build_effect_tab(), "Effect")
        self.control_tabs.addTab(self._build_transition_tab(), "Transitions")

        tab_row = QWidget()
        tab_row_layout = QHBoxLayout(tab_row)
        tab_row_layout.setContentsMargins(0, 0, 0, 0)
        tab_row_layout.setSpacing(16)
        tab_row_layout.addStretch(1)
        tab_row_layout.addWidget(self.control_tabs)
        tab_row_layout.addStretch(1)
        panel_layout.addWidget(tab_row)

        panel_layout.addWidget(self._build_asset_panel())

        return panel

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

        label = QLabel("Asset Preparation")
        label.setObjectName("sectionLabel")
        grid.addWidget(label, 0, 0, 1, 3)

        audio_caption = QLabel("Audio Folder")
        audio_caption.setObjectName("assetCaption")
        grid.addWidget(audio_caption, 1, 0, 1, 3)

        self.audio_path_edit = QLineEdit()
        self.audio_path_edit.setPlaceholderText("Choose audio folder…")
        self.audio_path_edit.setReadOnly(True)
        self.audio_path_edit.setObjectName("assetPath")
        grid.addWidget(self.audio_path_edit, 2, 0, 1, 2)

        audio_button = QPushButton("Browse Audio")
        audio_button.setProperty("variant", "secondary")
        audio_button.setObjectName("assetBrowseButton")
        audio_button.clicked.connect(self._handle_select_audio_folder)
        grid.addWidget(audio_button, 2, 2, 1, 1)

        image_caption = QLabel("Image Folder")
        image_caption.setObjectName("assetCaption")
        grid.addWidget(image_caption, 3, 0, 1, 3)

        self.image_path_edit = QLineEdit()
        self.image_path_edit.setPlaceholderText("Choose image folder…")
        self.image_path_edit.setReadOnly(True)
        self.image_path_edit.setObjectName("assetPath")
        grid.addWidget(self.image_path_edit, 4, 0, 1, 2)

        image_button = QPushButton("Browse Images")
        image_button.setProperty("variant", "secondary")
        image_button.setObjectName("assetBrowseButton")
        image_button.clicked.connect(self._handle_select_image_folder)
        grid.addWidget(image_button, 4, 2, 1, 1)

        self.bulk_rename_btn = QPushButton("Bulk Rename")
        self.bulk_rename_btn.setProperty("variant", "primary")
        self.bulk_rename_btn.clicked.connect(self._handle_bulk_rename)
        grid.addWidget(self.bulk_rename_btn, 5, 0, 1, 3)

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

    def _build_project_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("projectPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(16)

        title = QLabel("Project Management")
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

        self.reload_status_button = QPushButton("Reload Status")
        self.reload_status_button.setProperty("variant", "secondary")
        self.reload_status_button.clicked.connect(self._handle_reload_status_clicked)

        self.render_ffmpeg_button = QPushButton("Render via FFmpeg")
        self.render_ffmpeg_button.setProperty("variant", "primary")
        self.render_ffmpeg_button.clicked.connect(self._handle_ffmpeg_render_clicked)
        self.render_ffmpeg_button.setEnabled(False)

        button_row.addWidget(self.reload_button)
        button_row.addWidget(self.sync_audio_button)
        button_row.addWidget(self.sync_images_button)
        button_row.addWidget(self.reload_status_button)
        button_row.addWidget(self.render_ffmpeg_button)
        panel_layout.addLayout(button_row)

        return panel

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            #rootWidget {
                background-color: #f3f4f6;
            }
            #controlPanel,
            #projectPanel {
                background-color: #ffffff;
                border-radius: 16px;
                border: 1px solid #e5e7eb;
            }
            #controlTitle,
            #projectTitle {
                font-size: 18px;
                font-weight: 600;
                color: #0f172a;
            }
            QTabWidget#controlTabs::pane {
                border: none;
            }
            QTabBar#controlTabBar {
                qproperty-drawBase: 0;
                padding: 2px 0;
            }
            QTabBar#controlTabBar::tab {
                background: #e5e7eb;
                border: none;
                border-radius: 12px;
                padding: 4px 14px;
                margin-right: 6px;
                min-width: 88px;
                min-height: 34px;
                color: #1f2937;
                font-weight: 600;
            }
            QTabBar#controlTabBar::tab:selected {
                background: #6366f1;
                color: white;
            }
            QTabBar#controlTabBar::tab:hover:!selected {
                background: #dbe1fe;
            }
            QTabBar#controlTabBar::tab:last {
                margin-right: 0;
            }
            QPushButton {
                min-height: 32px;
                padding: 4px 14px;
                border-radius: 10px;
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
                border-radius: 12px;
                border: none;
                padding: 4px 12px;
                min-width: 110px;
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
            QLineEdit#searchField {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                background: #ffffff;
            }
            QLineEdit#searchField:focus {
                border-color: #6366f1;
            }
            QLineEdit#assetPath {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                background: #f9fafb;
            }
            QLineEdit#assetPath:focus {
                border-color: #6366f1;
                background: #ffffff;
            }
            QPushButton#assetBrowseButton {
                min-width: 128px;
            }
            #durationFrame {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
            #projectTable {
                border: 1px solid #e5e7eb;
                border-radius: 12px;
                background: #ffffff;
            }
            #projectTable::item {
                padding: 6px;
            }
            QHeaderView::section {
                background: #f3f4f6;
                color: #374151;
                font-weight: 600;
                border: none;
                border-right: 1px solid #e5e7eb;
                padding: 8px;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            #statusLabel {
                font-size: 12px;
                color: #4b5563;
                padding-left: 4px;
            }
            QLabel#sectionLabel {
                font-size: 12px;
                font-weight: 600;
                text-transform: uppercase;
                color: #64748b;
                letter-spacing: 0.08em;
            }
            QLabel#assetCaption {
                font-size: 11px;
                font-weight: 600;
                color: #475569;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }
            QLabel#hintLabel {
                font-size: 12px;
                color: #6b7280;
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

    def _update_ffmpeg_button_state(self) -> None:
        if not hasattr(self, "render_ffmpeg_button") or self.render_ffmpeg_button is None:
            return
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            self.render_ffmpeg_button.setEnabled(False)
            return
        can_render = all(
            (p.metadata.get("ffmpeg") or {}).get("ready")
            for p in selected
        )
        worker_running = self._worker is not None and self._worker.isRunning()
        self.render_ffmpeg_button.setEnabled(can_render and not worker_running)

    def _set_job_controls_state(self, busy: bool) -> None:
        controls = (
            getattr(self, "reload_button", None),
            getattr(self, "sync_audio_button", None),
            getattr(self, "sync_images_button", None),
            getattr(self, "bulk_rename_btn", None),
            getattr(self, "insert_animation_button", None),
            getattr(self, "remove_animation_button", None),
            getattr(self, "insert_effect_button", None),
            getattr(self, "remove_effect_button", None),
            getattr(self, "transition_apply_btn", None),
            getattr(self, "transition_clear_btn", None),
            getattr(self, "reload_status_button", None),
            getattr(self, "render_ffmpeg_button", None),
        )
        for button in controls:
            if button is not None:
                button.setEnabled(not busy)
        if not busy:
            self._update_ffmpeg_button_state()

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
        self._update_ffmpeg_button_state()

    # region project table helpers
    def refresh_projects(self) -> None:
        previously_selected = {p.name for p in self.projects if p.is_selected}
        self.projects = discover_projects()
        for project in self.projects:
            project.is_selected = project.name in previously_selected
        if not self.projects:
            logger.warning("No CapCut projects found. Check your CapCut library path.")
        self._populate_table()
        if self._status_message_override is None:
            self._update_status_label()
        else:
            self._update_ffmpeg_button_state()

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

    def _handle_select_audio_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Audio Folder", str(self.audio_folder or Path.home()))
        if folder:
            self.audio_folder = Path(folder)
            self.audio_path_edit.setText(folder)

    def _handle_select_image_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", str(self.image_folder or Path.home()))
        if folder:
            self.image_folder = Path(folder)
            self.image_path_edit.setText(folder)

    def _handle_bulk_rename(self) -> None:
        summaries = []
        try:
            if self.audio_folder:
                summaries.append(bulk_rename(self.audio_folder, "audio"))
            if self.image_folder:
                summaries.append(bulk_rename(self.image_folder, "image"))
        except BulkRenameError as exc:
            QMessageBox.warning(self, "Bulk rename failed", str(exc))
            return
        if not summaries:
            QMessageBox.information(self, "No folders", "Select at least one folder before renaming.")
            return
        message = []
        for summary in summaries:
            message.append(f"{summary.folder}: renamed {summary.renamed}, skipped {summary.skipped}")
        QMessageBox.information(self, 'Bulk rename', '\n'.join(message))

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

    def _handle_ffmpeg_render_clicked(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(self, "No Project Selected", "Select a project to render via FFmpeg.")
            return
        if len(selected) > 1:
            QMessageBox.information(
                self,
                "Multiple Projects",
                "FFmpeg render currently supports one project at a time.",
            )
            return

        project = selected[0]
        plan = (project.metadata.get("ffmpeg") or {})
        if not plan.get("ready"):
            reason = ", ".join(plan.get("issues") or ["Unsupported timeline"])
            QMessageBox.warning(
                self,
                "FFmpeg Render Unavailable",
                f"Project {project.name} is not FFmpeg-ready.\nReason: {reason}",
            )
            return

        default_name = f"{project.name}.mp4"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export via FFmpeg",
            str(Path.home() / default_name),
            "MP4 Video (*.mp4)",
        )
        if not save_path:
            return

        output_path = Path(save_path)
        try:
            render_project_with_ffmpeg(project, plan, output_path)
        except FFmpegRenderError as exc:
            logger.exception("FFmpeg render failed for %s", project.name)
            QMessageBox.critical(self, "FFmpeg Render Failed", str(exc))
            return

        QMessageBox.information(
            self,
            "FFmpeg Render Complete",
            f"Exported project {project.name} to\n{output_path}",
        )

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

    def _handle_stop_clicked(self) -> None:
        QMessageBox.information(self, "No Sync Running", "There is no active job to stop.")

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

    def _handle_reload_status_clicked(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(
                self,
                "No Project Selected",
                "Select at least one project before reloading status.",
            )
            return

        summaries: list[str] = []
        for project in selected:
            info = project.refresh_metadata()
            if info:
                duration = info.get("duration_s")
                track_count = info.get("track_count", 0)
                video_segments = info.get("video_segments", 0)
                parts = []
                if duration:
                    parts.append(f"duration {duration:.2f}s")
                parts.append(f"tracks {track_count}")
                if video_segments:
                    parts.append(f"video segments {video_segments}")
                ffmpeg_plan = info.get("ffmpeg") or {}
                if ffmpeg_plan.get("ready"):
                    parts.append("FFmpeg ready")
                else:
                    reason = ", ".join(ffmpeg_plan.get("issues") or []) or "FFmpeg unsupported"
                    parts.append(f"FFmpeg blocked: {reason}")
                project.notes = "Reloaded: " + ", ".join(parts)
            else:
                project.notes = "Reloaded: no metadata found"
            project.status = ProjectStatus.pending
            summaries.append(f"{project.name}: {project.notes}")

        self._refresh_status_cells()
        QMessageBox.information(
            self,
            "Project status reloaded",
            "\n".join(summaries),
        )
        self._update_ffmpeg_button_state()

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
