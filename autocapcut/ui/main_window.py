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
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus
from autocapcut.services.project_loader import discover_projects
from autocapcut.services.sync_audio import SyncAudioError, SyncSummary, sync_project_audio
from autocapcut.services.sync_images import SyncImageError, ImageSyncSummary, sync_project_images
from autocapcut.services.animation import TransitionError, apply_transition, clear_transitions
from autocapcut.services.animation_presets import TRANSITION_PRESETS
from autocapcut.services.bulk_rename import BulkRenameError, bulk_rename


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
        layout = QVBoxLayout(container)

        header = QLabel("Select project(s) to sync image durations with the audio track.")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setObjectName("headerLabel")
        layout.addWidget(header)

        layout.addLayout(self._build_rename_panel())
        layout.addLayout(self._build_transition_panel())

        self.project_table = QTableWidget(container)
        self.project_table.setColumnCount(5)
        self.project_table.setHorizontalHeaderLabels(
            ["Select", "Project", "Source", "Status", "Notes"]
        )
        header_view = self.project_table.horizontalHeader()
        header_view.setSectionResizeMode(self.columns.select, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.name, header_view.ResizeMode.Stretch)
        header_view.setSectionResizeMode(self.columns.source, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.status, header_view.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.columns.notes, header_view.ResizeMode.Stretch)
        self.project_table.verticalHeader().setVisible(False)
        self.project_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.project_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.project_table)

        button_row = QHBoxLayout()
        reload_button = QPushButton("Reload Projects", container)
        reload_button.clicked.connect(self.refresh_projects)

        sync_button = QPushButton("Sync Audio", container)
        sync_button.clicked.connect(self._handle_sync_clicked)

        stop_button = QPushButton("Stop", container)
        stop_button.clicked.connect(self._handle_stop_clicked)

        sync_images_button = QPushButton("Sync Images", container)
        sync_images_button.clicked.connect(self._handle_sync_images_clicked)

        for button in (reload_button, sync_button, sync_images_button, stop_button):
            button_row.addWidget(button)

        layout.addLayout(button_row)
        container.setLayout(layout)
        self.setCentralWidget(container)

    def _build_rename_panel(self):
        layout = QHBoxLayout()

        self.audio_path_edit = QLineEdit()
        self.audio_path_edit.setPlaceholderText("Choose audio folder…")
        self.audio_path_edit.setReadOnly(True)
        audio_btn = QPushButton("Browse Audio")
        audio_btn.clicked.connect(self._handle_select_audio_folder)

        self.image_path_edit = QLineEdit()
        self.image_path_edit.setPlaceholderText("Choose image folder…")
        self.image_path_edit.setReadOnly(True)
        image_btn = QPushButton("Browse Images")
        image_btn.clicked.connect(self._handle_select_image_folder)

        rename_btn = QPushButton("Bulk Rename")
        rename_btn.clicked.connect(self._handle_bulk_rename)

        for widget in (self.audio_path_edit, audio_btn, self.image_path_edit, image_btn, rename_btn):
            layout.addWidget(widget)
        layout.addStretch(1)
        return layout

    def _build_transition_panel(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        self.transition_apply_btn = QPushButton("Apply Black Fade")
        self.transition_apply_btn.clicked.connect(self._handle_transition_apply)
        self.transition_clear_btn = QPushButton("Remove Transitions")
        self.transition_clear_btn.clicked.connect(self._handle_transition_clear)
        layout.addWidget(self.transition_apply_btn)
        layout.addWidget(self.transition_clear_btn)
        layout.addStretch(1)
        return layout

    # region project table helpers
    def refresh_projects(self) -> None:
        previously_selected = {p.name for p in self.projects if p.is_selected}
        self.projects = discover_projects()
        for project in self.projects:
            project.is_selected = project.name in previously_selected
        if not self.projects:
            logger.warning("No CapCut projects found. Check your CapCut library path.")
        self._populate_table()

    def _populate_table(self) -> None:
        if self.project_table is None:
            return
        self.project_table.setRowCount(len(self.projects))
        for row, project in enumerate(self.projects):
            checkbox = QCheckBox()
            checkbox.setChecked(project.is_selected)
            checkbox.stateChanged.connect(self._make_checkbox_handler(project))
            self.project_table.setCellWidget(row, self.columns.select, checkbox)

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

    def _make_checkbox_handler(self, project: ProjectItem):
        def handler(state: int) -> None:
            project.is_selected = Qt.CheckState(state) == Qt.CheckState.Checked
            logger.debug("Project %s selection=%s", project.name, project.is_selected)
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
        self._worker.start()

    def _handle_stop_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            logger.info("User requested cancellation")
            self._worker.cancel()
        else:
            QMessageBox.information(self, "No Sync Running", "There is no active job to stop.")

    def _on_worker_finished(self) -> None:  # pragma: no cover - GUI callback
        self._refresh_status_cells()
        self._worker = None
        summary = self._summarise_last_job()
        if summary:
            QMessageBox.information(self, "Sync complete", summary)

    def _handle_transition_apply(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before applying transitions.")
            return
        for project in selected:
            try:
                summary = apply_transition(project, preset_key='black_fade', duration_seconds=0.5)
                project.status = ProjectStatus.done
                project.notes = f"Transitions: {summary.applied_transitions} applied"
            except TransitionError as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
        self._refresh_status_cells()

    def _handle_transition_clear(self) -> None:
        selected = [p for p in self.projects if p.is_selected]
        if not selected:
            QMessageBox.information(self, "No Project Selected", "Select at least one project before removing transitions.")
            return
        for project in selected:
            try:
                removed = clear_transitions(project)
                project.status = ProjectStatus.done
                project.notes = f"Removed {removed} transitions"
            except TransitionError as exc:
                project.status = ProjectStatus.failed
                project.notes = str(exc)
        self._refresh_status_cells()

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
