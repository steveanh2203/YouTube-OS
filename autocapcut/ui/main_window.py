"""Sync audio-only GUI for AutoCapcut prototype."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re
import sys
from typing import Callable, List
from uuid import uuid4

from loguru import logger
from PySide6.QtCore import QDateTime, QSize, QSettings, QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPixmap,
    QShowEvent,
    QTextCursor,
)
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
    QDateTimeEdit,
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
try:
    from AppKit import NSScreen
except ImportError:  # pragma: no cover - non-macOS fallback
    NSScreen = None

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
from autocapcut.services.raw_seo import (
    RawSEOError,
    apply_raw_seo,
    parse_keywords,
    read_raw_seo_metadata,
    write_raw_seo_payload,
)
from autocapcut.services.roxy_upload import (
    RoxyApiClient,
    RoxyPreflightResult,
    RoxyUploadError,
    get_roxy_rate_limit_snapshot,
    run_roxy_upload_preflight,
    upload_video_via_roxy,
)
from autocapcut.services.srt_generator import (
    SRTGenerationCancelled,
    SRTGeneratorError,
    StrictMatchReview,
    generate_merged_srt,
    parse_content,
)

DEFAULT_AUTOMATE_ROXY_PROFILE_ID = "2c7168a71197394052ee67be8e0a7fc0"
VIDEO_EXPORT_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
GOOGLE_ICON_NAMES = {
    "projects": "folder",
    "videos": "movie",
    "render": "play_circle_filled",
    "analytics": "analytics",
    "audio": "equalizer",
    "captions": "subtitles",
    "seo": "find_in_page",
    "add": "add",
    "edit": "edit",
    "delete": "delete",
    "children": "video_library",
}

SHOW_UI_ICONS = False


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
                self._emit_log("[STEP 1/5] Preparing CapCut dashboard...")
                automation.focus_capcut()
                if not automation.wait_for_dashboard_ready(APP_CONFIG.dashboard_ready_timeout_sec):
                    raise RuntimeError(
                        f"Dashboard not ready within {APP_CONFIG.dashboard_ready_timeout_sec}s"
                    )
                self._emit_log("[STEP 1/5] ✓ Dashboard ready")

                project.notes = "Opening project..."
                self.status_updated.emit()

                self._emit_log("[STEP 1/5] Locating and opening project in CapCut...")
                if not automation.open_project(project):
                    raise RuntimeError("Could not open project")
                self._emit_log("[STEP 1/5] ✓ Project clicked")

                project.notes = "Waiting for editor..."
                self.status_updated.emit()

                self._emit_log("[STEP 2/5] Waiting for project editor to load...")
                if not automation.wait_for_project_editor_ready(
                    project,
                    APP_CONFIG.project_open_timeout_sec,
                ):
                    retries = APP_CONFIG.project_open_retry_count
                    raise RuntimeError(
                        "Project editor not ready within "
                        f"{APP_CONFIG.project_open_timeout_sec}s (retry {retries}/{retries} exhausted)"
                    )
                self._emit_log("[STEP 2/5] ✓ Project editor ready")

                project.notes = "Starting export..."
                self.status_updated.emit()

                self._emit_log("[STEP 3/5] Opening export dialog and confirming export...")
                if not automation.start_render(project.name):
                    raise RuntimeError("Could not start render")
                self._emit_log("[STEP 3/5] ✓ Export dialog ready")

                if not automation.wait_for_export_started(
                    APP_CONFIG.export_start_timeout_sec,
                    getattr(automation, "last_export_folder", None),
                    getattr(automation, "last_export_name", None),
                ):
                    raise RuntimeError(
                        f"Export did not start after confirmation within {APP_CONFIG.export_start_timeout_sec}s"
                    )
                self._emit_log("[STEP 3/5] ✓ Export started")

                project.notes = "Rendering..."
                self.status_updated.emit()

                self._emit_log("[STEP 4/5] Waiting for export to complete...")
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
                self._emit_log("[STEP 4/5] ✓ Export completed")

                project.notes = "Closing project..."
                self.status_updated.emit()

                self._emit_log("[STEP 5/5] Closing project and returning to dashboard...")
                if not automation.close_project():
                    raise RuntimeError("Could not close project and return to dashboard")
                self._emit_log("[STEP 5/5] ✓ Returned to dashboard")

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


class AutomateWorker(QThread):
    """Background worker for full automate pipeline: Render -> Raw SEO -> Upload."""

    status_updated = Signal()
    job_finished = Signal(int, int, int, bool)  # completed, failed, total, stopped_early
    progress_updated = Signal(int, int)
    log_message = Signal(str)

    def __init__(
        self,
        jobs: list[AutomateJobItem],
        *,
        api_host: str,
        api_key: str,
        workspace_id: int,
        profile_id: str,
        close_profile_after_start: bool,
        strict_raw_seo: bool,
        debug_root: Path,
    ) -> None:
        super().__init__()
        self.jobs = jobs
        self.api_host = api_host
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.profile_id = profile_id
        self.close_profile_after_start = close_profile_after_start
        self.strict_raw_seo = strict_raw_seo
        self.debug_root = debug_root
        self._cancelled = False
        self._completed = 0
        self._failed = 0
        self._stopped_early = False

    def cancel(self) -> None:
        self._cancelled = True

    def _emit_log(self, message: str) -> None:
        logger.info(message)
        self.log_message.emit(message)

    @staticmethod
    def _resolve_rendered_file(
        automation,
        *,
        export_folder: Path | str | None,
        export_name: str | None,
        project_name: str,
    ) -> Path:
        cached_file = getattr(automation, "last_export_file", None)
        if isinstance(cached_file, Path):
            try:
                if cached_file.exists() and cached_file.is_file():
                    return cached_file.resolve()
            except OSError:
                pass

        find_candidate = getattr(automation, "_find_export_candidate", None)
        if callable(find_candidate):
            try:
                resolved = find_candidate(export_folder, export_name or project_name)
            except Exception:
                resolved = None
            if isinstance(resolved, Path) and resolved.exists() and resolved.is_file():
                return resolved.resolve()

        resolve_expected = getattr(automation, "_resolve_expected_export_path", None)
        if callable(resolve_expected):
            try:
                expected = resolve_expected(export_folder, export_name or project_name)
            except Exception:
                expected = None
            if isinstance(expected, Path) and expected.exists() and expected.is_file():
                return expected.resolve()

        folder = Path(export_folder).expanduser() if export_folder else None
        if folder and folder.is_dir():
            hint = Path(export_name or project_name).stem.casefold().strip()
            hinted: list[Path] = []
            all_candidates: list[Path] = []
            for entry in folder.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in VIDEO_EXPORT_EXTENSIONS:
                    continue
                all_candidates.append(entry)
                entry_stem = entry.stem.casefold().strip()
                if hint and (hint in entry_stem or entry_stem in hint):
                    hinted.append(entry)
            if hinted:
                return max(hinted, key=lambda path: path.stat().st_mtime).resolve()
            if all_candidates:
                return max(all_candidates, key=lambda path: path.stat().st_mtime).resolve()

        raise RuntimeError(
            "Could not locate rendered video file. "
            f"folder={export_folder or '(unknown)'} name={export_name or project_name}"
        )

    def run(self) -> None:
        from autocapcut.automation.capcut_automation import MacCapCutAutomation
        from autocapcut.config import APP_CONFIG

        total = len(self.jobs)
        self._emit_log("========== AUTOMATE WORKER STARTED ==========")
        self._emit_log(f"Total videos to automate: {total}")
        automation = MacCapCutAutomation()
        self.progress_updated.emit(0, total)

        for idx, job in enumerate(self.jobs, 1):
            project = job.project
            self._emit_log("")
            self._emit_log(f"------ VIDEO {idx}/{total}: {project.name} ------")

            if self._cancelled:
                self._emit_log(f"[CANCELLED] Automate cancelled before project {project.name}")
                project.status = ProjectStatus.failed
                project.notes = "Cancelled by user"
                self._stopped_early = True
                self.status_updated.emit()
                break

            project.status = ProjectStatus.processing
            project.notes = "Opening project..."
            self.status_updated.emit()

            try:
                self._emit_log("[STEP 1/7] Preparing CapCut dashboard...")
                automation.focus_capcut()
                if not automation.wait_for_dashboard_ready(APP_CONFIG.dashboard_ready_timeout_sec):
                    raise RuntimeError(
                        f"Dashboard not ready within {APP_CONFIG.dashboard_ready_timeout_sec}s"
                    )
                self._emit_log("[STEP 1/7] ✓ Dashboard ready")

                self._emit_log("[STEP 1/7] Locating and opening project in CapCut...")
                if not automation.open_project(project):
                    raise RuntimeError("Could not open project")
                self._emit_log("[STEP 1/7] ✓ Project clicked")

                project.notes = "Waiting for editor..."
                self.status_updated.emit()

                self._emit_log("[STEP 2/7] Waiting for project editor to load...")
                if not automation.wait_for_project_editor_ready(
                    project,
                    APP_CONFIG.project_open_timeout_sec,
                ):
                    retries = APP_CONFIG.project_open_retry_count
                    raise RuntimeError(
                        "Project editor not ready within "
                        f"{APP_CONFIG.project_open_timeout_sec}s (retry {retries}/{retries} exhausted)"
                    )
                self._emit_log("[STEP 2/7] ✓ Project editor ready")

                project.notes = "Starting export..."
                self.status_updated.emit()

                self._emit_log("[STEP 3/7] Opening export dialog and confirming export...")
                if not automation.start_render(project.name):
                    raise RuntimeError("Could not start render")
                self._emit_log("[STEP 3/7] ✓ Export dialog ready")

                if not automation.wait_for_export_started(
                    APP_CONFIG.export_start_timeout_sec,
                    getattr(automation, "last_export_folder", None),
                    getattr(automation, "last_export_name", None),
                ):
                    raise RuntimeError(
                        f"Export did not start after confirmation within {APP_CONFIG.export_start_timeout_sec}s"
                    )
                self._emit_log("[STEP 3/7] ✓ Export started")

                project.notes = "Rendering..."
                self.status_updated.emit()

                self._emit_log("[STEP 4/7] Waiting for export to complete...")
                export_folder = getattr(automation, "last_export_folder", None)
                export_name = getattr(automation, "last_export_name", None)
                if not automation.wait_for_render_complete(
                    APP_CONFIG.render_timeout_sec,
                    export_folder,
                    export_name,
                ):
                    raise RuntimeError("Render timeout")
                self._emit_log("[STEP 4/7] ✓ Export completed")

                export_file = getattr(automation, "last_export_file", None)
                if export_file:
                    self._emit_log(f"[STEP 4/7] Export file detected: {export_file}")

                rendered_file = self._resolve_rendered_file(
                    automation,
                    export_folder=export_folder,
                    export_name=export_name,
                    project_name=project.name,
                )
                self._emit_log(f"[STEP 4/7] Resolved output: {rendered_file}")

                if job.raw_seo_enabled:
                    project.notes = "Applying Raw SEO..."
                    self.status_updated.emit()
                    self._emit_log(f"[STEP 5/7] Applying Raw SEO ({job.raw_seo_source})...")
                    raw_summary = apply_raw_seo(
                        [rendered_file],
                        title=job.raw_seo_title,
                        description=job.raw_seo_description,
                        keywords=job.raw_seo_keywords,
                        extra_tags=job.raw_seo_extra_tags,
                        rename_to_title=False,
                        strict_verify=self.strict_raw_seo,
                    )
                    first_result = raw_summary.results[0] if raw_summary.results else None
                    if raw_summary.failed > 0 or first_result is None or not first_result.success:
                        detail = first_result.message if first_result is not None else "Unknown Raw SEO failure"
                        raise RawSEOError(detail)
                    rendered_file = first_result.file
                    self._emit_log("[STEP 5/7] ✓ Raw SEO applied")
                else:
                    self._emit_log("[STEP 5/7] Raw SEO skipped (all fields are empty)")

                project.notes = "Uploading via Roxy..."
                self.status_updated.emit()

                self._emit_log("[STEP 6/7] Running Roxy preflight...")
                preflight = run_roxy_upload_preflight(
                    api_host=self.api_host,
                    api_token=self.api_key,
                    workspace_id=self.workspace_id,
                    profile_id=self.profile_id,
                    video_path=rendered_file,
                )
                self._emit_log(
                    "[STEP 6/7] ✓ Preflight passed "
                    f"(workspace={preflight.workspace_id}, profile={preflight.profile_display_name})"
                )

                self._emit_log("[STEP 7/7] Uploading video via Roxy...")
                upload_video_via_roxy(
                    api_host=self.api_host,
                    api_token=self.api_key,
                    workspace_id=preflight.workspace_id,
                    profile_id=preflight.profile_id,
                    video_path=preflight.video_path,
                    close_profile_after_start=self.close_profile_after_start,
                    progress_cb=lambda msg: self._emit_log(f"  [Roxy] {msg}"),
                    debug_root=self.debug_root,
                )
                self._emit_log("[STEP 7/7] ✓ Upload started")
                self._emit_log("[DONE] Automate flow completed (project left open by design).")

                project.status = ProjectStatus.done
                project.notes = "Automated: render -> raw SEO -> upload started (no auto close)"
                self._completed += 1
                self._emit_log(f"[SUCCESS] Video '{project.name}' automated successfully!")

            except Exception as exc:
                logger.exception("[ERROR] Automate failed for %s: %s", project.name, exc)
                project.status = ProjectStatus.failed
                project.notes = str(exc)
                self._failed += 1
                self._emit_log(f"[ERROR] Automate failed for {project.name}: {exc}")
                try:
                    self._emit_log("[CLEANUP] Attempting to close project...")
                    automation.close_project()
                except Exception:
                    pass
                self._emit_log("[STOP] Pipeline stopped after failure. Please check logs.")
                self._stopped_early = True
                self.status_updated.emit()
                self.progress_updated.emit(self._completed + self._failed, total)
                break

            self.status_updated.emit()
            self.progress_updated.emit(self._completed + self._failed, total)

        self._emit_log("")
        self._emit_log("========== AUTOMATE WORKER FINISHED ==========")
        self._emit_log(f"Completed: {self._completed}, Failed: {self._failed}")
        self.job_finished.emit(self._completed, self._failed, total, self._stopped_early)


class RoxyUploadWorker(QThread):
    """Background worker for Roxy + YouTube upload startup."""

    progress_message = Signal(str)
    upload_finished = Signal(bool, str, object)

    def __init__(
        self,
        *,
        api_host: str,
        api_key: str,
        workspace_id: int,
        profile_id: str,
        video_path: Path,
        close_profile_after_start: bool,
        debug_root: Path,
    ) -> None:
        super().__init__()
        self.api_host = api_host
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.profile_id = profile_id
        self.video_path = video_path
        self.close_profile_after_start = close_profile_after_start
        self.debug_root = debug_root

    def run(self) -> None:  # pragma: no cover - thread execution
        try:
            summary = upload_video_via_roxy(
                api_host=self.api_host,
                api_token=self.api_key,
                workspace_id=self.workspace_id,
                profile_id=self.profile_id,
                video_path=self.video_path,
                close_profile_after_start=self.close_profile_after_start,
                progress_cb=self.progress_message.emit,
                debug_root=self.debug_root,
            )
        except RoxyUploadError as exc:
            logger.warning("Roxy upload failed: {}", exc)
            self.upload_finished.emit(False, str(exc), None)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unexpected Roxy upload error")
            self.upload_finished.emit(False, f"Unexpected error: {exc}", None)
        else:
            self.upload_finished.emit(True, "", summary)


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
    project: int = 4


@dataclass
class AutomateJobItem:
    project: ProjectItem
    raw_seo_title: str
    raw_seo_description: str
    raw_seo_keywords: list[str]
    raw_seo_extra_tags: dict[str, str]
    raw_seo_enabled: bool
    raw_seo_source: str


class MainWindow(QMainWindow):
    """Main application window focused on the Sync Audio feature."""

    columns = TableColumns()
    _main_window_bottom_margin = 0

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoCapCut")
        self.resize(1280, 740)
        self._fit_window_to_screen(
            self,
            min_width=1280,
            min_height=740,
            margin=0,
            bottom_margin=self._main_window_bottom_margin,
        )

        self.projects: list[ProjectItem] = []
        self.project_table: QTableWidget | None = None
        self._worker: SyncWorker | None = None
        self._render_worker: RenderWorker | None = None
        self._automate_worker: AutomateWorker | None = None
        self._last_job_projects: list[ProjectItem] = []
        self.image_folder: Path | None = None
        self.status_label: QLabel | None = None
        self._status_message_override: str | None = None
        self._render_log_dialog: QDialog | None = None
        self._render_log_text: QTextEdit | None = None
        self._render_progress_bar: QProgressBar | None = None
        self._render_progress_label: QLabel | None = None
        self._render_current_video_label: QLabel | None = None
        self._render_current_stage_label: QLabel | None = None
        self._render_total_chip: QLabel | None = None
        self._render_done_chip: QLabel | None = None
        self._render_failed_chip: QLabel | None = None
        self._render_remaining_chip: QLabel | None = None
        self._render_total_jobs: int = 0
        self._render_done_jobs: int = 0
        self._render_failed_jobs: int = 0
        self._render_current_video: str = "-"
        self._render_current_stage: str = "Waiting..."
        self._workspace_header: QWidget | None = None
        self.raw_seo_files: list[Path] = []
        self.raw_seo_draft: dict[str, object] = self._default_raw_seo_draft()
        self.roxy_video_path: Path | None = None
        self._roxy_rate_timer: QTimer | None = None
        self._roxy_upload_worker: RoxyUploadWorker | None = None
        self._roxy_upload_progress: QProgressDialog | None = None
        self.srt_input_path: Path | None = None
        self.srt_content_file_path: Path | None = None
        self._generated_srt: str | None = None
        self._nav_buttons: dict[str, QPushButton] = {}
        self._nav_group: QButtonGroup | None = None
        self._tool_stack: QStackedWidget | None = None
        self._tool_title_label: QLabel | None = None
        self._workspace_stack: QStackedWidget | None = None
        self._workspace_title_label: QLabel | None = None
        self._workspace_description_label: QLabel | None = None
        self.project_parent_list: QListWidget | None = None
        self.project_parent_empty_label: QLabel | None = None
        self.project_parent_detail_name: QLabel | None = None
        self.project_parent_detail_meta: QLabel | None = None
        self.project_parent_detail_stats: QLabel | None = None
        self.project_parent_edit_btn: QPushButton | None = None
        self.project_parent_delete_btn: QPushButton | None = None
        self.project_parent_open_children_btn: QPushButton | None = None
        self._selected_project_preset_id: str = ""
        self._font_family: str = "Space Grotesk"
        self._icon_font_family: str = ""
        self._icon_codepoints: dict[str, str] = {}
        self._updating_srt_paste_text: bool = False
        self._settings = QSettings("AutoCapCut", "AutoCapCut")
        self._last_srt_output_dir: Path = self._load_last_srt_output_dir()
        self.project_presets: list[dict[str, object]] = []
        self.project_assignment_by_path: dict[str, str] = {}
        self.video_child_settings_by_path: dict[str, dict[str, object]] = {}
        self.manual_child_projects_by_parent: dict[str, list[dict[str, object]]] = {}
        self.project_hierarchy_scroll: QScrollArea | None = None
        self.project_hierarchy_root: QWidget | None = None
        self.project_hierarchy_layout: QVBoxLayout | None = None
        self._load_project_dashboard_settings()

        self._build_ui()
        self.refresh_projects()

    def _fit_window_to_screen(
        self,
        widget: QWidget,
        *,
        min_width: int = 420,
        min_height: int = 240,
        margin: int = 0,
        bottom_margin: int | None = None,
    ) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        effective_bottom_margin = margin if bottom_margin is None else bottom_margin
        usable_rect = None
        if sys.platform == "darwin" and NSScreen is not None:
            cocoa_screen = NSScreen.mainScreen()
            if cocoa_screen is not None:
                frame = cocoa_screen.frame()
                visible = cocoa_screen.visibleFrame()
                top_inset = int(round((frame.origin.y + frame.size.height) - (visible.origin.y + visible.size.height)))
                usable_rect = (
                    int(round(visible.origin.x)),
                    top_inset,
                    int(round(visible.size.width)),
                    int(round(visible.size.height)),
                )

        if usable_rect is not None:
            usable_x, usable_y, usable_width, usable_height = usable_rect
            target_width = max(min_width, usable_width - (margin * 2))
            target_height = max(min_height, usable_height - margin - effective_bottom_margin)
            widget.resize(target_width, target_height)
            widget.move(usable_x + margin, usable_y + margin)
            return

        target_width = max(min_width, available.width() - (margin * 2))
        target_height = max(min_height, available.height() - margin - effective_bottom_margin)
        widget.resize(target_width, target_height)
        widget.move(available.x() + margin, available.y() + margin)

    def _fit_main_window(self) -> None:
        self._fit_window_to_screen(
            self,
            min_width=1280,
            min_height=740,
            margin=0,
            bottom_margin=self._main_window_bottom_margin,
        )

    # region Qt overrides
    def showEvent(self, event: QShowEvent) -> None:  # pragma: no cover - GUI callback
        super().showEvent(event)
        self._fit_main_window()

    def closeEvent(self, event: QCloseEvent) -> None:  # pragma: no cover - GUI callback
        if self._roxy_upload_worker and self._roxy_upload_worker.isRunning():
            QMessageBox.information(
                self,
                "Upload in progress",
                "Roxy upload is still running. Please wait until it finishes.",
            )
            event.ignore()
            return
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
        if self._automate_worker and self._automate_worker.isRunning():
            choice = QMessageBox.question(
                self,
                "Automation in progress",
                "Automate workflow is still running. Stop and exit?",
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._automate_worker.cancel()
                self._automate_worker.wait(2000)
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
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)

        sidebar = self._build_sidebar()
        workspace_panel = self._build_workspace_panel()

        # Subtle drop shadows for depth
        for widget, blur, opacity in (
            (sidebar, 18, 12),
            (workspace_panel, 28, 16),
        ):
            fx = QGraphicsDropShadowEffect()
            fx.setBlurRadius(blur)
            fx.setXOffset(0)
            fx.setYOffset(2)
            fx.setColor(QColor(0, 0, 0, opacity))
            widget.setGraphicsEffect(fx)

        layout.addWidget(sidebar)
        layout.addWidget(workspace_panel, 1)

        container.setLayout(layout)
        self.setCentralWidget(container)
        self._apply_styles()
        self._set_job_controls_state(False)

    def _load_fonts(self) -> str:
        """Prefer the macOS system SF font family exposed to Qt."""
        here = Path(__file__).resolve().parent.parent.parent / "resources" / "fonts"
        icon_font_path = (here / "MaterialIcons-Regular.ttf").resolve()
        icon_codepoints_path = (here / "MaterialIcons-Regular.codepoints").resolve()

        if icon_font_path.exists():
            fid = QFontDatabase.addApplicationFont(str(icon_font_path))
            if fid != -1:
                families = QFontDatabase.applicationFontFamilies(fid)
                if families:
                    self._icon_font_family = families[0]
        if icon_codepoints_path.exists():
            codepoints: dict[str, str] = {}
            for raw_line in icon_codepoints_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) == 2:
                    name, code = parts
                    codepoints[name] = code
            self._icon_codepoints = codepoints

        family = ".AppleSystemUIFont"
        app = QApplication.instance()
        if app:
            app.setFont(QFont(family, 13))
        return family

    def _make_symbol_icon(self, kind: str, color: str, *, size: int = 18) -> QIcon:
        if not SHOW_UI_ICONS:
            return QIcon()
        symbol_name = GOOGLE_ICON_NAMES.get(kind, "")
        codepoint = self._icon_codepoints.get(symbol_name, "")
        if self._icon_font_family and codepoint:
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor(color))
            font = QFont(self._icon_font_family)
            font.setPixelSize(int(size * 1.1))
            painter.setFont(font)
            painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), chr(int(codepoint, 16)))
            painter.end()
            return QIcon(pixmap)

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(color))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if kind == "projects":
            painter.drawRoundedRect(3, 6, size - 6, size - 9, 3, 3)
            painter.drawLine(5, 6, size * 0.45, 6)
            painter.drawLine(size * 0.45, 6, size * 0.58, 3.8)
            painter.drawLine(size * 0.58, 3.8, size - 4.5, 3.8)
        elif kind == "render":
            painter.drawRoundedRect(3, 4, size - 6, size - 8, 4, 4)
            painter.drawLine(6, size - 6, size - 6, size - 6)
            painter.drawLine(7, size - 8, 7, size - 11)
            painter.drawLine(size * 0.5, size - 8, size * 0.5, size - 13)
            painter.drawLine(size - 7, size - 8, size - 7, size - 10)
        elif kind == "analytics":
            painter.drawLine(4, size - 4, size - 4, size - 4)
            painter.drawLine(6, size - 4, 6, size - 9)
            painter.drawLine(size * 0.5, size - 4, size * 0.5, size - 12)
            painter.drawLine(size - 6, size - 4, size - 6, size - 7)
        elif kind == "audio":
            painter.drawLine(5.5, size - 5, 5.5, size * 0.38)
            painter.drawLine(5.5, size * 0.52, size * 0.58, size * 0.34)
            painter.drawArc(int(size * 0.46), int(size * 0.30), int(size * 0.34), int(size * 0.34), -20 * 16, 220 * 16)
        elif kind == "captions":
            painter.drawRoundedRect(3.5, 4.5, size - 7, size - 9, 3, 3)
            painter.drawLine(6.5, 8, size - 6.5, 8)
            painter.drawLine(6.5, 11.5, size * 0.6, 11.5)
            painter.drawLine(6.5, 15, size - 8.5, 15)
        elif kind == "seo":
            painter.drawRoundedRect(4, 4, size - 8, size - 8, 4, 4)
            painter.drawLine(6.5, 7.5, size - 6.5, 7.5)
            painter.drawLine(6.5, 11, size * 0.62, 11)
            painter.drawLine(6.5, 14.5, size - 8.5, 14.5)
        elif kind == "add":
            painter.drawLine(size / 2, 4, size / 2, size - 4)
            painter.drawLine(4, size / 2, size - 4, size / 2)
        elif kind == "edit":
            painter.drawLine(5, size - 5, size - 5, 5)
            painter.drawLine(size - 7, 4.5, size - 4, 7.5)
            painter.drawLine(4, size - 4, 7.5, size - 4.8)
        elif kind == "delete":
            painter.drawLine(5, 5, size - 5, 5)
            painter.drawLine(7, 5, 8.5, 3.5)
            painter.drawLine(size - 7, 5, size - 8.5, 3.5)
            painter.drawRoundedRect(6, 6.5, size - 12, size - 10, 2, 2)
            painter.drawLine(size * 0.42, 8.5, size * 0.42, size - 5)
            painter.drawLine(size * 0.58, 8.5, size * 0.58, size - 5)
        elif kind == "children":
            painter.drawRoundedRect(3, 5.5, size - 6, size - 9, 3, 3)
            painter.drawLine(size * 0.42, size * 0.38, size * 0.68, size / 2)
            painter.drawLine(size * 0.42, size * 0.62, size * 0.68, size / 2)
        painter.end()
        return QIcon(pixmap)

    def _refresh_sidebar_nav_icons(self) -> None:
        for btn in self._nav_buttons.values():
            kind = str(btn.property("iconKind") or "").strip()
            if not kind:
                continue
            color = "#0B57D0" if btn.isChecked() else "#5F6368"
            btn.setIcon(self._make_symbol_icon(kind, color))
            btn.setIconSize(QSize(18, 18))

    def _build_sidebar(self) -> QFrame:
        """Build the primary workspace sidebar."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(248)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("sidebarHeader")
        hdr_layout = QVBoxLayout(header)
        hdr_layout.setContentsMargins(22, 22, 22, 18)
        hdr_layout.setSpacing(14)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(12)

        monogram = QLabel("AC")
        monogram.setObjectName("sidebarMonogram")

        brand_copy = QVBoxLayout()
        brand_copy.setContentsMargins(0, 0, 0, 0)
        brand_copy.setSpacing(4)
        product_chip = QLabel("DESKTOP CONTROL")
        product_chip.setObjectName("sidebarChip")
        app_name = QLabel("AutoCapCut")
        app_name.setObjectName("appName")
        app_tagline = QLabel("Operational workspace for project structure, render monitoring, and analytics.")
        app_tagline.setObjectName("appTagline")
        brand_copy.addWidget(product_chip)
        brand_copy.addWidget(app_name)
        brand_row.addWidget(monogram, 0, Qt.AlignmentFlag.AlignTop)
        brand_row.addLayout(brand_copy, 1)

        hdr_layout.addLayout(brand_row)
        hdr_layout.addWidget(app_tagline)
        layout.addWidget(header)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        nav_area = QWidget()
        nav_area.setObjectName("navArea")
        nav_layout = QVBoxLayout(nav_area)
        nav_layout.setContentsMargins(14, 18, 14, 14)
        nav_layout.setSpacing(10)

        section = QLabel("CORE WORKSPACES")
        section.setObjectName("navSection")
        nav_layout.addWidget(section)

        for key, label in (
            ("projects", "Projects"),
            ("render_dashboard", "Render Dashboard"),
            ("analytics", "Analytics"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("navBtn")
            btn.setProperty("iconKind", "render" if key == "render_dashboard" else key)
            btn.clicked.connect(lambda _checked, k=key: self._switch_workspace(k))
            self._nav_buttons[key] = btn
            self._nav_group.addButton(btn)
            nav_layout.addWidget(btn)

        layout.addWidget(nav_area)
        layout.addStretch(1)

        if "projects" in self._nav_buttons:
            self._nav_buttons["projects"].setChecked(True)
        self._refresh_sidebar_nav_icons()

        return sidebar

    def _build_workspace_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("workspacePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("workspaceHeader")
        self._workspace_header = header
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(28, 26, 28, 22)
        header_layout.setSpacing(8)

        eyebrow = QLabel("AUTO CAPCUT")
        eyebrow.setObjectName("workspaceEyebrow")
        self._workspace_title_label = QLabel("Projects")
        self._workspace_title_label.setObjectName("workspaceTitle")
        self._workspace_description_label = QLabel(
            "Manage parent projects and child videos from one structured workspace."
        )
        self._workspace_description_label.setObjectName("workspaceDescription")
        self._workspace_description_label.setWordWrap(True)
        header_layout.addWidget(eyebrow)
        header_layout.addWidget(self._workspace_title_label)
        header_layout.addWidget(self._workspace_description_label)
        layout.addWidget(header)

        self._workspace_stack = QStackedWidget()
        self._workspace_stack.setObjectName("workspaceStack")
        self._workspace_stack.addWidget(self._build_projects_workspace())
        self._workspace_stack.addWidget(self._build_render_dashboard_workspace())
        self._workspace_stack.addWidget(
            self._build_workspace_page(
                title="Analytics",
                description="Metrics, throughput, and trend review will land in this workspace.",
                note="Next step: add the first analytics overview cards and charts.",
            )
        )
        layout.addWidget(self._workspace_stack, 1)

        return panel

    def _build_projects_workspace(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(18)

        toolbar = QWidget()
        toolbar.setObjectName("projectsToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(20, 18, 20, 18)
        toolbar_layout.setSpacing(12)

        title_wrap = QVBoxLayout()
        title_wrap.setContentsMargins(0, 0, 0, 0)
        title_wrap.setSpacing(4)
        section_title = QLabel("Parent projects")
        section_title.setObjectName("workspaceSectionTitle")
        section_body = QLabel("Manage your top-level project groups before assigning child videos into them.")
        section_body.setObjectName("workspaceBody")
        section_body.setWordWrap(True)
        title_wrap.addWidget(section_title)
        title_wrap.addWidget(section_body)
        toolbar_layout.addLayout(title_wrap, 1)

        self.project_preset_add_btn = QPushButton("Add")
        self.project_preset_add_btn.setObjectName("projectActionBtn")
        self.project_preset_add_btn.setProperty("variant", "primary")
        self.project_preset_add_btn.setIcon(self._make_symbol_icon("add", "#F8FAFC"))
        self.project_preset_add_btn.setIconSize(QSize(16, 16))
        self.project_preset_add_btn.clicked.connect(self._handle_project_preset_add)

        self.project_parent_edit_btn = QPushButton("Edit")
        self.project_parent_edit_btn.setObjectName("projectActionBtn")
        self.project_parent_edit_btn.setProperty("variant", "secondary")
        self.project_parent_edit_btn.setIcon(self._make_symbol_icon("edit", "#1F1F1F"))
        self.project_parent_edit_btn.setIconSize(QSize(16, 16))
        self.project_parent_edit_btn.clicked.connect(
            lambda: self._handle_project_preset_edit(self._selected_project_preset_id)
        )

        self.project_parent_delete_btn = QPushButton("Delete")
        self.project_parent_delete_btn.setObjectName("projectActionBtn")
        self.project_parent_delete_btn.setProperty("variant", "danger")
        self.project_parent_delete_btn.setIcon(self._make_symbol_icon("delete", "#C5221F"))
        self.project_parent_delete_btn.setIconSize(QSize(16, 16))
        self.project_parent_delete_btn.clicked.connect(
            lambda: self._handle_project_preset_delete(self._selected_project_preset_id)
        )

        toolbar_layout.addWidget(self.project_preset_add_btn)
        toolbar_layout.addWidget(self.project_parent_edit_btn)
        toolbar_layout.addWidget(self.project_parent_delete_btn)
        layout.addWidget(toolbar)

        content_row = QHBoxLayout()
        content_row.setSpacing(18)

        list_card = QFrame()
        list_card.setObjectName("projectsListCard")
        list_card.setMinimumWidth(320)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(16, 16, 16, 16)
        list_layout.setSpacing(12)

        list_heading = QLabel("Project parents")
        list_heading.setObjectName("projectsPanelTitle")
        list_layout.addWidget(list_heading)

        self.project_parent_list = QListWidget()
        self.project_parent_list.setObjectName("projectParentList")
        self.project_parent_list.currentItemChanged.connect(self._handle_project_parent_selection_changed)
        list_layout.addWidget(self.project_parent_list, 1)

        self.project_parent_empty_label = QLabel("No parent projects yet. Use Add to create the first one.")
        self.project_parent_empty_label.setObjectName("hintLabel")
        self.project_parent_empty_label.setWordWrap(True)
        list_layout.addWidget(self.project_parent_empty_label)

        detail_card = QFrame()
        detail_card.setObjectName("projectsDetailCard")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(20, 20, 20, 20)
        detail_layout.setSpacing(12)

        detail_heading = QLabel("Details")
        detail_heading.setObjectName("projectsPanelTitle")
        detail_layout.addWidget(detail_heading)

        self.project_parent_detail_name = QLabel("Select a parent project")
        self.project_parent_detail_name.setObjectName("projectDetailTitle")
        detail_layout.addWidget(self.project_parent_detail_name)

        self.project_parent_detail_meta = QLabel()
        self.project_parent_detail_meta.setVisible(False)

        self.project_parent_detail_stats = QLabel("Child projects: 0")
        self.project_parent_detail_stats.setObjectName("projectDetailStats")
        detail_layout.addWidget(self.project_parent_detail_stats)

        self.project_parent_open_children_btn = QPushButton("Open child projects")
        self.project_parent_open_children_btn.setObjectName("projectActionBtn")
        self.project_parent_open_children_btn.setProperty("variant", "secondary")
        self.project_parent_open_children_btn.setIcon(self._make_symbol_icon("children", "#1F1F1F"))
        self.project_parent_open_children_btn.setIconSize(QSize(16, 16))
        self.project_parent_open_children_btn.clicked.connect(
            lambda: self._open_project_children_dialog(self._selected_project_preset_id)
        )
        detail_layout.addWidget(self.project_parent_open_children_btn, 0, Qt.AlignmentFlag.AlignLeft)
        detail_layout.addStretch(1)

        content_row.addWidget(list_card, 4)
        content_row.addWidget(detail_card, 6)
        layout.addLayout(content_row, 1)
        layout.addStretch(1)
        return page

    def _build_child_videos_page(
        self,
        preset_id: str,
        on_children_changed: Callable[[], None],
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        toolbar = QWidget()
        toolbar.setObjectName("projectsToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(18, 16, 18, 16)
        toolbar_layout.setSpacing(12)

        title_wrap = QVBoxLayout()
        title_wrap.setContentsMargins(0, 0, 0, 0)
        title_wrap.setSpacing(4)
        heading = QLabel("Child projects")
        heading.setObjectName("projectsPanelTitle")
        subheading = QLabel("Create and manage child projects under this parent.")
        subheading.setObjectName("workspaceBody")
        subheading.setWordWrap(True)
        title_wrap.addWidget(heading)
        title_wrap.addWidget(subheading)
        toolbar_layout.addLayout(title_wrap, 1)

        add_btn = QPushButton("Create project")
        add_btn.setObjectName("projectActionBtn")
        add_btn.setProperty("variant", "primary")
        add_btn.setIcon(self._make_symbol_icon("add", "#FFFFFF"))
        add_btn.setIconSize(QSize(16, 16))

        select_done_btn = QPushButton("Done")
        select_done_btn.setObjectName("projectActionBtn")
        select_done_btn.setProperty("variant", "secondary")
        select_done_btn.setVisible(False)

        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("projectActionBtn")
        delete_btn.setProperty("variant", "danger")
        delete_btn.setIcon(self._make_symbol_icon("delete", "#C5221F"))
        delete_btn.setIconSize(QSize(16, 16))
        delete_btn.setEnabled(False)

        toolbar_layout.addWidget(add_btn)
        toolbar_layout.addWidget(select_done_btn)
        toolbar_layout.addWidget(delete_btn)
        layout.addWidget(toolbar)

        list_card = QFrame()
        list_card.setObjectName("projectsListCard")
        list_card.setMinimumWidth(500)
        list_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(18, 18, 18, 18)
        list_layout.setSpacing(12)

        list_heading = QLabel("Child project list")
        list_heading.setObjectName("projectsPanelTitle")
        list_layout.addWidget(list_heading)

        table = QTableWidget()
        table.setObjectName("projectChildrenTable")
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["No.", "Project", "Status"])
        table.setAlternatingRowColors(False)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(52)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setSectionResizeMode(0, header.ResizeMode.Fixed)
        table.setColumnWidth(0, 72)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        header.setSectionResizeMode(2, header.ResizeMode.Fixed)
        table.setColumnWidth(2, 120)

        list_layout.addWidget(table, 1)

        detail_card = QFrame()
        detail_card.setObjectName("projectsDetailCard")
        detail_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(24, 24, 24, 24)
        detail_layout.setSpacing(0)

        detail_heading = QLabel("Project details")
        detail_heading.setObjectName("projectsPanelTitle")
        detail_layout.addWidget(detail_heading)

        detail_selected_label = QLabel()  # kept for internal state, hidden
        detail_selected_label.setVisible(False)

        detail_layout.addSpacing(16)

        title_group = QVBoxLayout()
        title_group.setSpacing(4)
        title_label = QLabel("Title")
        title_label.setObjectName("sectionLabel")
        title_edit = QLineEdit()
        title_edit.setPlaceholderText("Enter video title")
        title_group.addWidget(title_label)
        title_group.addWidget(title_edit)
        detail_layout.addLayout(title_group)

        detail_layout.addSpacing(12)

        desc_group = QVBoxLayout()
        desc_group.setSpacing(4)
        description_label = QLabel("Description")
        description_label.setObjectName("sectionLabel")
        description_edit = QTextEdit()
        description_edit.setPlaceholderText("Enter description for this child project...")
        description_edit.setFixedHeight(120)
        desc_group.addWidget(description_label)
        desc_group.addWidget(description_edit)
        detail_layout.addLayout(desc_group)

        detail_layout.addSpacing(16)

        # ── Folder section ──────────────────────────────────────────
        RESOURCE_FOLDERS = ["image", "thumb", "heygen", "caption"]

        folder_group = QVBoxLayout()
        folder_group.setSpacing(8)
        folder_label = QLabel("Project folder")
        folder_label.setObjectName("sectionLabel")

        # Row 1: path input + 2 buttons
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_edit = QLineEdit()
        folder_edit.setPlaceholderText("No folder selected…")
        folder_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(100)
        scan_btn = QPushButton("Scan & Create")
        scan_btn.setProperty("variant", "primary")
        scan_btn.setFixedWidth(140)
        scan_btn.setEnabled(False)
        folder_row.addWidget(folder_edit, 1)
        folder_row.addWidget(browse_btn)
        folder_row.addWidget(scan_btn)

        # Row 2: badges hiển thị trạng thái từng sub-folder
        folder_status_row = QHBoxLayout()
        folder_status_row.setSpacing(6)
        folder_status_labels: dict[str, QLabel] = {}
        for fname in RESOURCE_FOLDERS:
            badge = QLabel(f"📁 {fname}")
            badge.setObjectName("folderBadgeNone")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedHeight(24)
            folder_status_labels[fname] = badge
            folder_status_row.addWidget(badge)
        folder_status_row.addStretch(1)

        folder_group.addWidget(folder_label)
        folder_group.addLayout(folder_row)
        folder_group.addLayout(folder_status_row)
        detail_layout.addLayout(folder_group)

        detail_layout.addStretch(1)

        detail_hint_label = QLabel()  # kept for internal state updates, hidden
        detail_hint_label.setVisible(False)

        detail_actions = QHBoxLayout()
        detail_actions.addStretch(1)
        save_detail_btn = QPushButton("Save details")
        save_detail_btn.setProperty("variant", "primary")
        save_detail_btn.setEnabled(False)
        detail_actions.addWidget(save_detail_btn)
        detail_layout.addLayout(detail_actions)

        content_wrap = QWidget()
        content_layout = QHBoxLayout(content_wrap)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)
        content_layout.addWidget(list_card, 5)
        content_layout.addWidget(detail_card, 7)

        def current_child_project() -> dict[str, object] | None:
            row = table.currentRow()
            if row < 0:
                return None
            item = table.item(row, 1)
            if item is None:
                return None
            child_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not child_id:
                return None
            for child in self._manual_child_projects_for_parent(preset_id):
                if str(child.get("id", "")).strip() == child_id:
                    return child
            return None

        detail_loading = False
        detail_dirty = False
        loaded_child_id = ""
        bulk_select_mode = False
        checked_child_ids: set[str] = set()

        def _set_square_state(button: QPushButton, checked: bool) -> None:
            button.setChecked(checked)
            button.setText("✓" if checked else "")

        def checked_child_ids_list() -> list[str]:
            return list(checked_child_ids)

        def _set_bulk_mode(enabled: bool) -> None:
            nonlocal bulk_select_mode
            bulk_select_mode = enabled
            if not enabled:
                checked_child_ids.clear()
            table.setHorizontalHeaderLabels(["All" if enabled else "No.", "Project", "Status"])
            select_done_btn.setVisible(enabled)
            refresh_actions()

        def _refresh_folder_badges(path: str) -> None:
            """Update badge colours based on which sub-folders exist on disk."""
            import os
            for fname in RESOURCE_FOLDERS:
                badge = folder_status_labels[fname]
                if not path:
                    badge.setText(f"📁 {fname}")
                    badge.setObjectName("folderBadgeNone")
                elif os.path.isdir(os.path.join(path, fname)):
                    badge.setText(f"✓ {fname}")
                    badge.setObjectName("folderBadgeOk")
                else:
                    badge.setText(f"✕ {fname}")
                    badge.setObjectName("folderBadgeMissing")
                badge.style().unpolish(badge)
                badge.style().polish(badge)
            scan_btn.setEnabled(bool(path))

        def set_detail_enabled(enabled: bool) -> None:
            for widget in (title_edit, description_edit):
                widget.setEnabled(enabled)
            save_detail_btn.setEnabled(enabled and detail_dirty)
            browse_btn.setEnabled(enabled)

        def load_child_detail(child: dict[str, object] | None) -> None:
            nonlocal detail_loading, detail_dirty, loaded_child_id
            detail_loading = True
            if child is None:
                loaded_child_id = ""
                detail_selected_label.setText("Select a child project")
                title_edit.clear()
                description_edit.clear()
                folder_edit.clear()
                detail_hint_label.setText("Select a child project to edit.")
                detail_dirty = False
                set_detail_enabled(False)
                _refresh_folder_badges("")
                detail_loading = False
                return
            loaded_child_id = str(child.get("id", "")).strip()
            detail_selected_label.setText(str(child.get("name", "")).strip())
            title_edit.setText(str(child.get("title", "")).strip())
            description_edit.setPlainText(str(child.get("description", "")).strip())
            fp = str(child.get("folder_path", "")).strip()
            folder_edit.setText(fp)
            _refresh_folder_badges(fp)
            detail_hint_label.setText("Data is saved per child project.")
            detail_dirty = False
            set_detail_enabled(True)
            detail_loading = False

        def persist_child_detail(*, refresh_table: bool = True) -> None:
            nonlocal detail_dirty
            if not loaded_child_id:
                return
            children = self._manual_child_projects_for_parent(preset_id)
            for index, item in enumerate(children):
                if str(item.get("id", "")).strip() != loaded_child_id:
                    continue
                updated = dict(item)
                updated["title"] = title_edit.text().strip()
                updated["description"] = description_edit.toPlainText().strip()
                updated["folder_path"] = folder_edit.text().strip()
                children[index] = self._normalize_manual_child_project(updated) or updated
                break
            self.manual_child_projects_by_parent[str(preset_id).strip()] = children
            self._save_project_dashboard_settings()
            detail_dirty = False
            save_detail_btn.setEnabled(False)
            detail_hint_label.setText("Changes saved.")
            if refresh_table:
                populate_children_table(preferred_child_id=loaded_child_id)

        def refresh_actions() -> None:
            if bulk_select_mode:
                checked_count = len(checked_child_ids)
                delete_btn.setEnabled(checked_count > 0)
                delete_btn.setText(f"Delete ({checked_count})" if checked_count else "Delete")
                return
            delete_btn.setEnabled(current_child_project() is not None)
            delete_btn.setText("Delete")

        def _toggle_checked_child(child_id: str, checked: bool) -> None:
            if checked:
                checked_child_ids.add(child_id)
            else:
                checked_child_ids.discard(child_id)
            refresh_actions()

        def _handle_no_header_click() -> None:
            preferred_child_id = loaded_child_id or (
                str(current_child_project().get("id", "")).strip() if current_child_project() else ""
            )
            if not bulk_select_mode:
                _set_bulk_mode(True)
                populate_children_table(preferred_child_id=preferred_child_id)
                return
            all_child_ids = {
                str(child.get("id", "")).strip()
                for child in self._manual_child_projects_for_parent(preset_id)
                if str(child.get("id", "")).strip()
            }
            if checked_child_ids and checked_child_ids == all_child_ids:
                checked_child_ids.clear()
            else:
                checked_child_ids.clear()
                checked_child_ids.update(all_child_ids)
            populate_children_table(preferred_child_id=preferred_child_id)

        def populate_children_table(preferred_child_id: str = "") -> None:
            children = sorted(
                self._manual_child_projects_for_parent(preset_id),
                key=lambda item: str(item.get("name", "")).casefold(),
            )
            table.setRowCount(len(children))
            table.clearSelection()
            selected_row = -1
            for row, child in enumerate(children):
                child_id = str(child.get("id", "")).strip()
                match = re.search(r"(\d+)$", str(child.get("name", "")).strip())
                order_text = match.group(1) if match else str(row + 1)
                if bulk_select_mode:
                    order_item = QTableWidgetItem("")
                    order_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    table.setItem(row, 0, order_item)

                    select_btn = QPushButton()
                    select_btn.setObjectName("rowSelectSquare")
                    select_btn.setCheckable(True)
                    select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    select_btn.setFixedSize(18, 18)
                    _set_square_state(select_btn, child_id in checked_child_ids)
                    select_btn.toggled.connect(
                        lambda checked, cid=child_id, btn=select_btn: (
                            _set_square_state(btn, checked),
                            _toggle_checked_child(cid, checked),
                        )
                    )
                    select_widget = QWidget()
                    select_layout = QHBoxLayout(select_widget)
                    select_layout.setContentsMargins(0, 0, 0, 0)
                    select_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    select_layout.addWidget(select_btn)
                    table.setCellWidget(row, 0, select_widget)
                else:
                    table.setCellWidget(row, 0, None)
                    order_item = QTableWidgetItem(order_text)
                    order_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    order_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    table.setItem(row, 0, order_item)

                name_item = QTableWidgetItem(str(child.get("name", "")).strip())
                name_item.setData(Qt.ItemDataRole.UserRole, child_id)
                name_item.setToolTip(
                    "Title: "
                    + (str(child.get("title", "")).strip() or "(empty)")
                    + "\nDescription: "
                    + (str(child.get("description", "")).strip() or "(empty)")
                )
                # ── col 2: status pill widget ─────────────────────────────
                status_key = str(child.get("status", "draft"))
                status_text = self._manual_child_status_label(status_key)
                status_colors = {
                    "draft":       ("#F1F5F9", "#64748B"),
                    "in_progress": ("#FEF3C7", "#D97706"),
                    "done":        ("#DCFCE7", "#16A34A"),
                    "published":   ("#DBEAFE", "#2563EB"),
                }
                bg, fg = status_colors.get(status_key, ("#F1F5F9", "#64748B"))
                pill = QLabel(status_text)
                pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pill.setStyleSheet(
                    f"QLabel {{ background: {bg}; color: {fg}; border-radius: 10px;"
                    f" font-size: 11px; font-weight: 600; padding: 3px 10px; }}"
                )
                pill_widget = QWidget()
                pill_lay = QHBoxLayout(pill_widget)
                pill_lay.setContentsMargins(6, 0, 6, 0)
                pill_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pill_lay.addWidget(pill)

                name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                status_placeholder = QTableWidgetItem()
                status_placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                table.setItem(row, 1, name_item)
                table.setItem(row, 2, status_placeholder)
                table.setCellWidget(row, 2, pill_widget)
                if child_id == preferred_child_id:
                    selected_row = row
            if table.rowCount():
                table.selectRow(selected_row if selected_row >= 0 else 0)
            else:
                load_child_detail(None)
            refresh_actions()

        def handle_add_child() -> None:
            payload = self._open_manual_child_project_editor(preset_id)
            if payload is None:
                return
            children = self._manual_child_projects_for_parent(preset_id)
            children.append(payload)
            self.manual_child_projects_by_parent[str(preset_id).strip()] = children
            self._save_project_dashboard_settings()
            self._refresh_projects_workspace(preset_id)
            populate_children_table(str(payload.get("id", "")).strip())
            on_children_changed()

        def handle_edit_child() -> None:
            nonlocal detail_dirty
            child = current_child_project()
            if child is None:
                return
            if detail_dirty:
                persist_child_detail()
            payload = self._open_manual_child_project_editor(preset_id, initial=child)
            if payload is None:
                return
            children = self._manual_child_projects_for_parent(preset_id)
            child_id = str(child.get("id", "")).strip()
            for index, item in enumerate(children):
                if str(item.get("id", "")).strip() == child_id:
                    children[index] = payload
                    break
            self.manual_child_projects_by_parent[str(preset_id).strip()] = children
            self._save_project_dashboard_settings()
            populate_children_table(str(payload.get("id", "")).strip())
            on_children_changed()

        def handle_delete_child() -> None:
            ids_to_delete = checked_child_ids_list() if bulk_select_mode else []
            if not ids_to_delete:
                child = current_child_project()
                if child is None:
                    return
                ids_to_delete = [str(child.get("id", "")).strip()]

            count = len(ids_to_delete)
            all_children = self._manual_child_projects_for_parent(preset_id)
            if count == 1:
                single = next(
                    (c for c in all_children if str(c.get("id", "")).strip() == ids_to_delete[0]),
                    None,
                )
                msg = f"Delete '{str(single.get('name', '')).strip()}'?" if single else "Delete selected project?"
            else:
                msg = f"Delete {count} selected projects? This cannot be undone."
            reply = QMessageBox.question(
                self,
                "Remove child project" if count == 1 else "Remove child projects",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            id_set = set(ids_to_delete)
            self.manual_child_projects_by_parent[str(preset_id).strip()] = [
                item
                for item in self._manual_child_projects_for_parent(preset_id)
                if str(item.get("id", "")).strip() not in id_set
            ]
            checked_child_ids.clear()
            self._save_project_dashboard_settings()
            self._refresh_projects_workspace(preset_id)
            populate_children_table()
            on_children_changed()

        def mark_detail_dirty() -> None:
            nonlocal detail_dirty
            if detail_loading or current_child_project() is None:
                return
            detail_dirty = True
            save_detail_btn.setEnabled(True)
            detail_hint_label.setText("Unsaved changes.")

        def handle_child_selection_changed() -> None:
            if detail_dirty and loaded_child_id:
                persist_child_detail(refresh_table=False)
            child = current_child_project()
            load_child_detail(child)
            refresh_actions()

        def handle_child_double_click() -> None:
            child = current_child_project()
            if child is None:
                return
            self._open_manual_child_project_workspace(
                preset_id,
                str(child.get("id", "")).strip(),
                on_saved=lambda: (
                    populate_children_table(preferred_child_id=str(child.get("id", "")).strip()),
                    on_children_changed(),
                ),
            )

        def handle_browse_folder() -> None:
            from PySide6.QtWidgets import QFileDialog
            current = folder_edit.text().strip()
            start = current if current else str(Path.home())
            chosen = QFileDialog.getExistingDirectory(
                None, "Select folder for child project", start
            )
            if not chosen:
                return
            folder_edit.setText(chosen)
            _refresh_folder_badges(chosen)
            mark_detail_dirty()

        def handle_scan_folders() -> None:
            import os
            path = folder_edit.text().strip()
            if not path or not os.path.isdir(path):
                QMessageBox.warning(None, "Invalid folder", f"Folder not found:\n{path}")
                return
            created, existed = [], []
            for fname in RESOURCE_FOLDERS:
                sub = os.path.join(path, fname)
                if os.path.isdir(sub):
                    existed.append(fname)
                else:
                    os.makedirs(sub)
                    created.append(fname)
            _refresh_folder_badges(path)
            persist_child_detail(refresh_table=False)
            parts = []
            if created:
                parts.append(f"✅ Created: {', '.join(created)}")
            if existed:
                parts.append(f"⬜ Already exists: {', '.join(existed)}")
            QMessageBox.information(None, "Done", "\n".join(parts))

        add_btn.clicked.connect(handle_add_child)
        delete_btn.clicked.connect(handle_delete_child)
        table.itemSelectionChanged.connect(handle_child_selection_changed)
        table.itemDoubleClicked.connect(lambda _item: handle_child_double_click())
        header.sectionClicked.connect(lambda section: _handle_no_header_click() if section == 0 else None)
        select_done_btn.clicked.connect(
            lambda: (
                _set_bulk_mode(False),
                populate_children_table(preferred_child_id=loaded_child_id),
            )
        )
        title_edit.textChanged.connect(mark_detail_dirty)
        description_edit.textChanged.connect(mark_detail_dirty)
        save_detail_btn.clicked.connect(persist_child_detail)
        browse_btn.clicked.connect(handle_browse_folder)
        scan_btn.clicked.connect(handle_scan_folders)

        _set_bulk_mode(False)
        populate_children_table()

        layout.addWidget(content_wrap, 1)
        return page

    def _build_child_tool_placeholder_page(self, title: str, description: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("workspaceEmptyCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("workspaceSectionTitle")
        body_label = QLabel(description)
        body_label.setObjectName("workspaceBody")
        body_label.setWordWrap(True)
        note_label = QLabel("Next step: wire this tool to the selected child videos.")
        note_label.setObjectName("workspaceEmptyNote")
        note_label.setWordWrap(True)

        card_layout.addWidget(title_label)
        card_layout.addWidget(body_label)
        card_layout.addWidget(note_label)
        card_layout.addStretch(1)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_workspace_page(
        self,
        *,
        title: str,
        description: str,
        note: str,
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(0)

        empty_card = QFrame()
        empty_card.setObjectName("workspaceEmptyCard")
        empty_layout = QVBoxLayout(empty_card)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        empty_layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("workspaceSectionTitle")
        body_label = QLabel(description)
        body_label.setObjectName("workspaceBody")
        body_label.setWordWrap(True)
        note_label = QLabel(note)
        note_label.setObjectName("workspaceEmptyNote")
        note_label.setWordWrap(True)

        empty_layout.addWidget(title_label)
        empty_layout.addWidget(body_label)
        empty_layout.addSpacing(10)
        empty_layout.addWidget(note_label)
        empty_layout.addStretch(1)

        layout.addWidget(empty_card)
        layout.addStretch(1)
        return page

    def _build_render_dashboard_workspace(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        render_panel = self._build_project_panel()
        render_panel.setObjectName("projectPanel")
        layout.addWidget(render_panel, 1)
        return page

    def _switch_workspace(self, key: str) -> None:
        index_map = {
            "projects": 0,
            "render_dashboard": 1,
            "analytics": 2,
        }
        titles = {
            "projects": (
                "Projects",
                "Manage parent projects and child videos from one structured workspace.",
            ),
            "render_dashboard": (
                "Render Dashboard",
                "Track render queues, live progress, failures, and operator actions.",
            ),
            "analytics": (
                "Analytics",
                "Review output volume, success rate, and performance trends over time.",
            ),
        }
        if self._workspace_stack is not None:
            self._workspace_stack.setCurrentIndex(index_map.get(key, 0))
        title, description = titles.get(key, titles["projects"])
        if self._workspace_header is not None:
            self._workspace_header.setVisible(key != "render_dashboard")
        if self._workspace_title_label is not None:
            self._workspace_title_label.setText(title)
        if self._workspace_description_label is not None:
            self._workspace_description_label.setText(description)
        self._refresh_sidebar_nav_icons()

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
        self._tool_stack.addWidget(self._build_project_presets_tab())     # 3
        self._tool_stack.addWidget(self._build_remove_background_tab())   # 4
        self._tool_stack.addWidget(self._build_rename_tab())              # 5
        self._tool_stack.addWidget(self._build_raw_seo_tab())             # 6
        self._tool_stack.addWidget(self._build_srt_generator_tab())       # 7
        self._tool_stack.addWidget(self._build_roxy_upload_tab())         # 8
        layout.addWidget(self._tool_stack, 1)

        return panel

    def _switch_tool(self, key: str) -> None:
        """Switch the visible tool panel page and update the header title."""
        index_map = {
            "animation": 0,
            "effect":    1,
            "transition": 2,
            "project":   3,
            "remove_bg": 4,
            "rename":    5,
            "raw_seo":   6,
            "srt":       7,
            "roxy_upload": 8,
        }
        titles = {
            "animation": "Animation",
            "effect": "Effects",
            "transition": "Transitions",
            "project": "Project",
            "remove_bg":  "Remove Background",
            "rename": "Rename Files",
            "raw_seo": "Raw SEO",
            "srt": "SRT Generator",
            "roxy_upload": "Upload Roxy",
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

    def _build_project_presets_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        info = QLabel("Project cha hiển thị dạng card; mỗi card chứa bảng video con theo phong cách CapCut.")
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        actions = QHBoxLayout()
        self.project_preset_add_btn = QPushButton("Add Project")
        self.project_preset_add_btn.setProperty("variant", "secondary")
        self.project_preset_add_btn.clicked.connect(self._handle_project_preset_add)
        actions.addWidget(self.project_preset_add_btn)
        self.project_hierarchy_refresh_btn = QPushButton("Refresh")
        self.project_hierarchy_refresh_btn.setProperty("variant", "secondary")
        self.project_hierarchy_refresh_btn.clicked.connect(lambda: self._refresh_project_preset_tab())
        actions.addWidget(self.project_hierarchy_refresh_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.project_hierarchy_scroll = QScrollArea()
        self.project_hierarchy_scroll.setObjectName("projectHierarchyScroll")
        self.project_hierarchy_scroll.setWidgetResizable(True)
        self.project_hierarchy_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.project_hierarchy_root = QWidget()
        self.project_hierarchy_root.setObjectName("projectHierarchyRoot")
        self.project_hierarchy_layout = QVBoxLayout(self.project_hierarchy_root)
        self.project_hierarchy_layout.setContentsMargins(4, 4, 4, 4)
        self.project_hierarchy_layout.setSpacing(10)
        self.project_hierarchy_scroll.setWidget(self.project_hierarchy_root)
        layout.addWidget(self.project_hierarchy_scroll, 1)

        self._refresh_project_preset_tab()
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

    def _build_raw_seo_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        info = QLabel(
            "Apply basic SEO metadata (title, description, keywords) to media files using ExifTool."
        )
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        files_label = QLabel("Media Files")
        files_label.setObjectName("sectionLabel")
        layout.addWidget(files_label)

        file_row = QHBoxLayout()
        self.raw_seo_files_edit = QLineEdit()
        self.raw_seo_files_edit.setPlaceholderText("Select one or more files…")
        self.raw_seo_files_edit.setReadOnly(True)
        self.raw_seo_files_edit.setObjectName("assetPath")
        file_row.addWidget(self.raw_seo_files_edit, 1)

        self.raw_seo_browse_btn = QPushButton("Browse Files")
        self.raw_seo_browse_btn.setProperty("variant", "secondary")
        self.raw_seo_browse_btn.clicked.connect(self._handle_select_raw_seo_files)
        file_row.addWidget(self.raw_seo_browse_btn)

        self.raw_seo_clear_btn = QPushButton("Clear")
        self.raw_seo_clear_btn.setProperty("variant", "secondary")
        self.raw_seo_clear_btn.clicked.connect(self._handle_clear_raw_seo_files)
        file_row.addWidget(self.raw_seo_clear_btn)
        layout.addLayout(file_row)

        self.raw_seo_file_list = QListWidget()
        self.raw_seo_file_list.setObjectName("presetList")
        self.raw_seo_file_list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.raw_seo_file_list, 1)

        editor_row = QHBoxLayout()
        self.raw_seo_open_editor_btn = QPushButton("Edit Metadata Fields")
        self.raw_seo_open_editor_btn.setProperty("variant", "secondary")
        self.raw_seo_open_editor_btn.clicked.connect(self._open_raw_seo_editor)
        editor_row.addWidget(self.raw_seo_open_editor_btn)

        self.raw_seo_clear_metadata_btn = QPushButton("Reset Fields")
        self.raw_seo_clear_metadata_btn.setProperty("variant", "secondary")
        self.raw_seo_clear_metadata_btn.clicked.connect(self._clear_raw_seo_metadata_draft)
        editor_row.addWidget(self.raw_seo_clear_metadata_btn)
        layout.addLayout(editor_row)

        self.raw_seo_draft_summary_label = QLabel("")
        self.raw_seo_draft_summary_label.setObjectName("hintLabel")
        self.raw_seo_draft_summary_label.setWordWrap(True)
        layout.addWidget(self.raw_seo_draft_summary_label)
        self._update_raw_seo_draft_summary()

        self.raw_seo_strict_check = QCheckBox("Strict mode (verify metadata after write)")
        self.raw_seo_strict_check.setChecked(True)
        layout.addWidget(self.raw_seo_strict_check)

        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(10)
        action_grid.setVerticalSpacing(8)
        self.raw_seo_apply_btn = QPushButton("Apply Raw SEO")
        self.raw_seo_apply_btn.setProperty("variant", "primary")
        self.raw_seo_apply_btn.clicked.connect(self._handle_raw_seo_apply)
        action_grid.addWidget(self.raw_seo_apply_btn, 0, 0, 1, 2)

        self.raw_seo_export_btn = QPushButton("Export JSON Payload")
        self.raw_seo_export_btn.setProperty("variant", "secondary")
        self.raw_seo_export_btn.clicked.connect(self._handle_raw_seo_export_payload)
        action_grid.addWidget(self.raw_seo_export_btn, 1, 0)

        self.raw_seo_view_btn = QPushButton("View Metadata")
        self.raw_seo_view_btn.setProperty("variant", "secondary")
        self.raw_seo_view_btn.clicked.connect(self._handle_raw_seo_view_metadata)
        action_grid.addWidget(self.raw_seo_view_btn, 1, 1)
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        layout.addLayout(action_grid)

        self.raw_seo_result_label = QLabel("")
        self.raw_seo_result_label.setObjectName("hintLabel")
        self.raw_seo_result_label.setWordWrap(True)
        layout.addWidget(self.raw_seo_result_label)
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

    def _build_roxy_upload_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        info = QLabel(
            "Upload one SEO-processed video to YouTube Studio via a selected Roxy anti-detect profile."
        )
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        api_label = QLabel("Roxy API")
        api_label.setObjectName("sectionLabel")
        layout.addWidget(api_label)

        host_row = QHBoxLayout()
        self.roxy_api_host_edit = QLineEdit()
        self.roxy_api_host_edit.setPlaceholderText("http://127.0.0.1:50000")
        self.roxy_api_host_edit.setText(
            self._settings.value("roxy/api_host", "http://127.0.0.1:50000", type=str)
        )
        host_row.addWidget(self.roxy_api_host_edit, 1)
        layout.addLayout(host_row)

        token_row = QHBoxLayout()
        self.roxy_api_key_edit = QLineEdit()
        self.roxy_api_key_edit.setPlaceholderText("Roxy API key/token")
        self.roxy_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.roxy_api_key_edit.setText(self._settings.value("roxy/api_key", "", type=str))
        token_row.addWidget(self.roxy_api_key_edit, 1)
        layout.addLayout(token_row)

        self.roxy_rate_limit_label = QLabel("")
        self.roxy_rate_limit_label.setObjectName("hintLabel")
        self.roxy_rate_limit_label.setWordWrap(True)
        layout.addWidget(self.roxy_rate_limit_label)
        self._start_roxy_rate_timer()
        self._refresh_roxy_rate_limit_label()

        workspace_label = QLabel("Workspace + Profile")
        workspace_label.setObjectName("sectionLabel")
        layout.addWidget(workspace_label)

        workspace_row = QHBoxLayout()
        self.roxy_workspace_spin = QSpinBox()
        self.roxy_workspace_spin.setRange(1, 1_000_000)
        raw_workspace_id = self._settings.value("roxy/workspace_id", 1)
        try:
            workspace_id = int(raw_workspace_id)
        except (TypeError, ValueError):
            workspace_id = 1
        self.roxy_workspace_spin.setValue(max(1, workspace_id))
        workspace_row.addWidget(QLabel("Workspace ID"))
        workspace_row.addWidget(self.roxy_workspace_spin)
        workspace_row.addStretch(1)
        layout.addLayout(workspace_row)

        profile_row = QHBoxLayout()
        self.roxy_profile_combo = QComboBox()
        self.roxy_profile_combo.setObjectName("profileCombo")
        self.roxy_profile_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.roxy_profile_combo.currentIndexChanged.connect(self._handle_roxy_profile_changed)
        profile_row.addWidget(self.roxy_profile_combo, 1)

        self.roxy_load_profiles_btn = QPushButton("Load Profiles")
        self.roxy_load_profiles_btn.setProperty("variant", "secondary")
        self.roxy_load_profiles_btn.clicked.connect(self._handle_roxy_load_profiles)
        profile_row.addWidget(self.roxy_load_profiles_btn)
        layout.addLayout(profile_row)

        self.roxy_profile_id_edit = QLineEdit()
        self.roxy_profile_id_edit.setPlaceholderText("Optional: paste profile dirId manually")
        self.roxy_profile_id_edit.setText(self._settings.value("roxy/profile_id", "", type=str))
        layout.addWidget(self.roxy_profile_id_edit)

        video_label = QLabel("Video File")
        video_label.setObjectName("sectionLabel")
        layout.addWidget(video_label)

        video_row = QHBoxLayout()
        self.roxy_video_path_edit = QLineEdit()
        self.roxy_video_path_edit.setPlaceholderText("Select video to upload...")
        self.roxy_video_path_edit.setReadOnly(True)
        self.roxy_video_path_edit.setObjectName("assetPath")
        video_row.addWidget(self.roxy_video_path_edit, 1)

        self.roxy_video_browse_btn = QPushButton("Browse Video")
        self.roxy_video_browse_btn.setProperty("variant", "secondary")
        self.roxy_video_browse_btn.clicked.connect(self._handle_roxy_select_video)
        video_row.addWidget(self.roxy_video_browse_btn)
        layout.addLayout(video_row)

        self.roxy_close_profile_check = QCheckBox("Close profile right after upload starts")
        self.roxy_close_profile_check.setChecked(False)
        layout.addWidget(self.roxy_close_profile_check)

        self.roxy_upload_btn = QPushButton("Upload via Roxy")
        self.roxy_upload_btn.setProperty("variant", "primary")
        self.roxy_upload_btn.clicked.connect(self._handle_roxy_upload)
        layout.addWidget(self.roxy_upload_btn)

        self.roxy_result_label = QLabel("")
        self.roxy_result_label.setObjectName("hintLabel")
        self.roxy_result_label.setWordWrap(True)
        layout.addWidget(self.roxy_result_label)

        last_video_path = self._settings.value("roxy/video_path", "", type=str)
        if last_video_path:
            self._set_roxy_video_path(Path(last_video_path))

        # Load profiles once when tab is created so user can select immediately.
        self._handle_roxy_load_profiles(silent=True)
        return tab

    def _build_project_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("projectPanel")
        panel.setMinimumWidth(640)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(8)

        title = QLabel("CapCut projects")
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
        self.project_table.setMinimumHeight(520)
        self.project_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.project_table.setHorizontalHeaderLabels(
            ["Select", "Video", "Source", "Status", "Project"]
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
        header_view.setSectionResizeMode(self.columns.project, header_view.ResizeMode.Stretch)
        self.project_table.setColumnWidth(self.columns.select, 78)
        panel_layout.addWidget(self.project_table, 1)

        self.status_label = QLabel("Loading projects…")
        self.status_label.setObjectName("statusLabel")
        panel_layout.addWidget(self.status_label)

        action_panel = QFrame()
        action_panel.setObjectName("actionPanel")
        action_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        action_grid = QGridLayout(action_panel)
        action_grid.setContentsMargins(12, 10, 12, 10)
        action_grid.setHorizontalSpacing(10)
        action_grid.setVerticalSpacing(8)

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

        self.automate_button = QPushButton("Automate")
        self.automate_button.setProperty("variant", "success")
        self.automate_button.clicked.connect(self._handle_automate_clicked)

        quick_actions = (
            self.reload_button,
            self.sync_audio_button,
            self.sync_images_button,
            self.sync_caption_button,
            self.auto_render_button,
            self.automate_button,
        )
        for button in quick_actions:
            button.setProperty("actionRole", "quick")
            button.setMinimumWidth(170)
            button.setMinimumHeight(40)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        action_grid.addWidget(self.reload_button, 0, 0)
        action_grid.addWidget(self.sync_audio_button, 0, 1)
        action_grid.addWidget(self.sync_images_button, 0, 2)
        action_grid.addWidget(self.sync_caption_button, 1, 0)
        action_grid.addWidget(self.auto_render_button, 1, 1)
        action_grid.addWidget(self.automate_button, 1, 2)
        for column in range(3):
            action_grid.setColumnStretch(column, 1)

        panel_layout.addWidget(action_panel)

        return panel

    def _apply_styles(self) -> None:
        import qdarktheme  # light theme base provided by pyqtdarktheme plugin

        base = qdarktheme.load_stylesheet("light")
        ff = self._font_family

        custom = f"""
            * {{
                font-family: "{ff}";
                color: #1F1F1F;
            }}

            #rootWidget {{
                background: #F7F9FC;
            }}

            #sidebar, #toolPanel, #projectPanel, #workspacePanel {{
                background: #FFFFFF;
                border: 1px solid #DADCE0;
                border-radius: 24px;
            }}

            #sidebarHeader {{
                background: transparent;
                border: none;
                border-bottom: 1px solid #E3E7EE;
                border-top-left-radius: 24px;
                border-top-right-radius: 24px;
            }}

            #sidebarHeader QLabel, #navArea QLabel {{
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
            }}

            #sidebarChip {{
                font-size: 11px;
                font-weight: 700;
                color: #0B57D0;
                letter-spacing: 0.08em;
            }}

            #sidebarMonogram {{
                min-width: 44px;
                min-height: 44px;
                max-width: 44px;
                max-height: 44px;
                border-radius: 14px;
                background: #E8F0FE;
                color: #0B57D0;
                font-size: 16px;
                font-weight: 700;
                qproperty-alignment: AlignCenter;
            }}

            #appName {{
                font-size: 22px;
                font-weight: 600;
                color: #1F1F1F;
                letter-spacing: -0.02em;
            }}

            #appTagline {{
                font-size: 13px;
                color: #5F6368;
            }}

            #navArea {{
                background: #FFFFFF;
                border: none;
            }}

            QLabel#navSection {{
                font-size: 11px;
                font-weight: 700;
                color: #5F6368;
                letter-spacing: 0.08em;
                background: transparent;
                border: none;
            }}

            QPushButton#navBtn {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 18px;
                padding: 0px 16px;
                min-height: 52px;
                text-align: left;
                font-size: 15px;
                font-weight: 500;
                color: #444746;
            }}

            QPushButton#navBtn:hover:!checked {{
                background: #EEF3FD;
                color: #0B57D0;
            }}

            QPushButton#navBtn:checked {{
                background: #D3E3FD;
                color: #041E49;
                border: 1px solid #D3E3FD;
            }}

            #toolHeader, #workspaceHeader {{
                background: #FFFFFF;
                border: none;
                border-bottom: 1px solid #E3E7EE;
                border-top-left-radius: 24px;
                border-top-right-radius: 24px;
            }}

            #toolTitle, #workspaceTitle {{
                font-size: 32px;
                font-weight: 600;
                color: #1F1F1F;
                letter-spacing: -0.03em;
            }}

            #workspaceEyebrow {{
                font-size: 11px;
                font-weight: 700;
                color: #0B57D0;
                letter-spacing: 0.08em;
            }}

            #workspaceDescription {{
                font-size: 15px;
                color: #5F6368;
                max-width: 640px;
            }}

            #projectTitle {{
                font-size: 18px;
                font-weight: 600;
                color: #1F1F1F;
                letter-spacing: -0.02em;
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                margin: 0px;
            }}

            #workspaceStack {{
                background: transparent;
                border: none;
            }}

            #projectsToolbar, #projectsListCard, #projectsDetailCard {{
                background: #FFFFFF;
                border: 1px solid #E3E7EE;
                border-radius: 20px;
            }}

            #projectsPanelTitle {{
                font-size: 16px;
                font-weight: 600;
                color: #1F1F1F;
                background: transparent;
            }}

            QListWidget#projectParentList {{
                border: none;
                background: transparent;
                outline: none;
                padding: 0px;
            }}

            QListWidget#projectParentList::item {{
                border: 1px solid #E3E7EE;
                border-radius: 18px;
                padding: 14px 16px;
                margin-bottom: 10px;
                background: #FFFFFF;
                color: #1F1F1F;
            }}

            QListWidget#projectParentList::item:selected {{
                background: #D3E3FD;
                border: 1px solid #D3E3FD;
                color: #041E49;
            }}

            QLabel#projectDetailTitle {{
                font-size: 18px;
                font-weight: 600;
                color: #1F1F1F;
            }}

            QLabel#projectDetailStats {{
                font-size: 14px;
                font-weight: 600;
                color: #0B57D0;
            }}

            #workspaceHeader QLabel,
            #workspaceEmptyCard QLabel,
            #projectsToolbar QLabel,
            #projectsListCard QLabel,
            #projectsDetailCard QLabel {{
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
            }}

            QLabel#folderBadgeNone {{
                background: #F1F3F4;
                color: #80868B;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
                padding: 0px 10px;
                border: none;
            }}
            QLabel#folderBadgeOk {{
                background: #E6F4EA;
                color: #137333;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
                padding: 0px 10px;
                border: none;
            }}
            QLabel#folderBadgeMissing {{
                background: #FCE8E6;
                color: #C5221F;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
                padding: 0px 10px;
                border: none;
            }}

            #workspaceSectionTitle {{
                font-size: 18px;
                font-weight: 600;
                color: #1F1F1F;
                letter-spacing: -0.02em;
            }}

            #workspaceEmptyCard {{
                background: #FFFFFF;
                border: 1px solid #E3E7EE;
                border-radius: 20px;
            }}

            #workspaceBody {{
                font-size: 15px;
                font-weight: 400;
                color: #5F6368;
                line-height: 1.5em;
            }}

            #workspaceEmptyNote {{
                padding: 10px 14px;
                border-radius: 14px;
                background: #E8F0FE;
                color: #0B57D0;
                border: 1px solid #D3E3FD;
                font-size: 14px;
                font-weight: 600;
            }}

            #actionPanel {{
                background: #FFFFFF;
                border: 1px solid #E3E7EE;
                border-radius: 16px;
            }}

            QPushButton[actionRole="quick"] {{
                min-height: 44px;
                padding: 0px 16px;
                font-size: 14px;
                font-weight: 600;
                text-align: center;
            }}

            #toolStack {{
                background: #FFFFFF;
                border: none;
            }}

            QPushButton {{
                min-height: 40px;
                padding: 5px 18px;
                border-radius: 20px;
                font-size: 14px;
                font-weight: 600;
            }}

            QPushButton#projectActionBtn {{
                min-height: 40px;
                padding: 0px 18px;
            }}

            QPushButton:disabled {{
                background: #EEF1F4;
                color: #9AA0A6;
                border: 1px solid #DADCE0;
            }}

            QPushButton[variant="primary"] {{
                background: #0B57D0;
                color: #FFFFFF;
                border: 1px solid #0B57D0;
            }}

            QPushButton[variant="primary"]:hover:!disabled {{
                background: #0842A0;
                border-color: #0842A0;
            }}

            QPushButton[variant="primary"]:pressed:!disabled {{
                background: #062E6F;
                border-color: #062E6F;
            }}

            QPushButton[variant="secondary"] {{
                background: #FFFFFF;
                color: #1F1F1F;
                border: 1px solid #DADCE0;
            }}

            QPushButton[variant="secondary"]:hover:!disabled {{
                background: #F8FAFD;
                border-color: #BDC1C6;
            }}

            QPushButton[variant="reload"] {{
                background: #FFFFFF;
                color: #444746;
                border: 1px solid #DADCE0;
            }}

            QPushButton[variant="reload"]:hover:!disabled {{
                background: #F8FAFD;
                border-color: #BDC1C6;
                color: #1F1F1F;
            }}

            QPushButton[variant="audio"] {{
                background: #E8F0FE;
                color: #0B57D0;
                border: 1px solid #D3E3FD;
            }}

            QPushButton[variant="audio"]:hover:!disabled {{
                background: #D3E3FD;
                border-color: #A8C7FA;
                color: #0842A0;
            }}

            QPushButton[variant="images"] {{
                background: #E6F4EA;
                color: #137333;
                border: 1px solid #CEEAD6;
            }}

            QPushButton[variant="images"]:hover:!disabled {{
                background: #CEEAD6;
                border-color: #A8DAB5;
                color: #0D652D;
            }}

            QPushButton[variant="captions"] {{
                background: #F3E8FD;
                color: #7B1FA2;
                border: 1px solid #E1BEE7;
            }}

            QPushButton[variant="captions"]:hover:!disabled {{
                background: #EADCF8;
                border-color: #D7AFE8;
                color: #6A1B9A;
            }}

            QPushButton[variant="pill"] {{
                min-width: 86px;
                border-radius: 18px;
                background: #F1F3F4;
                border: 1px solid #E3E7EE;
                color: #444746;
                font-size: 12px;
                font-weight: 600;
            }}

            QPushButton[variant="pill"]:checked {{
                background: #D3E3FD;
                border-color: #D3E3FD;
                color: #041E49;
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
                background: #FCE8E6;
                border: 1px solid #F4C7C3;
                color: #C5221F;
            }}

            QPushButton[variant="danger"]:hover:!disabled {{
                background: #FAD2CF;
                border-color: #F0B8B5;
            }}

            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit {{
                font-size: 13px;
                border: 1px solid #DADCE0;
                border-radius: 14px;
                background: #FFFFFF;
                padding: 8px 12px;
                color: #1F1F1F;
                selection-background-color: #D3E3FD;
                selection-color: #041E49;
            }}

            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QTextEdit:focus {{
                border: 1px solid #0B57D0;
                background: #FFFFFF;
            }}

            QLineEdit#assetPath {{
                background: #F8FAFD;
                color: #5F6368;
            }}

            QLabel#sectionLabel, QLabel#assetCaption {{
                font-size: 12px;
                font-weight: 600;
                color: #5F6368;
                letter-spacing: 0.06em;
            }}

            QLabel#hintLabel {{
                font-size: 14px;
                color: #5F6368;
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
                background: #F8FAFD;
                border: 1px solid #E3E7EE;
                border-radius: 16px;
            }}

            QListWidget#presetList {{
                font-size: 13px;
                border: 1px solid #DADCE0;
                border-radius: 16px;
                background: #FFFFFF;
            }}

            QListWidget#presetList::item {{
                padding: 8px 12px;
                border-radius: 6px;
            }}

            QListWidget#presetList::item:hover:!selected {{
                background: #F8FAFD;
            }}

            QListWidget#presetList::item:selected {{
                background: #D3E3FD;
                color: #041E49;
                font-weight: 600;
            }}

            #projectHierarchyScroll {{
                border: 1px solid #DADCE0;
                border-radius: 16px;
                background: #F8FAFD;
            }}

            #projectHierarchyRoot {{
                background: transparent;
            }}

            QFrame#projectParentCard {{
                border: 1px solid #E3E7EE;
                border-radius: 16px;
                background: #FFFFFF;
            }}

            QLabel#projectParentTitle {{
                font-size: 15px;
                font-weight: 700;
                color: #1F1F1F;
                background: transparent;
                border: none;
            }}

            QLabel#projectParentCount {{
                font-size: 11px;
                font-weight: 700;
                color: #0B57D0;
                background: #E8F0FE;
                border: 1px solid #D3E3FD;
                border-radius: 999px;
                padding: 2px 8px;
            }}

            QLabel#projectParentMeta {{
                font-size: 12px;
                color: #5F6368;
                background: transparent;
                border: none;
            }}

            QTableWidget#projectChildrenTable {{
                font-size: 13px;
                border: none;
                background: transparent;
                alternate-background-color: transparent;
                outline: none;
                selection-background-color: transparent;
            }}

            QTableWidget#projectChildrenTable::item {{
                padding: 0px 12px;
                color: #1E293B;
                border-bottom: 1px solid #F1F5F9;
                background: transparent;
            }}

            QTableWidget#projectChildrenTable::item:selected {{
                background: #EFF6FF;
                color: #1D4ED8;
            }}

            QTableWidget#projectChildrenTable::item:hover:!selected {{
                background: #F8FAFF;
            }}

            QTableWidget#projectChildrenTable QHeaderView {{
                background: transparent;
            }}

            QTableWidget#projectChildrenTable QHeaderView::section {{
                font-size: 11px;
                font-weight: 700;
                background: #F8FAFC;
                color: #475467;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                padding: 12px 12px;
            }}

            QTableWidget#projectChildrenTable QHeaderView::section:first {{
                padding: 12px 8px;
            }}

            QPushButton#rowSelectSquare {{
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                border: 1.5px solid #CBD5E1;
                border-radius: 6px;
                background: #FFFFFF;
                color: transparent;
                font-size: 12px;
                font-weight: 700;
                padding: 0px;
            }}

            QPushButton#rowSelectSquare:hover {{
                border-color: #93C5FD;
                background: #F8FAFF;
            }}

            QPushButton#rowSelectSquare:checked {{
                border-color: #0B57D0;
                background: #0B57D0;
                color: #FFFFFF;
            }}

            QCheckBox#rowCheckbox {{
                margin: 0px;
            }}

            QCheckBox#rowCheckbox::indicator {{
                width: 15px;
                height: 15px;
                border: 1.5px solid #CBD5E1;
                border-radius: 4px;
                background: white;
            }}

            QCheckBox#rowCheckbox::indicator:checked {{
                border: 1.5px solid #3B82F6;
                background: #3B82F6;
            }}

            QCheckBox#rowCheckbox::indicator:hover {{
                border: 1.5px solid #93C5FD;
            }}

            QCheckBox#headerSelectAll::indicator {{
                width: 15px;
                height: 15px;
                border: 1.5px solid #CBD5E1;
                border-radius: 4px;
                background: white;
            }}

            QCheckBox#headerSelectAll::indicator:checked {{
                border: 1.5px solid #3B82F6;
                background: #3B82F6;
            }}

            QCheckBox#headerSelectAll::indicator:indeterminate {{
                border: 1.5px solid #3B82F6;
                background: #BFDBFE;
            }}

            #projectTable {{
                font-size: 13px;
                border: 1px solid #DADCE0;
                border-radius: 16px;
                background: #FFFFFF;
                alternate-background-color: #F8FAFD;
                gridline-color: #E3E7EE;
            }}

            #projectTable::item {{
                padding: 7px 8px;
                color: #334155;
            }}

            #projectTable::item:selected {{
                background: #D3E3FD;
                color: #041E49;
            }}

            QTableWidget QTableCornerButton::section {{
                background: #F1F3F4;
                border: none;
            }}

            QHeaderView::section {{
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.04em;
                background: #F1F3F4;
                color: #5F6368;
                border: none;
                border-right: 1px solid #E3E7EE;
                border-bottom: 1px solid #E3E7EE;
                padding: 10px 8px;
            }}

            QHeaderView::section:checked,
            QHeaderView::section:selected,
            QHeaderView::section:pressed,
            QHeaderView::section:focus {{
                background: #F1F3F4;
                color: #5F6368;
                border: none;
                border-right: 1px solid #E3E7EE;
                border-bottom: 1px solid #E3E7EE;
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
                border: 1px solid #9AA0A6;
                border-radius: 4px;
                background: #FFFFFF;
                image: none;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox::indicator:hover {{
                border-color: #0B57D0;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox::indicator:unchecked {{
                image: none;
            }}

            QTableWidget QCheckBox#rowSelectCheckbox::indicator:checked {{
                background: #0B57D0;
                border-color: #0B57D0;
                image: none;
            }}

            QTableWidget QLabel#rowSelectOrderLabel {{
                min-width: 18px;
                max-width: 18px;
                color: #0B57D0;
                font-size: 11px;
                font-weight: 700;
                background: transparent;
                border: none;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo {{
                min-height: 24px;
                border: 1px solid transparent;
                border-radius: 12px;
                padding: 2px 22px 2px 8px;
                background: #E8F0FE;
                color: #041E49;
                font-size: 12px;
                font-weight: 600;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo[hasProject="false"] {{
                background: #F8FAFD;
                color: #5F6368;
                font-weight: 500;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo:hover {{
                border-color: #A8C7FA;
                background: #D3E3FD;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo:focus {{
                border: 1px solid #0B57D0;
                background: #D3E3FD;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 18px;
                border: none;
                background: transparent;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo QAbstractItemView {{
                border: 1px solid #DADCE0;
                background: #FFFFFF;
                selection-background-color: #D3E3FD;
                selection-color: #041E49;
                padding: 4px;
            }}

            QDialog#childProjectsDialog {{
                background: #F7F9FC;
            }}

            QDialog#childProjectsDialog QLabel#projectParentTitle,
            QDialog#childProjectsDialog QLabel#workspaceBody,
            QDialog#childProjectsDialog QLabel#navSection,
            QDialog#childProjectsDialog QLabel#projectsPanelTitle,
            QDialog#childProjectsDialog QLabel#workspaceSectionTitle,
            QDialog#childProjectsDialog QLabel#workspaceEmptyNote {{
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
            }}

            QDialog#childProjectsDialog QFrame#childProjectsShell {{
                background: #FFFFFF;
                border: 1px solid #DADCE0;
                border-radius: 24px;
            }}

            QDialog#childProjectWorkspaceDialog QFrame#childProjectsShell {{
                background: transparent;
                border: none;
                border-radius: 0px;
            }}

            QDialog#childProjectWorkspaceDialog QFrame#childToolsSidebar {{
                background: #F8FAFD;
                border: none;
                border-right: 1px solid #E3E7EE;
                border-top-left-radius: 24px;
                border-bottom-left-radius: 24px;
            }}

            QDialog#childProjectWorkspaceDialog QFrame#childToolContent,
            QDialog#childProjectWorkspaceDialog QScrollArea#childToolScroll,
            QDialog#childProjectWorkspaceDialog QScrollArea#childToolScroll > QWidget > QWidget,
            QDialog#childProjectWorkspaceDialog QFrame#projectsDetailCard {{
                background: transparent;
                border: none;
                border-radius: 0px;
            }}

            QDialog#childProjectWorkspaceDialog QPushButton {{
                background: #FFFFFF;
                color: #0B57D0;
                border: 1px solid #D0D7E2;
                border-radius: 18px;
            }}

            QDialog#childProjectWorkspaceDialog QPushButton:hover {{
                background: #F8FAFC;
                border-color: #B7C6D8;
            }}

            QDialog#childProjectWorkspaceDialog QPushButton:checked {{
                background: #FFFFFF;
                color: #0B57D0;
                border: 1px solid #0B57D0;
            }}

            QDialog#childProjectsDialog QFrame#childToolsSidebar {{
                background: #F8FAFD;
                border: none;
                border-right: 1px solid #E3E7EE;
                border-top-left-radius: 24px;
                border-bottom-left-radius: 24px;
            }}

            QDialog#childProjectsDialog QFrame#childToolContent {{
                background: #FFFFFF;
                border: none;
                border-top-right-radius: 24px;
                border-bottom-right-radius: 24px;
            }}

            QDialog#childProjectsDialog QPushButton#childToolNavBtn {{
                min-height: 46px;
                padding: 0px 14px;
                text-align: left;
                border-radius: 18px;
                background: transparent;
                border: 1px solid transparent;
                color: #444746;
                font-size: 13px;
                font-weight: 600;
            }}

            QDialog#childProjectsDialog QPushButton#childToolNavBtn:hover:!checked {{
                background: #EEF3FD;
                color: #0B57D0;
            }}

            QDialog#childProjectsDialog QPushButton#childToolNavBtn:checked {{
                background: #D3E3FD;
                border: 1px solid #D3E3FD;
                color: #041E49;
            }}

            QDialog#childProjectsDialog QStackedWidget#childToolStack {{
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

            QDialog#srtStrictReviewDialog {{
                background: #F8FAFC;
                border: 1px solid #D5DEE9;
                border-radius: 18px;
            }}

            QDialog#srtStrictReviewDialog QFrame#strictReviewHero,
            QDialog#srtStrictReviewDialog QFrame#strictReviewCard {{
                background: #FFFFFF;
                border: 1px solid #D6DEE9;
                border-radius: 16px;
            }}

            QDialog#srtStrictReviewDialog QLabel#strictReviewIcon {{
                background: #FEF3C7;
                border: 1px solid #FCD34D;
                border-radius: 28px;
                padding: 10px;
            }}

            QDialog#srtStrictReviewDialog QLabel#strictReviewEyebrow {{
                font-size: 11px;
                font-weight: 700;
                color: #B45309;
                letter-spacing: 0.08em;
            }}

            QDialog#srtStrictReviewDialog QLabel#strictReviewTitle {{
                font-size: 24px;
                font-weight: 700;
                color: #0F172A;
            }}

            QDialog#srtStrictReviewDialog QLabel#strictReviewBody {{
                font-size: 13px;
                color: #475569;
            }}

            QDialog#srtStrictReviewDialog QLabel#strictReviewCardTitle {{
                font-size: 11px;
                font-weight: 700;
                color: #64748B;
                letter-spacing: 0.06em;
            }}

            QDialog#srtStrictReviewDialog QTextEdit#strictReviewText {{
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
                color: #0F172A;
                padding: 10px 12px;
            }}

            QDialog#srtStrictReviewDialog QLabel#strictReviewMetrics {{
                font-size: 12px;
                color: #334155;
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
                padding: 10px 12px;
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

            QDialog#renderLogDialog QFrame#renderStatusCard {{
                background: #F8FAFC;
                border: 1px solid #D6DEE9;
                border-radius: 10px;
            }}

            QDialog#renderLogDialog QLabel#renderCurrentVideoLabel {{
                font-size: 15px;
                font-weight: 700;
                color: #0F172A;
            }}

            QDialog#renderLogDialog QLabel#renderCurrentStageLabel {{
                font-size: 13px;
                font-weight: 600;
                color: #334155;
            }}

            QDialog#renderLogDialog QLabel#renderStatChip {{
                background: #E2E8F0;
                border: 1px solid #CBD5E1;
                border-radius: 9px;
                color: #334155;
                font-size: 12px;
                font-weight: 700;
                padding: 4px 9px;
            }}

            QDialog#renderLogDialog QLabel#renderStatChipDanger {{
                background: #FEE2E2;
                border: 1px solid #FCA5A5;
                border-radius: 9px;
                color: #991B1B;
                font-size: 12px;
                font-weight: 700;
                padding: 4px 9px;
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
            getattr(self, "raw_seo_apply_btn", None),
            getattr(self, "raw_seo_browse_btn", None),
            getattr(self, "raw_seo_clear_btn", None),
            getattr(self, "raw_seo_export_btn", None),
            getattr(self, "raw_seo_view_btn", None),
            getattr(self, "raw_seo_open_editor_btn", None),
            getattr(self, "raw_seo_clear_metadata_btn", None),
            getattr(self, "raw_seo_strict_check", None),
            getattr(self, "srt_generate_btn", None),
            getattr(self, "roxy_load_profiles_btn", None),
            getattr(self, "roxy_video_browse_btn", None),
            getattr(self, "roxy_upload_btn", None),
            getattr(self, "project_preset_add_btn", None),
            getattr(self, "project_hierarchy_refresh_btn", None),
            getattr(self, "project_parent_edit_btn", None),
            getattr(self, "project_parent_delete_btn", None),
            getattr(self, "project_parent_open_children_btn", None),
            getattr(self, "automate_button", None),
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
            f"Loaded {total} video(s) · Selected {selected} · Completed {completed}"
        )

    def _update_ffmpeg_button_state(self) -> None:
        """Compatibility shim for legacy FFmpeg controls (currently no-op)."""
        return

    @staticmethod
    def _default_project_preset_draft() -> dict[str, object]:
        return {
            "name": "",
            "keywords_raw": "",
            "author": "",
            "publisher": "",
            "copyright": "",
            "rating": 5,
        }

    @staticmethod
    def _default_video_child_settings_draft() -> dict[str, object]:
        return {
            "description": "",
            "keywords_raw": "",
            "author": "",
            "publisher": "",
            "copyright": "",
            "rating": 5,
        }

    @staticmethod
    def _default_manual_child_project_draft() -> dict[str, object]:
        return {
            "id": "",
            "name": "",
            "status": "draft",
            "title": "",
            "description": "",
            "seeding_comments_raw": "",
            "keywords_raw": "",
            "author": "",
            "publisher": "",
            "copyright": "",
            "rating": 5,
            "folder_path": "",
        }

    @classmethod
    def _normalize_video_child_settings(cls, raw: object) -> dict[str, object]:
        base = cls._default_video_child_settings_draft()
        if not isinstance(raw, dict):
            return dict(base)
        payload = dict(base)
        payload["description"] = str(raw.get("description", "")).strip()
        payload["keywords_raw"] = str(raw.get("keywords_raw", "")).strip()
        payload["author"] = str(raw.get("author", "")).strip()
        payload["publisher"] = str(raw.get("publisher", "")).strip()
        payload["copyright"] = str(raw.get("copyright", "")).strip()
        try:
            rating = int(raw.get("rating", 5))
        except (TypeError, ValueError):
            rating = 5
        payload["rating"] = max(0, min(5, rating))
        return payload

    @classmethod
    def _normalize_manual_child_project(cls, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        payload = dict(cls._default_manual_child_project_draft())
        payload["id"] = str(raw.get("id", "")).strip() or uuid4().hex[:12]
        payload["name"] = str(raw.get("name", "")).strip()
        payload["status"] = str(raw.get("status", "draft")).strip() or "draft"
        payload["title"] = str(raw.get("title", "")).strip()
        payload["description"] = str(raw.get("description", "")).strip()
        payload["seeding_comments_raw"] = str(raw.get("seeding_comments_raw", "")).strip()
        payload["keywords_raw"] = str(raw.get("keywords_raw", "")).strip()
        payload["author"] = str(raw.get("author", "")).strip()
        payload["publisher"] = str(raw.get("publisher", "")).strip()
        payload["copyright"] = str(raw.get("copyright", "")).strip()
        payload["folder_path"] = str(raw.get("folder_path", "")).strip()
        try:
            rating = int(raw.get("rating", 5))
        except (TypeError, ValueError):
            rating = 5
        payload["rating"] = max(0, min(5, rating))
        if not str(payload["name"]).strip():
            return None
        return payload

    @staticmethod
    def _manual_child_status_options() -> list[tuple[str, str]]:
        return [
            ("draft", "Draft"),
            ("ready", "Ready"),
            ("paused", "Paused"),
        ]

    def _manual_child_status_label(self, value: object) -> str:
        normalized = str(value or "draft").strip() or "draft"
        for key, label in self._manual_child_status_options():
            if key == normalized:
                return label
        return "Draft"

    def _manual_child_projects_for_parent(self, preset_id: str) -> list[dict[str, object]]:
        parent_key = str(preset_id).strip()
        items = self.manual_child_projects_by_parent.get(parent_key, [])
        normalized: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for item in items:
            clean = self._normalize_manual_child_project(item)
            if clean is None:
                continue
            child_id = str(clean.get("id", "")).strip()
            if child_id in seen_ids:
                clean["id"] = uuid4().hex[:12]
                child_id = str(clean["id"])
            seen_ids.add(child_id)
            normalized.append(clean)
        self.manual_child_projects_by_parent[parent_key] = normalized
        return normalized

    @staticmethod
    def _parse_seeding_comments(raw: str) -> list[str]:
        comments: list[str] = []
        for line in raw.splitlines():
            clean = line.strip()
            if not clean:
                continue
            clean = re.sub(r"^[-*•]+\s*", "", clean)
            clean = re.sub(r"^\d+[\).\-\s]+", "", clean)
            clean = clean.strip()
            if clean:
                comments.append(clean)
        return comments

    def _next_manual_child_project_number(self, preset_id: str) -> int:
        next_number = 1
        for item in self._manual_child_projects_for_parent(preset_id):
            name = str(item.get("name", "")).strip()
            match = re.fullmatch(r"Project\s+(\d+)", name, flags=re.IGNORECASE)
            if not match:
                continue
            next_number = max(next_number, int(match.group(1)) + 1)
        return next_number

    @staticmethod
    def _normalize_project_preset(raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name", "")).strip()
        if not name:
            return None
        preset_id = str(raw.get("id", "")).strip() or uuid4().hex[:12]
        try:
            rating = int(raw.get("rating", 5))
        except (TypeError, ValueError):
            rating = 5
        rating = max(0, min(5, rating))
        return {
            "id": preset_id,
            "name": name,
            "keywords_raw": str(raw.get("keywords_raw", "")).strip(),
            "author": str(raw.get("author", "")).strip(),
            "publisher": str(raw.get("publisher", "")).strip(),
            "copyright": str(raw.get("copyright", "")).strip(),
            "rating": rating,
        }

    def _project_preset_ids(self) -> set[str]:
        return {
            str(preset.get("id", "")).strip()
            for preset in self.project_presets
            if str(preset.get("id", "")).strip()
        }

    def _load_project_dashboard_settings(self) -> None:
        self.project_presets = []
        self.project_assignment_by_path = {}
        self.video_child_settings_by_path = {}
        self.manual_child_projects_by_parent = {}

        presets_raw = self._settings.value("project_dashboard/presets", "", type=str) or ""
        if presets_raw:
            try:
                parsed = json.loads(presets_raw)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                seen_ids: set[str] = set()
                for item in parsed:
                    normalized = self._normalize_project_preset(item)
                    if normalized is None:
                        continue
                    preset_id = str(normalized["id"])
                    if preset_id in seen_ids:
                        normalized["id"] = uuid4().hex[:12]
                    seen_ids.add(str(normalized["id"]))
                    self.project_presets.append(normalized)

        assignments_raw = self._settings.value("project_dashboard/assignments", "", type=str) or ""
        if assignments_raw:
            try:
                parsed = json.loads(assignments_raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                valid_ids = self._project_preset_ids()
                for path, preset_id in parsed.items():
                    clean_path = str(path).strip()
                    clean_preset_id = str(preset_id).strip()
                    if clean_path and clean_preset_id and clean_preset_id in valid_ids:
                        self.project_assignment_by_path[clean_path] = clean_preset_id

        child_settings_raw = (
            self._settings.value("project_dashboard/video_child_settings", "", type=str) or ""
        )
        if child_settings_raw:
            try:
                parsed = json.loads(child_settings_raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                for path, settings in parsed.items():
                    clean_path = str(path).strip()
                    if not clean_path:
                        continue
                    self.video_child_settings_by_path[clean_path] = self._normalize_video_child_settings(settings)

        manual_children_raw = (
            self._settings.value("project_dashboard/manual_child_projects", "", type=str) or ""
        )
        if manual_children_raw:
            try:
                parsed = json.loads(manual_children_raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                for parent_id, items in parsed.items():
                    clean_parent_id = str(parent_id).strip()
                    if not clean_parent_id or not isinstance(items, list):
                        continue
                    normalized_items = []
                    for item in items:
                        clean = self._normalize_manual_child_project(item)
                        if clean is not None:
                            normalized_items.append(clean)
                    self.manual_child_projects_by_parent[clean_parent_id] = normalized_items

    def _save_project_dashboard_settings(self) -> None:
        self._settings.setValue(
            "project_dashboard/presets",
            json.dumps(self.project_presets, ensure_ascii=False),
        )
        self._settings.setValue(
            "project_dashboard/assignments",
            json.dumps(self.project_assignment_by_path, ensure_ascii=False),
        )
        self._settings.setValue(
            "project_dashboard/video_child_settings",
            json.dumps(self.video_child_settings_by_path, ensure_ascii=False),
        )
        self._settings.setValue(
            "project_dashboard/manual_child_projects",
            json.dumps(self.manual_child_projects_by_parent, ensure_ascii=False),
        )

    def _project_preset_name(self, preset_id: str) -> str:
        for preset in self.project_presets:
            if str(preset.get("id", "")) == preset_id:
                return str(preset.get("name", "")).strip()
        return ""

    def _sorted_project_presets(self) -> list[dict[str, object]]:
        return sorted(
            self.project_presets,
            key=lambda item: str(item.get("name", "")).strip().casefold(),
        )

    def _set_project_assignment(self, project: ProjectItem, preset_id: str) -> None:
        project.assigned_project_id = preset_id
        if preset_id:
            self.project_assignment_by_path[project.path] = preset_id
        else:
            self.project_assignment_by_path.pop(project.path, None)
        self._save_project_dashboard_settings()

    @staticmethod
    def _update_project_combo_visual_state(combo: QComboBox) -> None:
        has_project = bool(str(combo.currentData() or "").strip())
        combo.setProperty("hasProject", "true" if has_project else "false")
        combo.style().unpolish(combo)
        combo.style().polish(combo)
        combo.update()

    def _handle_project_assignment_changed(self, project: ProjectItem, preset_id: object) -> None:
        assigned_id = str(preset_id or "").strip()
        valid_ids = self._project_preset_ids()
        if assigned_id and assigned_id not in valid_ids:
            assigned_id = ""
        if project.assigned_project_id == assigned_id:
            return
        self._set_project_assignment(project, assigned_id)
        self._refresh_project_preset_tab()

    def _open_project_preset_editor(
        self,
        *,
        initial: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        draft = self._default_project_preset_draft()
        if initial:
            for key in draft:
                if key in initial:
                    draft[key] = initial[key]

        dialog = QDialog(self)
        dialog.setWindowTitle("Project Preset")
        dialog.resize(640, 480)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        name_label = QLabel("Project Name")
        name_label.setObjectName("sectionLabel")
        layout.addWidget(name_label)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("e.g. Seniorforge 365")
        name_edit.setText(str(draft.get("name", "")).strip())
        layout.addWidget(name_edit)

        keywords_label = QLabel("Keywords")
        keywords_label.setObjectName("sectionLabel")
        layout.addWidget(keywords_label)
        keywords_edit = QTextEdit()
        keywords_edit.setPlaceholderText("keyword 1, keyword 2")
        keywords_edit.setPlainText(str(draft.get("keywords_raw", "")))
        keywords_edit.setFixedHeight(92)
        layout.addWidget(keywords_edit)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        author_label = QLabel("Author")
        author_label.setObjectName("sectionLabel")
        grid.addWidget(author_label, 0, 0)
        author_edit = QLineEdit()
        author_edit.setText(str(draft.get("author", "")).strip())
        grid.addWidget(author_edit, 1, 0)

        publisher_label = QLabel("Publisher")
        publisher_label.setObjectName("sectionLabel")
        grid.addWidget(publisher_label, 0, 1)
        publisher_edit = QLineEdit()
        publisher_edit.setText(str(draft.get("publisher", "")).strip())
        grid.addWidget(publisher_edit, 1, 1)

        copyright_label = QLabel("Copyright Note")
        copyright_label.setObjectName("sectionLabel")
        grid.addWidget(copyright_label, 2, 0)
        copyright_edit = QLineEdit()
        copyright_edit.setPlaceholderText("Channel URL")
        copyright_edit.setText(str(draft.get("copyright", "")).strip())
        grid.addWidget(copyright_edit, 3, 0, 1, 2)

        rating_label = QLabel("Rating (0-5)")
        rating_label.setObjectName("sectionLabel")
        grid.addWidget(rating_label, 4, 0)
        rating_spin = QSpinBox()
        rating_spin.setRange(0, 5)
        try:
            rating_spin.setValue(int(draft.get("rating", 5)))
        except (TypeError, ValueError):
            rating_spin.setValue(5)
        grid.addWidget(rating_spin, 5, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("variant", "secondary")
        save_btn = QPushButton("Save")
        save_btn.setProperty("variant", "primary")
        save_btn.setDefault(True)
        save_btn.setAutoDefault(True)
        actions.addWidget(cancel_btn)
        actions.addWidget(save_btn)
        layout.addLayout(actions)

        result: dict[str, object] | None = None

        def save_and_close() -> None:
            nonlocal result
            name = name_edit.text().strip()
            if not name:
                QMessageBox.information(dialog, "Missing name", "Project name is required.")
                return
            result = {
                "name": name,
                "keywords_raw": keywords_edit.toPlainText().strip(),
                "author": author_edit.text().strip(),
                "publisher": publisher_edit.text().strip(),
                "copyright": copyright_edit.text().strip(),
                "rating": rating_spin.value(),
            }
            dialog.accept()

        save_btn.clicked.connect(save_and_close)
        cancel_btn.clicked.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return result
        return None

    def _open_project_preset_manager(self) -> None:
        # Backward-compatible entrypoint if any old button still calls this.
        self._switch_tool("project")

    def _find_project_preset_by_id(self, preset_id: str) -> dict[str, object] | None:
        target = str(preset_id).strip()
        if not target:
            return None
        for preset in self.project_presets:
            if str(preset.get("id", "")) == target:
                return preset
        return None

    @staticmethod
    def _project_preset_as_raw_seo_source(preset: dict[str, object] | None) -> dict[str, object]:
        if not isinstance(preset, dict):
            return {
                "description": "",
                "keywords_raw": "",
                "author": "",
                "publisher": "",
                "copyright": "",
                "rating": -1,
            }
        try:
            rating = int(preset.get("rating", 5))
        except (TypeError, ValueError):
            rating = 5
        return {
            "description": "",
            "keywords_raw": str(preset.get("keywords_raw", "")).strip(),
            "author": str(preset.get("author", "")).strip(),
            "publisher": str(preset.get("publisher", "")).strip(),
            "copyright": str(preset.get("copyright", "")).strip(),
            "rating": max(0, min(5, rating)),
        }

    @staticmethod
    def _child_has_full_override(settings: dict[str, object]) -> bool:
        text_fields = ("description", "keywords_raw", "author", "publisher", "copyright")
        if any(str(settings.get(field, "")).strip() for field in text_fields):
            return True
        try:
            rating = int(settings.get("rating", 5))
        except (TypeError, ValueError):
            rating = 5
        return rating != 5

    def _build_automate_job_item(self, project: ProjectItem) -> AutomateJobItem:
        preset = self._find_project_preset_by_id(str(project.assigned_project_id or "").strip())
        preset_source = self._project_preset_as_raw_seo_source(preset)
        child_existing = self.video_child_settings_by_path.get(project.path, {})
        child_source = self._normalize_video_child_settings(child_existing)

        if self._child_has_full_override(child_source):
            source = child_source
            source_label = "child override"
        elif preset is not None:
            source = preset_source
            source_label = "project preset"
        else:
            source = {
                "description": "",
                "keywords_raw": "",
                "author": "",
                "publisher": "",
                "copyright": "",
                "rating": -1,
            }
            source_label = "empty defaults"

        seo_draft = {
            "title": "",
            "description": str(source.get("description", "")).strip(),
            "keywords_raw": str(source.get("keywords_raw", "")).strip(),
            "author": str(source.get("author", "")).strip(),
            "publisher": str(source.get("publisher", "")).strip(),
            "copyright": str(source.get("copyright", "")).strip(),
            "comment": "",
            "language": "",
            "website": "",
            "date_created": "",
            "city": "",
            "country": "",
            "custom_tags_raw": "",
            "rating": source.get("rating", -1),
        }

        keywords = parse_keywords(str(seo_draft["keywords_raw"]))
        extra_tags = self._collect_raw_seo_advanced_tags_from_draft(seo_draft)
        # apply_raw_seo currently requires at least one core field:
        # title, description, or keywords.
        raw_seo_enabled = any(
            (
                str(seo_draft["title"]).strip(),
                str(seo_draft["description"]).strip(),
                keywords,
            )
        )
        if not raw_seo_enabled:
            extra_tags = {}

        return AutomateJobItem(
            project=project,
            raw_seo_title=str(seo_draft["title"]).strip(),
            raw_seo_description=str(seo_draft["description"]).strip(),
            raw_seo_keywords=keywords,
            raw_seo_extra_tags=extra_tags,
            raw_seo_enabled=raw_seo_enabled,
            raw_seo_source=source_label,
        )

    def _video_child_settings_for_path(self, path: str) -> dict[str, object]:
        existing = self.video_child_settings_by_path.get(path)
        if existing is None:
            normalized = self._normalize_video_child_settings({})
            self.video_child_settings_by_path[path] = normalized
            return dict(normalized)
        normalized = self._normalize_video_child_settings(existing)
        self.video_child_settings_by_path[path] = normalized
        return dict(normalized)

    def _open_video_child_editor(self, project: ProjectItem) -> bool:
        draft = self._video_child_settings_for_path(project.path)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit Child Project: {project.name}")
        dialog.resize(760, 560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info = QLabel("Chỉnh sửa thông số cho project con.")
        info.setObjectName("hintLabel")
        layout.addWidget(info)

        desc_label = QLabel("Description")
        desc_label.setObjectName("sectionLabel")
        layout.addWidget(desc_label)
        desc_edit = QTextEdit()
        desc_edit.setPlaceholderText("Description override...")
        desc_edit.setPlainText(str(draft.get("description", "")))
        desc_edit.setFixedHeight(92)
        layout.addWidget(desc_edit)

        keywords_label = QLabel("Keywords")
        keywords_label.setObjectName("sectionLabel")
        layout.addWidget(keywords_label)
        keywords_edit = QTextEdit()
        keywords_edit.setPlaceholderText("kw1, kw2...")
        keywords_edit.setPlainText(str(draft.get("keywords_raw", "")))
        keywords_edit.setFixedHeight(86)
        layout.addWidget(keywords_edit)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        author_label = QLabel("Author")
        author_label.setObjectName("sectionLabel")
        grid.addWidget(author_label, 0, 0)
        author_edit = QLineEdit()
        author_edit.setText(str(draft.get("author", "")))
        grid.addWidget(author_edit, 1, 0)

        publisher_label = QLabel("Publisher")
        publisher_label.setObjectName("sectionLabel")
        grid.addWidget(publisher_label, 0, 1)
        publisher_edit = QLineEdit()
        publisher_edit.setText(str(draft.get("publisher", "")))
        grid.addWidget(publisher_edit, 1, 1)

        copyright_label = QLabel("Copyright")
        copyright_label.setObjectName("sectionLabel")
        grid.addWidget(copyright_label, 2, 0)
        copyright_edit = QLineEdit()
        copyright_edit.setText(str(draft.get("copyright", "")))
        grid.addWidget(copyright_edit, 3, 0, 1, 2)

        rating_label = QLabel("Rating (0-5)")
        rating_label.setObjectName("sectionLabel")
        grid.addWidget(rating_label, 4, 0)
        rating_spin = QSpinBox()
        rating_spin.setRange(0, 5)
        try:
            rating_spin.setValue(int(draft.get("rating", 5)))
        except (TypeError, ValueError):
            rating_spin.setValue(5)
        grid.addWidget(rating_spin, 5, 0)
        layout.addLayout(grid)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("variant", "secondary")
        save_btn = QPushButton("Save")
        save_btn.setProperty("variant", "primary")
        actions.addWidget(cancel_btn)
        actions.addWidget(save_btn)
        layout.addLayout(actions)

        saved = False

        def save_and_close() -> None:
            nonlocal saved
            payload = {
                "description": desc_edit.toPlainText().strip(),
                "keywords_raw": keywords_edit.toPlainText().strip(),
                "author": author_edit.text().strip(),
                "publisher": publisher_edit.text().strip(),
                "copyright": copyright_edit.text().strip(),
                "rating": rating_spin.value(),
            }
            self.video_child_settings_by_path[project.path] = self._normalize_video_child_settings(payload)
            self._save_project_dashboard_settings()
            saved = True
            dialog.accept()

        save_btn.clicked.connect(save_and_close)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec()
        return saved

    def _open_manual_child_project_editor(
        self,
        preset_id: str,
        *,
        initial: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        draft = self._default_manual_child_project_draft()
        if initial:
            draft.update({key: initial.get(key, draft.get(key)) for key in draft})

        dialog = QDialog(self)
        dialog.setWindowTitle("Child Project")
        dialog.resize(420, 220)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info = QLabel("Set the sequence number for this child project.")
        info.setObjectName("workspaceBody")
        info.setWordWrap(True)
        layout.addWidget(info)

        number_label = QLabel("Project Number")
        number_label.setObjectName("sectionLabel")
        layout.addWidget(number_label)

        number_spin = QSpinBox()
        number_spin.setRange(1, 9999)
        existing_name = str(draft.get("name", "")).strip()
        existing_match = re.fullmatch(r"Project\s+(\d+)", existing_name, flags=re.IGNORECASE)
        if existing_match:
            number_spin.setValue(int(existing_match.group(1)))
        else:
            number_spin.setValue(self._next_manual_child_project_number(preset_id))
        layout.addWidget(number_spin)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("variant", "secondary")
        save_btn = QPushButton("Save")
        save_btn.setProperty("variant", "primary")
        actions.addWidget(cancel_btn)
        actions.addWidget(save_btn)
        layout.addLayout(actions)

        result: dict[str, object] | None = None

        def save_and_close() -> None:
            nonlocal result
            project_number = number_spin.value()
            payload = {
                "id": str(draft.get("id", "")).strip() or uuid4().hex[:12],
                "name": f"Project {project_number}",
                "status": "draft",
                "title": str(draft.get("title", "")).strip(),
                "description": str(draft.get("description", "")).strip(),
                "seeding_comments_raw": str(draft.get("seeding_comments_raw", "")).strip(),
                "keywords_raw": "",
                "author": "",
                "publisher": "",
                "copyright": "",
                "rating": 5,
            }
            result = self._normalize_manual_child_project(payload)
            dialog.accept()

        cancel_btn.clicked.connect(dialog.reject)
        save_btn.clicked.connect(save_and_close)
        dialog.exec()
        return result

    def _open_project_children_dialog(self, preset_id: str) -> None:
        preset = self._find_project_preset_by_id(preset_id)
        if preset is None:
            QMessageBox.information(self, "No project", "Project not found.")
            return

        children = self._manual_child_projects_for_parent(preset_id)
        dialog = QDialog(self, Qt.WindowType.Window)
        dialog.setWindowTitle(f"Child Projects - {str(preset.get('name', '')).strip()}")
        dialog.setObjectName("childProjectsDialog")
        self._fit_window_to_screen(dialog, min_width=1280, min_height=740)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        heading = QLabel(f"{str(preset.get('name', '')).strip()} · {len(children)} child project(s)")
        heading.setObjectName("projectParentTitle")
        subtitle = QLabel("Create and manage child projects under this parent preset.")
        subtitle.setObjectName("workspaceBody")
        header_col.addWidget(heading)
        header_col.addWidget(subtitle)
        header_row.addLayout(header_col, 1)
        close_btn_top = QPushButton("Back")
        close_btn_top.setProperty("variant", "primary")
        close_btn_top.setIcon(dialog.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        close_btn_top.setIconSize(QSize(16, 16))
        close_btn_top.clicked.connect(dialog.accept)
        header_row.addWidget(close_btn_top, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header_row)

        def refresh_child_dialog_header() -> None:
            latest_children = self._manual_child_projects_for_parent(preset_id)
            heading.setText(
                f"{str(preset.get('name', '')).strip()} · {len(latest_children)} child project(s)"
            )

        # Build the child videos page directly — no sidebar, full-width
        child_page = self._build_child_videos_page(preset_id, refresh_child_dialog_header)
        layout.addWidget(child_page, 1)

        dialog.exec()

    def _find_manual_child_project(self, preset_id: str, child_id: str) -> dict[str, object] | None:
        target_parent = str(preset_id).strip()
        target_child = str(child_id).strip()
        if not target_parent or not target_child:
            return None
        for child in self._manual_child_projects_for_parent(target_parent):
            if str(child.get("id", "")).strip() == target_child:
                return dict(child)
        return None

    def _save_manual_child_project(self, preset_id: str, payload: dict[str, object]) -> None:
        target_parent = str(preset_id).strip()
        target_child = str(payload.get("id", "")).strip()
        children = self._manual_child_projects_for_parent(target_parent)
        for index, child in enumerate(children):
            if str(child.get("id", "")).strip() == target_child:
                children[index] = self._normalize_manual_child_project(payload) or dict(payload)
                self.manual_child_projects_by_parent[target_parent] = children
                self._save_project_dashboard_settings()
                return

    def _build_manual_child_project_workspace(
        self,
        preset_id: str,
        child_id: str,
        on_saved: Callable[[], None] | None = None,
    ) -> QWidget | None:
        child = self._find_manual_child_project(preset_id, child_id)
        if child is None:
            return None

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        shell = QFrame()
        shell.setObjectName("childProjectsShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("childToolsSidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 16, 14, 16)
        sidebar_layout.setSpacing(10)

        sidebar_title = QLabel("Child tools")
        sidebar_title.setObjectName("navSection")
        sidebar_layout.addWidget(sidebar_title)

        tool_group = QButtonGroup(page)
        tool_group.setExclusive(True)
        tool_stack = QStackedWidget()

        content_host = QFrame()
        content_host.setObjectName("childToolContent")
        content_layout = QVBoxLayout(content_host)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(0)

        content_scroll = QScrollArea()
        content_scroll.setObjectName("childToolScroll")
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content_scroll.setWidget(tool_stack)
        content_layout.addWidget(content_scroll, 1)

        def build_details_page() -> QWidget:
            details_page = QWidget()
            details_layout = QVBoxLayout(details_page)
            details_layout.setContentsMargins(0, 0, 0, 0)
            details_layout.setSpacing(14)

            card = QFrame()
            card.setObjectName("projectsDetailCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(24, 24, 24, 24)
            card_layout.setSpacing(12)

            card_heading = QLabel("AI Gen")
            card_heading.setObjectName("projectsPanelTitle")
            card_layout.addWidget(card_heading)

            card_name = QLabel(str(child.get("name", "")).strip())
            card_name.setObjectName("projectDetailTitle")
            card_layout.addWidget(card_name)

            title_label = QLabel("Title")
            title_label.setObjectName("sectionLabel")
            card_layout.addWidget(title_label)

            title_edit = QLineEdit()
            title_edit.setPlaceholderText("Enter video title")
            title_edit.setText(str(child.get("title", "")).strip())
            card_layout.addWidget(title_edit)

            description_label = QLabel("Description")
            description_label.setObjectName("sectionLabel")
            card_layout.addWidget(description_label)

            description_edit = QTextEdit()
            description_edit.setPlaceholderText("Enter description for this child project...")
            description_edit.setPlainText(str(child.get("description", "")).strip())
            description_edit.setFixedHeight(140)
            card_layout.addWidget(description_edit)

            seeding_label = QLabel("Comment seeding")
            seeding_label.setObjectName("sectionLabel")
            card_layout.addWidget(seeding_label)

            seeding_edit = QTextEdit()
            seeding_edit.setPlaceholderText("Paste comment, mỗi dòng một comment")
            seeding_edit.setPlainText(str(child.get("seeding_comments_raw", "")).strip())
            seeding_edit.setFixedHeight(180)
            card_layout.addWidget(seeding_edit)

            parsed_count = QLabel("Parsed comments: 0")
            parsed_count.setObjectName("hintLabel")
            card_layout.addWidget(parsed_count)

            parsed_list = QListWidget()
            parsed_list.setObjectName("presetList")
            parsed_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            card_layout.addWidget(parsed_list, 1)

            actions = QHBoxLayout()
            actions.addStretch(1)
            save_btn = QPushButton("Save details")
            save_btn.setProperty("variant", "secondary")
            actions.addWidget(save_btn)
            card_layout.addLayout(actions)

            def refresh_comments_preview() -> None:
                comments = self._parse_seeding_comments(seeding_edit.toPlainText().strip())
                parsed_count.setText(f"Parsed comments: {len(comments)}")
                parsed_list.clear()
                if not comments:
                    parsed_list.addItem(QListWidgetItem("Chưa có comment nào."))
                    return
                for comment in comments:
                    parsed_list.addItem(QListWidgetItem(comment))

            def save_details() -> None:
                updated = dict(child)
                updated["title"] = title_edit.text().strip()
                updated["description"] = description_edit.toPlainText().strip()
                updated["seeding_comments_raw"] = seeding_edit.toPlainText().strip()
                self._save_manual_child_project(preset_id, updated)
                if on_saved is not None:
                    on_saved()

            seeding_edit.textChanged.connect(refresh_comments_preview)
            save_btn.clicked.connect(save_details)
            refresh_comments_preview()

            details_layout.addWidget(card, 1)
            return details_page

        pages = [
            ("ai_gen", "AI Gen", build_details_page()),
            ("ai_audio", "AI Audio", self._build_child_tool_placeholder_page("AI Audio", "Manage AI audio workflows for the selected child project.")),
            ("livestream", "Livestream", self._build_child_tool_placeholder_page("Livestream", "Manage livestream workflows for the selected child project.")),
        ]

        for index, (_key, label, page_widget) in enumerate(pages):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("childToolNavButton")
            if index == 0:
                button.setChecked(True)
            tool_group.addButton(button, index)
            sidebar_layout.addWidget(button)
            tool_stack.addWidget(page_widget)
        sidebar_layout.addStretch(1)

        tool_group.idClicked.connect(tool_stack.setCurrentIndex)
        shell_layout.addWidget(sidebar)
        shell_layout.addWidget(content_host, 1)
        layout.addWidget(shell, 1)
        return page

    def _open_manual_child_project_workspace(
        self,
        preset_id: str,
        child_id: str,
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        preset = self._find_project_preset_by_id(preset_id)
        child = self._find_manual_child_project(preset_id, child_id)
        if preset is None or child is None:
            QMessageBox.information(self, "No project", "Child project not found.")
            return

        dialog = QDialog(self, Qt.WindowType.Window)
        dialog.setWindowTitle(f"{str(child.get('name', '')).strip()} - {str(preset.get('name', '')).strip()}")
        dialog.setObjectName("childProjectWorkspaceDialog")
        self._fit_window_to_screen(dialog, min_width=1280, min_height=740)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        heading = QLabel(str(child.get("name", "")).strip())
        heading.setObjectName("projectParentTitle")
        subtitle = QLabel("Double-click workspace for child project tools and detailed editing.")
        subtitle.setObjectName("workspaceBody")
        header_col.addWidget(heading)
        header_col.addWidget(subtitle)
        header_row.addLayout(header_col, 1)

        back_btn = QPushButton("Back")
        back_btn.setProperty("variant", "primary")
        back_btn.setIcon(dialog.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        back_btn.setIconSize(QSize(16, 16))
        back_btn.clicked.connect(dialog.accept)
        header_row.addWidget(back_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header_row)

        workspace = self._build_manual_child_project_workspace(
            preset_id,
            child_id,
            on_saved=on_saved,
        )
        if workspace is None:
            QMessageBox.information(self, "No project", "Child project not found.")
            return
        layout.addWidget(workspace, 1)
        dialog.exec()

    def _handle_project_parent_selection_changed(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        self._selected_project_preset_id = str(current.data(Qt.ItemDataRole.UserRole) or "").strip() if current else ""
        self._refresh_projects_workspace()

    def _refresh_projects_workspace(self, selected_preset_id: str = "") -> None:
        if self.project_parent_list is None:
            return

        current_item = self.project_parent_list.currentItem()
        current_id = (
            str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if current_item is not None
            else ""
        )
        target_id = str(selected_preset_id).strip() or self._selected_project_preset_id or current_id
        self.project_parent_list.blockSignals(True)
        self.project_parent_list.clear()

        children_by_parent: dict[str, int] = {
            str(preset.get("id", "")): len(
                self._manual_child_projects_for_parent(str(preset.get("id", "")))
            )
            for preset in self.project_presets
        }

        for preset in self._sorted_project_presets():
            preset_id = str(preset.get("id", "")).strip()
            item = QListWidgetItem(
                f"{str(preset.get('name', '')).strip()}\n{children_by_parent.get(preset_id, 0)} child project(s)"
            )
            item.setData(Qt.ItemDataRole.UserRole, preset_id)
            item.setToolTip(
                "Keywords: "
                + (str(preset.get("keywords_raw", "")).strip() or "(empty)")
                + "\nAuthor: "
                + (str(preset.get("author", "")).strip() or "(empty)")
                + "\nPublisher: "
                + (str(preset.get("publisher", "")).strip() or "(empty)")
            )
            item.setIcon(self._make_symbol_icon("projects", "#0B57D0"))
            self.project_parent_list.addItem(item)

        if self.project_parent_empty_label is not None:
            self.project_parent_empty_label.setVisible(self.project_parent_list.count() == 0)
        self.project_parent_list.setVisible(self.project_parent_list.count() > 0)

        resolved_id = target_id
        if self.project_parent_list.count():
            matched_item: QListWidgetItem | None = None
            for index in range(self.project_parent_list.count()):
                item = self.project_parent_list.item(index)
                if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() == target_id:
                    matched_item = item
                    break
            if matched_item is None:
                matched_item = self.project_parent_list.item(0)
            self.project_parent_list.setCurrentItem(matched_item)
            resolved_id = str(matched_item.data(Qt.ItemDataRole.UserRole) or "").strip()
        else:
            resolved_id = ""
        self.project_parent_list.blockSignals(False)

        self._selected_project_preset_id = resolved_id
        preset = self._find_project_preset_by_id(resolved_id)
        child_count = children_by_parent.get(resolved_id, 0)

        if self.project_parent_detail_name is not None:
            self.project_parent_detail_name.setText(
                str(preset.get("name", "")).strip() if preset else "Select a parent project"
            )
        # detail_meta hidden — keywords/author/publisher removed from display
        if self.project_parent_detail_stats is not None:
            self.project_parent_detail_stats.setText(f"Child projects: {child_count}" if preset else "Child projects: 0")
        if self.project_parent_open_children_btn is not None:
            self.project_parent_open_children_btn.setText(
                f"Open child projects ({child_count})" if preset else "Open child projects"
            )

        has_selection = preset is not None
        for button in (
            self.project_parent_edit_btn,
            self.project_parent_delete_btn,
            self.project_parent_open_children_btn,
        ):
            if button is not None:
                button.setEnabled(has_selection)

    def _refresh_project_preset_tab(self, selected_preset_id: str = "") -> None:
        self._refresh_projects_workspace(selected_preset_id)
        if self.project_hierarchy_layout is None:
            return

        while self.project_hierarchy_layout.count():
            item = self.project_hierarchy_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not self.project_presets:
            empty = QLabel("Chưa có project cha. Nhấn 'Add Project' để tạo project đầu tiên.")
            empty.setObjectName("hintLabel")
            empty.setWordWrap(True)
            self.project_hierarchy_layout.addWidget(empty)
            self.project_hierarchy_layout.addStretch(1)
            return

        children_by_parent: dict[str, list[dict[str, object]]] = {
            str(preset.get("id", "")): self._manual_child_projects_for_parent(str(preset.get("id", "")))
            for preset in self.project_presets
        }

        for preset in self._sorted_project_presets():
            parent_id = str(preset.get("id", ""))
            children = children_by_parent.get(parent_id, [])

            card = QFrame()
            card.setObjectName("projectParentCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(8)

            header_row = QHBoxLayout()
            title = QLabel(str(preset.get("name", "")).strip())
            title.setObjectName("projectParentTitle")
            header_row.addWidget(title)

            count = QLabel(f"{len(children)} project(s)")
            count.setObjectName("projectParentCount")
            header_row.addWidget(count)
            header_row.addStretch(1)

            edit_btn = QPushButton("Edit")
            edit_btn.setProperty("variant", "secondary")
            edit_btn.clicked.connect(
                lambda _checked=False, pid=parent_id: self._handle_project_preset_edit(pid)
            )
            header_row.addWidget(edit_btn)

            delete_btn = QPushButton("Delete")
            delete_btn.setProperty("variant", "danger")
            delete_btn.clicked.connect(
                lambda _checked=False, pid=parent_id: self._handle_project_preset_delete(pid)
            )
            header_row.addWidget(delete_btn)
            card_layout.addLayout(header_row)

            meta = QLabel(
                "Keywords: "
                + (str(preset.get("keywords_raw", "")).strip() or "(empty)")
                + " | Author: "
                + (str(preset.get("author", "")).strip() or "(empty)")
                + " | Publisher: "
                + (str(preset.get("publisher", "")).strip() or "(empty)")
            )
            meta.setObjectName("projectParentMeta")
            meta.setWordWrap(True)
            card_layout.addWidget(meta)

            open_children_btn = QPushButton(
                f"Open Child Projects ({len(children)})" if children else "Open Child Projects"
            )
            open_children_btn.setProperty("variant", "primary")
            open_children_btn.clicked.connect(
                lambda _checked=False, pid=parent_id: self._open_project_children_dialog(pid)
            )
            card_layout.addWidget(open_children_btn, 0, Qt.AlignmentFlag.AlignLeft)

            self.project_hierarchy_layout.addWidget(card)

        self.project_hierarchy_layout.addStretch(1)

    def _sync_project_preset_changes(self, preferred_id: str = "") -> None:
        self._save_project_dashboard_settings()
        self._refresh_project_preset_tab(preferred_id)
        self._populate_table()

    def _handle_project_preset_add(self) -> None:
        payload = self._open_project_preset_editor()
        if payload is None:
            return
        payload["id"] = uuid4().hex[:12]
        self.project_presets.append(payload)
        self._sync_project_preset_changes(str(payload["id"]))

    def _handle_project_preset_edit(self, preset_id: str = "") -> None:
        preset = self._find_project_preset_by_id(preset_id)
        if preset is None:
            QMessageBox.information(self, "No project", "Project not found.")
            return
        payload = self._open_project_preset_editor(initial=preset)
        if payload is None:
            return
        payload["id"] = str(preset.get("id", ""))
        for index, item in enumerate(self.project_presets):
            if str(item.get("id", "")) == payload["id"]:
                self.project_presets[index] = payload
                break
        self._sync_project_preset_changes(str(payload["id"]))

    def _handle_project_preset_delete(self, preset_id: str = "") -> None:
        preset = self._find_project_preset_by_id(preset_id)
        if preset is None:
            QMessageBox.information(self, "No project", "Project not found.")
            return
        name = str(preset.get("name", "")).strip() or "this preset"
        reply = QMessageBox.question(
            self,
            "Delete preset",
            f"Delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        preset_id = str(preset.get("id", ""))
        self.project_presets = [
            item for item in self.project_presets if str(item.get("id", "")) != preset_id
        ]
        self.project_assignment_by_path = {
            path: assigned
            for path, assigned in self.project_assignment_by_path.items()
            if assigned != preset_id
        }
        for project in self.projects:
            if project.assigned_project_id == preset_id:
                project.assigned_project_id = ""
        self._sync_project_preset_changes()

    # region project table helpers
    @staticmethod
    def _status_color(status: ProjectStatus) -> QColor:
        palette = {
            ProjectStatus.pending: QColor("#5F6368"),
            ProjectStatus.processing: QColor("#0B57D0"),
            ProjectStatus.done: QColor("#137333"),
            ProjectStatus.failed: QColor("#C5221F"),
        }
        return palette.get(status, QColor("#444746"))

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
        valid_ids = self._project_preset_ids()
        cleaned_assignment = False
        for project in self.projects:
            previous_order = selected_order_by_path.get(project.path)
            project.is_selected = previous_order is not None
            project.selection_order = previous_order
            assigned_id = str(self.project_assignment_by_path.get(project.path, "")).strip()
            if assigned_id and assigned_id not in valid_ids:
                assigned_id = ""
                self.project_assignment_by_path.pop(project.path, None)
                cleaned_assignment = True
            project.assigned_project_id = assigned_id
            self._refresh_project_metadata(project)
        if cleaned_assignment:
            self._save_project_dashboard_settings()
        self._normalize_selection_orders()
        if not self.projects:
            logger.warning("No CapCut projects found. Check your CapCut library path.")
        self._populate_table()
        self._refresh_project_preset_tab()
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
            name_item.setToolTip(project.notes)
            source_item = QTableWidgetItem(project.source.label())
            status_item = QTableWidgetItem(project.status.label())
            status_item.setForeground(QBrush(self._status_color(project.status)))

            for column, item in (
                (self.columns.name, name_item),
                (self.columns.source, source_item),
                (self.columns.status, status_item),
            ):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.project_table.setItem(row, column, item)

            project_combo = QComboBox()
            project_combo.setObjectName("rowProjectPresetCombo")
            project_combo.addItem("Select project", "")
            for preset in self._sorted_project_presets():
                project_combo.addItem(
                    str(preset.get("name", "")).strip(),
                    str(preset.get("id", "")).strip(),
                )
            selected_index = project_combo.findData(project.assigned_project_id)
            if selected_index < 0:
                selected_index = 0
                project.assigned_project_id = ""
            project_combo.setCurrentIndex(selected_index)
            self._update_project_combo_visual_state(project_combo)

            def _on_project_changed(_index: int, p=project, combo=project_combo) -> None:
                self._update_project_combo_visual_state(combo)
                self._handle_project_assignment_changed(p, combo.currentData())

            project_combo.currentIndexChanged.connect(_on_project_changed)
            project_container = QWidget()
            project_container_layout = QHBoxLayout(project_container)
            project_container_layout.setContentsMargins(8, 4, 8, 4)
            project_container_layout.setSpacing(0)
            project_container_layout.addWidget(project_combo)
            self.project_table.setCellWidget(row, self.columns.project, project_container)

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
            name_item = self.project_table.item(row, self.columns.name)
            status_item = self.project_table.item(row, self.columns.status)
            if name_item is not None:
                name_item.setToolTip(project.notes)
            if status_item is not None:
                status_item.setText(project.status.label())
                status_item.setForeground(QBrush(self._status_color(project.status)))
        self._refresh_project_preset_tab()
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

    # ── Raw SEO handlers ────────────────────────────────────────────────────

    def _set_raw_seo_files(self, files: list[Path]) -> None:
        unique_files: list[Path] = []
        seen: set[str] = set()
        for file_path in files:
            key = str(file_path)
            if key in seen:
                continue
            seen.add(key)
            unique_files.append(file_path)

        self.raw_seo_files = unique_files

        if hasattr(self, "raw_seo_file_list"):
            self.raw_seo_file_list.clear()
            for file_path in unique_files:
                item = QListWidgetItem(file_path.name)
                item.setToolTip(str(file_path))
                self.raw_seo_file_list.addItem(item)
            if unique_files and self.raw_seo_file_list.count() > 0:
                self.raw_seo_file_list.setCurrentRow(0)

        if hasattr(self, "raw_seo_files_edit"):
            if unique_files:
                self.raw_seo_files_edit.setText(f"{len(unique_files)} file(s) selected")
            else:
                self.raw_seo_files_edit.clear()

    @staticmethod
    def _default_raw_seo_draft() -> dict[str, object]:
        return {
            "title": "",
            "description": "",
            "keywords_raw": "",
            "author": "",
            "publisher": "",
            "copyright": "",
            "comment": "",
            "rating": -1,
            "language": "",
            "website": "",
            "date_created": "",
            "city": "",
            "country": "",
            "custom_tags_raw": "",
        }

    def _clear_raw_seo_metadata_draft(self) -> None:
        self.raw_seo_draft = self._default_raw_seo_draft()
        self._update_raw_seo_draft_summary()
        if hasattr(self, "raw_seo_result_label"):
            self.raw_seo_result_label.setText("Metadata fields reset.")

    def _update_raw_seo_draft_summary(self) -> None:
        if not hasattr(self, "raw_seo_draft_summary_label"):
            return
        title = str(self.raw_seo_draft.get("title", "")).strip()
        desc = str(self.raw_seo_draft.get("description", "")).strip()
        keywords = parse_keywords(str(self.raw_seo_draft.get("keywords_raw", "")))
        custom_lines = [
            ln.strip()
            for ln in str(self.raw_seo_draft.get("custom_tags_raw", "")).splitlines()
            if ln.strip()
        ]
        advanced_count = 0
        for key in (
            "author",
            "publisher",
            "copyright",
            "comment",
            "language",
            "website",
            "date_created",
            "city",
            "country",
        ):
            if str(self.raw_seo_draft.get(key, "")).strip():
                advanced_count += 1
        if int(self.raw_seo_draft.get("rating", -1)) >= 0:
            advanced_count += 1
        advanced_count += len(custom_lines)

        parts = [
            f"Title: {'set' if title else 'empty'}",
            f"Description: {'set' if desc else 'empty'}",
            f"Keywords: {len(keywords)}",
            f"Advanced tags: {advanced_count}",
        ]
        self.raw_seo_draft_summary_label.setText(" | ".join(parts))

    def _open_raw_seo_editor(self) -> None:
        draft = dict(self.raw_seo_draft)
        dialog = QDialog(self)
        dialog.setObjectName("rawSeoEditorDialog")
        dialog.setWindowTitle("Raw SEO Metadata Editor")
        dialog.resize(980, 760)
        dialog.setMinimumSize(860, 620)
        dialog.setStyleSheet(
            """
            QDialog#rawSeoEditorDialog {
                background: #F8FAFC;
            }
            QFrame#rawSeoCard {
                background: transparent;
                border: none;
                border-radius: 0px;
            }
            QLabel#rawSeoHeading {
                font-size: 20px;
                font-weight: 700;
                color: #0F172A;
                background: transparent;
                border: none;
            }
            QLabel#rawSeoSubheading {
                font-size: 13px;
                color: #64748B;
                background: transparent;
                border: none;
            }
            QDialog#rawSeoEditorDialog QLabel#sectionLabel {
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                color: #475569;
            }
            QDialog#rawSeoEditorDialog QLineEdit:focus,
            QDialog#rawSeoEditorDialog QTextEdit:focus,
            QDialog#rawSeoEditorDialog QSpinBox:focus {
                border: 1px solid #CBD5E1;
                background: #FFFFFF;
                outline: none;
            }
            """
        )

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(18, 18, 18, 16)
        outer.setSpacing(12)

        heading = QLabel("Raw SEO Metadata Editor")
        heading.setObjectName("rawSeoHeading")
        outer.addWidget(heading)

        subheading = QLabel("Edit metadata fields, then click Save to apply to Raw SEO.")
        subheading.setObjectName("rawSeoSubheading")
        subheading.setWordWrap(True)
        outer.addWidget(subheading)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        form_root = QWidget()
        form = QVBoxLayout(form_root)
        form.setContentsMargins(4, 0, 4, 0)
        form.setSpacing(12)

        basic_frame = QFrame()
        basic_frame.setObjectName("rawSeoCard")
        basic_layout = QGridLayout(basic_frame)
        basic_layout.setContentsMargins(14, 12, 14, 12)
        basic_layout.setHorizontalSpacing(10)
        basic_layout.setVerticalSpacing(8)

        basic_title = QLabel("Basic SEO Fields")
        basic_title.setObjectName("sectionLabel")
        basic_layout.addWidget(basic_title, 0, 0, 1, 2)

        title_label = QLabel("Title")
        title_label.setObjectName("sectionLabel")
        basic_layout.addWidget(title_label, 1, 0, 1, 2)
        title_edit = QLineEdit()
        title_edit.setPlaceholderText("Keyword chính + hook tiêu đề…")
        title_edit.setText(str(draft.get("title", "")))
        basic_layout.addWidget(title_edit, 2, 0, 1, 2)

        desc_label = QLabel("Description")
        desc_label.setObjectName("sectionLabel")
        basic_layout.addWidget(desc_label, 3, 0)
        desc_edit = QTextEdit()
        desc_edit.setPlaceholderText("Mô tả SEO cho video/ảnh…")
        desc_edit.setPlainText(str(draft.get("description", "")))
        desc_edit.setFixedHeight(118)
        basic_layout.addWidget(desc_edit, 4, 0)

        keywords_label = QLabel("Keywords")
        keywords_label.setObjectName("sectionLabel")
        basic_layout.addWidget(keywords_label, 3, 1)
        keywords_edit = QTextEdit()
        keywords_edit.setPlaceholderText("kw1, kw2, kw3 (comma/new line separated)")
        keywords_edit.setPlainText(str(draft.get("keywords_raw", "")))
        keywords_edit.setFixedHeight(118)
        basic_layout.addWidget(keywords_edit, 4, 1)
        form.addWidget(basic_frame)

        advanced_frame = QFrame()
        advanced_frame.setObjectName("rawSeoCard")
        advanced_grid = QGridLayout(advanced_frame)
        advanced_grid.setContentsMargins(14, 12, 14, 12)
        advanced_grid.setHorizontalSpacing(10)
        advanced_grid.setVerticalSpacing(8)

        advanced_title = QLabel("Advanced Metadata")
        advanced_title.setObjectName("sectionLabel")
        advanced_grid.addWidget(advanced_title, 0, 0, 1, 2)

        author_label = QLabel("Author")
        author_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(author_label, 1, 0)
        author_edit = QLineEdit()
        author_edit.setPlaceholderText("Creator / Artist")
        author_edit.setText(str(draft.get("author", "")))
        advanced_grid.addWidget(author_edit, 2, 0)

        publisher_label = QLabel("Publisher")
        publisher_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(publisher_label, 1, 1)
        publisher_edit = QLineEdit()
        publisher_edit.setPlaceholderText("Channel / Publisher name")
        publisher_edit.setText(str(draft.get("publisher", "")))
        advanced_grid.addWidget(publisher_edit, 2, 1)

        copyright_label = QLabel("Copyright")
        copyright_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(copyright_label, 3, 0)
        copyright_edit = QLineEdit()
        copyright_edit.setPlaceholderText("Copyright notice")
        copyright_edit.setText(str(draft.get("copyright", "")))
        advanced_grid.addWidget(copyright_edit, 4, 0)

        comment_label = QLabel("Comment")
        comment_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(comment_label, 3, 1)
        comment_edit = QLineEdit()
        comment_edit.setPlaceholderText("Optional comment metadata")
        comment_edit.setText(str(draft.get("comment", "")))
        advanced_grid.addWidget(comment_edit, 4, 1)

        rating_label = QLabel("Rating (0-5)")
        rating_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(rating_label, 5, 0)
        rating_spin = QSpinBox()
        rating_spin.setRange(-1, 5)
        rating_spin.setSpecialValueText("Skip")
        rating_spin.setValue(int(draft.get("rating", -1)))
        advanced_grid.addWidget(rating_spin, 6, 0)

        language_label = QLabel("Language")
        language_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(language_label, 5, 1)
        language_edit = QLineEdit()
        language_edit.setPlaceholderText("e.g. en, ko, vi")
        language_edit.setText(str(draft.get("language", "")))
        advanced_grid.addWidget(language_edit, 6, 1)

        website_label = QLabel("Website")
        website_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(website_label, 7, 0)
        website_edit = QLineEdit()
        website_edit.setPlaceholderText("https://example.com")
        website_edit.setText(str(draft.get("website", "")))
        advanced_grid.addWidget(website_edit, 8, 0)

        date_created_label = QLabel("Date Created")
        date_created_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(date_created_label, 7, 1)
        raw_date_created = str(draft.get("date_created", "")).strip()
        date_created_edit = QDateTimeEdit()
        date_created_edit.setDisplayFormat("yyyy:MM:dd HH:mm:ss")
        date_created_edit.setCalendarPopup(True)
        parsed_date = QDateTime.fromString(raw_date_created, "yyyy:MM:dd HH:mm:ss")
        if not parsed_date.isValid():
            parsed_date = QDateTime.fromString(raw_date_created, "yyyy-MM-dd HH:mm:ss")
        if not parsed_date.isValid():
            parsed_date = QDateTime.currentDateTime()
        date_created_edit.setDateTime(parsed_date)
        advanced_grid.addWidget(date_created_edit, 8, 1)

        city_label = QLabel("City")
        city_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(city_label, 9, 0)
        city_edit = QLineEdit()
        city_edit.setPlaceholderText("City")
        city_edit.setText(str(draft.get("city", "")))
        advanced_grid.addWidget(city_edit, 10, 0)

        country_label = QLabel("Country")
        country_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(country_label, 9, 1)
        country_edit = QLineEdit()
        country_edit.setPlaceholderText("Country")
        country_edit.setText(str(draft.get("country", "")))
        advanced_grid.addWidget(country_edit, 10, 1)

        custom_tags_label = QLabel("Custom Tags (Tag=Value, one per line)")
        custom_tags_label.setObjectName("sectionLabel")
        advanced_grid.addWidget(custom_tags_label, 11, 0, 1, 2)
        custom_tags_edit = QTextEdit()
        custom_tags_edit.setPlaceholderText("Examples:\nXMP-dc:Publisher=My Channel\nSoftware=AutoCapCut")
        custom_tags_edit.setPlainText(str(draft.get("custom_tags_raw", "")))
        custom_tags_edit.setFixedHeight(96)
        advanced_grid.addWidget(custom_tags_edit, 12, 0, 1, 2)
        form.addWidget(advanced_frame)

        scroll.setWidget(form_root)
        outer.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        save_btn = QPushButton("Save")
        save_btn.setProperty("variant", "primary")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setMinimumWidth(96)
        save_btn.setMinimumWidth(96)
        actions.addWidget(cancel_btn)
        actions.addWidget(save_btn)
        outer.addLayout(actions)

        def save_and_close() -> None:
            self.raw_seo_draft = {
                "title": title_edit.text().strip(),
                "description": desc_edit.toPlainText().strip(),
                "keywords_raw": keywords_edit.toPlainText(),
                "author": author_edit.text().strip(),
                "publisher": publisher_edit.text().strip(),
                "copyright": copyright_edit.text().strip(),
                "comment": comment_edit.text().strip(),
                "rating": rating_spin.value(),
                "language": language_edit.text().strip(),
                "website": website_edit.text().strip(),
                "date_created": date_created_edit.dateTime().toString("yyyy:MM:dd HH:mm:ss"),
                "city": city_edit.text().strip(),
                "country": country_edit.text().strip(),
                "custom_tags_raw": custom_tags_edit.toPlainText(),
            }
            self._update_raw_seo_draft_summary()
            if hasattr(self, "raw_seo_result_label"):
                self.raw_seo_result_label.setText("Metadata fields saved.")
            dialog.accept()

        save_btn.clicked.connect(save_and_close)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec()

    @staticmethod
    def _collect_raw_seo_advanced_tags_from_draft(draft: dict[str, object]) -> dict[str, str]:
        tags: dict[str, str] = {}
        author = str(draft.get("author", "")).strip()
        if author:
            tags["XMP-dc:Creator"] = author
            tags["Artist"] = author

        publisher = str(draft.get("publisher", "")).strip()
        if publisher:
            tags["XMP-dc:Publisher"] = publisher

        copyright_text = str(draft.get("copyright", "")).strip()
        if copyright_text:
            tags["XMP-dc:Rights"] = copyright_text
            tags["Copyright"] = copyright_text

        comment = str(draft.get("comment", "")).strip()
        if comment:
            tags["Comment"] = comment

        rating_value = int(draft.get("rating", -1))
        if rating_value >= 0:
            tags["XMP-xmp:Rating"] = str(rating_value)

        language = str(draft.get("language", "")).strip()
        if language:
            tags["XMP-dc:Language"] = language

        website = str(draft.get("website", "")).strip()
        if website:
            tags["URL"] = website
            tags["XMP-xmpRights:WebStatement"] = website

        date_created = str(draft.get("date_created", "")).strip()
        if date_created:
            tags["XMP-photoshop:DateCreated"] = date_created

        city = str(draft.get("city", "")).strip()
        if city:
            tags["XMP-photoshop:City"] = city

        country = str(draft.get("country", "")).strip()
        if country:
            tags["XMP-photoshop:Country"] = country

        raw_custom = str(draft.get("custom_tags_raw", ""))
        if raw_custom.strip():
            for line_number, raw_line in enumerate(raw_custom.splitlines(), 1):
                line = raw_line.strip()
                if not line:
                    continue
                if "=" not in line:
                    raise RawSEOError(f"Invalid custom tag at line {line_number}: expected Tag=Value")
                tag, value = line.split("=", 1)
                tag = tag.strip()
                value = value.strip()
                if not tag or not value:
                    raise RawSEOError(f"Invalid custom tag at line {line_number}: expected Tag=Value")
                tags[tag] = value
        return tags

    def _collect_raw_seo_input(self) -> tuple[str, str, list[str], dict[str, str]]:
        title = str(self.raw_seo_draft.get("title", "")).strip()
        description = str(self.raw_seo_draft.get("description", "")).strip()
        keywords_raw = str(self.raw_seo_draft.get("keywords_raw", ""))
        keywords = parse_keywords(keywords_raw)
        advanced_tags = self._collect_raw_seo_advanced_tags_from_draft(self.raw_seo_draft)
        if not any((title, description, keywords, advanced_tags)):
            raise RawSEOError(
                "Please click 'Edit Metadata Fields' and fill at least one value before applying."
            )
        return title, description, keywords, advanced_tags

    def _handle_select_raw_seo_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select files for Raw SEO",
            str(Path.home()),
            (
                "Media files (*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.mp3 *.m4a *.wav *.flac "
                "*.jpg *.jpeg *.png *.webp *.heic *.tif *.tiff);;All files (*)"
            ),
        )
        if files:
            self._set_raw_seo_files([Path(path) for path in files])

    def _handle_clear_raw_seo_files(self) -> None:
        self._set_raw_seo_files([])
        if hasattr(self, "raw_seo_result_label"):
            self.raw_seo_result_label.setText("")

    def _selected_raw_seo_file(self) -> Path | None:
        if hasattr(self, "raw_seo_file_list"):
            selected_items = self.raw_seo_file_list.selectedItems()
            if selected_items:
                tooltip = selected_items[0].toolTip()
                if tooltip:
                    return Path(tooltip)
            current_item = self.raw_seo_file_list.currentItem()
            if current_item and current_item.toolTip():
                return Path(current_item.toolTip())
        if self.raw_seo_files:
            return self.raw_seo_files[0]
        return None

    def _current_raw_seo_additional_tags(self) -> list[str]:
        try:
            return list(self._collect_raw_seo_advanced_tags_from_draft(self.raw_seo_draft).keys())
        except RawSEOError:
            return []

    @staticmethod
    def _format_raw_seo_metadata(metadata: dict[str, object]) -> str:
        preferred_order = [
            "Title",
            "Title-en-US",
            "QuickTime:Title",
            "ItemList:Title",
            "Keys:Title",
            "Description",
            "Description-en-US",
            "QuickTime:Description",
            "ItemList:Description",
            "Keys:Description",
            "Keywords",
            "Subject",
            "XMP-dc:Title",
            "XMP-dc:Description",
            "XMP-dc:Subject",
            "XMP-dc:Creator",
            "Creator",
            "Artist",
            "XMP-dc:Publisher",
            "XMP-dc:Rights",
            "Rights",
            "Copyright",
            "Comment",
            "XMP-xmp:Rating",
            "Rating",
            "XMP-dc:Language",
            "Language",
            "URL",
            "XMP-xmpRights:WebStatement",
            "XMP-photoshop:DateCreated",
            "XMP-photoshop:City",
            "XMP-photoshop:Country",
        ]
        lines: list[str] = []
        for key in preferred_order:
            if key not in metadata:
                continue
            value = metadata[key]
            if isinstance(value, list):
                rendered = ", ".join(str(item) for item in value)
            else:
                rendered = str(value)
            lines.append(f"{key}: {rendered}")

        extras = [key for key in metadata.keys() if key not in preferred_order and key != "SourceFile"]
        for key in sorted(extras):
            value = metadata[key]
            if isinstance(value, list):
                rendered = ", ".join(str(item) for item in value)
            else:
                rendered = str(value)
            lines.append(f"{key}: {rendered}")
        return "\n".join(lines) if lines else "No SEO metadata fields found."

    def _show_raw_seo_metadata_dialog(self, file_path: Path, metadata_text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Raw SEO Metadata")
        dialog.resize(720, 460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        file_label = QLabel(str(file_path))
        file_label.setObjectName("hintLabel")
        file_label.setWordWrap(True)
        layout.addWidget(file_label)

        text_box = QTextEdit()
        text_box.setReadOnly(True)
        text_box.setPlainText(metadata_text)
        layout.addWidget(text_box, 1)

        actions = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        copy_btn.setProperty("variant", "secondary")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(metadata_text))
        actions.addWidget(copy_btn)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.setProperty("variant", "secondary")
        open_folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path.parent)))
        )
        actions.addWidget(open_folder_btn)
        actions.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.setProperty("variant", "primary")
        close_btn.clicked.connect(dialog.accept)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        dialog.exec()

    def _handle_raw_seo_view_metadata(self) -> None:
        target = self._selected_raw_seo_file()
        if target is None:
            QMessageBox.information(self, "No file", "Select a file in Raw SEO list first.")
            return

        self._set_job_controls_state(True)
        try:
            metadata = read_raw_seo_metadata(
                target,
                additional_tags=self._current_raw_seo_additional_tags(),
            )
        except RawSEOError as exc:
            QMessageBox.warning(self, "View metadata failed", str(exc))
            return
        finally:
            self._set_job_controls_state(False)

        rendered = self._format_raw_seo_metadata(metadata)
        self._show_raw_seo_metadata_dialog(target, rendered)

    def _show_raw_seo_result_dialog(
        self,
        *,
        title: str,
        message: str,
        metadata_file: Path | None,
        folder_to_open: Path | None,
        additional_tags: list[str] | None,
        warning: bool,
    ) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        if metadata_file is not None:
            view_metadata_btn = dialog.addButton("View Metadata", QMessageBox.ButtonRole.ActionRole)
        else:
            view_metadata_btn = None
        if folder_to_open is not None:
            dialog.setInformativeText(str(folder_to_open))
            open_folder_btn = dialog.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        else:
            open_folder_btn = None
        close_btn = dialog.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
        dialog.setDefaultButton(close_btn)
        dialog.exec()

        clicked = dialog.clickedButton()
        if open_folder_btn is not None and clicked == open_folder_btn:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_to_open)))
            if not opened:
                QMessageBox.warning(self, "Open folder failed", f"Could not open folder:\n{folder_to_open}")
            return

        if view_metadata_btn is not None and clicked == view_metadata_btn and metadata_file is not None:
            try:
                metadata = read_raw_seo_metadata(metadata_file, additional_tags=additional_tags)
            except RawSEOError as exc:
                QMessageBox.warning(self, "View metadata failed", str(exc))
                return
            rendered = self._format_raw_seo_metadata(metadata)
            self._show_raw_seo_metadata_dialog(metadata_file, rendered)
            return

    def _handle_raw_seo_apply(self) -> None:
        if not self.raw_seo_files:
            QMessageBox.information(self, "No files", "Select at least one file before applying Raw SEO.")
            return

        try:
            title, description, keywords, advanced_tags = self._collect_raw_seo_input()
        except RawSEOError as exc:
            QMessageBox.information(self, "Missing metadata", str(exc))
            return
        strict_mode = (
            self.raw_seo_strict_check.isChecked()
            if hasattr(self, "raw_seo_strict_check")
            else False
        )

        progress = QProgressDialog("Preparing Raw SEO...", None, 0, len(self.raw_seo_files), self)
        progress.setWindowTitle("Raw SEO")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        def update_progress(done: int, total: int, current: Path | None) -> None:
            if total and progress.maximum() != total:
                progress.setMaximum(total)
            progress.setValue(done)
            if current is not None:
                progress.setLabelText(f"Updating {current.name} ({done}/{total})")
            QApplication.processEvents()

        self._set_job_controls_state(True)
        try:
            summary = apply_raw_seo(
                self.raw_seo_files,
                title=title,
                description=description,
                keywords=keywords,
                extra_tags=advanced_tags,
                rename_to_title=True,
                strict_verify=strict_mode,
                progress_callback=update_progress,
            )
        except RawSEOError as exc:
            QMessageBox.warning(self, "Raw SEO failed", str(exc))
            return
        finally:
            self._set_job_controls_state(False)
            progress.close()
            progress.deleteLater()

        message_lines = [
            f"Processed: {summary.processed}",
            f"Success: {summary.succeeded}",
            f"Failed: {summary.failed}",
            f"Strict mode: {'ON' if strict_mode else 'OFF'}",
            f"Advanced tags: {len(advanced_tags)}",
        ]

        failed_items = [item for item in summary.results if not item.success]
        if failed_items:
            message_lines.append("Failed files:")
            for item in failed_items[:5]:
                message_lines.append(f"- {item.file.name}: {item.message}")
            if len(failed_items) > 5:
                message_lines.append(f"...and {len(failed_items) - 5} more")

        message = "\n".join(message_lines)
        if hasattr(self, "raw_seo_result_label"):
            self.raw_seo_result_label.setText(message)

        # Refresh selected file list because successful files may have been renamed to Title.
        self._set_raw_seo_files([item.file for item in summary.results])

        success_items = [item for item in summary.results if item.success]
        metadata_file: Path | None = None
        folder_to_open: Path | None = None
        if success_items:
            metadata_file = success_items[0].file
            folder_to_open = success_items[0].file.parent
        elif self.raw_seo_files:
            metadata_file = self.raw_seo_files[0]
            folder_to_open = self.raw_seo_files[0].parent

        self._show_raw_seo_result_dialog(
            title="Raw SEO complete with issues" if summary.failed else "Raw SEO complete",
            message=message,
            metadata_file=metadata_file,
            folder_to_open=folder_to_open,
            additional_tags=list(advanced_tags.keys()),
            warning=bool(summary.failed),
        )

    def _handle_raw_seo_export_payload(self) -> None:
        try:
            title, description, keywords, advanced_tags = self._collect_raw_seo_input()
        except RawSEOError as exc:
            QMessageBox.information(self, "Missing metadata", str(exc))
            return

        initial_dir = self.raw_seo_files[0].parent if self.raw_seo_files else Path.home()
        suggested = initial_dir / "raw_seo_payload.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Raw SEO payload",
            str(suggested),
            "JSON files (*.json)",
        )
        if not path:
            return

        output_path = Path(path)
        if output_path.suffix.lower() != ".json":
            output_path = output_path.with_suffix(".json")

        try:
            saved_path = write_raw_seo_payload(
                output_path,
                files=self.raw_seo_files,
                title=title,
                description=description,
                keywords=keywords,
                extra_tags=advanced_tags,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return

        if hasattr(self, "raw_seo_result_label"):
            self.raw_seo_result_label.setText(f"Payload saved: {saved_path}")
        QMessageBox.information(self, "Payload exported", f"Saved JSON payload:\n{saved_path}")

    # ── Roxy Upload handlers ───────────────────────────────────────────────

    def _show_roxy_notification_dialog(self, *, title: str, message: str, warning: bool = False) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(780, 420)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Upload details" if not warning else "Upload error details")
        header.setObjectName("sectionLabel")
        layout.addWidget(header)

        content = QTextEdit()
        content.setReadOnly(True)
        content.setPlainText(message)
        content.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(content, 1)

        actions = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        copy_btn.setProperty("variant", "secondary")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(message))
        actions.addWidget(copy_btn)
        actions.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.setProperty("variant", "primary")
        close_btn.clicked.connect(dialog.accept)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        dialog.exec()

    def _start_roxy_rate_timer(self) -> None:
        if self._roxy_rate_timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._refresh_roxy_rate_limit_label)
            self._roxy_rate_timer = timer
        if not self._roxy_rate_timer.isActive():
            self._roxy_rate_timer.start()

    def _refresh_roxy_rate_limit_label(self) -> None:
        if not hasattr(self, "roxy_rate_limit_label"):
            return
        snapshot = get_roxy_rate_limit_snapshot(default_limit=50)
        source = "server" if snapshot.source == "server-header" else "local"
        text = (
            f"API quota (60s): {snapshot.used_last_minute}/{snapshot.limit_per_minute} used "
            f"· {snapshot.remaining} remaining ({source})"
        )
        if snapshot.reset_in_seconds is not None:
            text += f" · reset in ~{snapshot.reset_in_seconds}s"
        self.roxy_rate_limit_label.setText(text)

    def _set_roxy_video_path(self, path: Path | None) -> None:
        self.roxy_video_path = path
        if hasattr(self, "roxy_video_path_edit"):
            if path is None:
                self.roxy_video_path_edit.clear()
            else:
                self.roxy_video_path_edit.setText(str(path))

    def _combo_roxy_profile_id(self) -> str:
        if not hasattr(self, "roxy_profile_combo"):
            return ""
        combo_text = self.roxy_profile_combo.currentText().strip().lower()
        combo_value = self.roxy_profile_combo.currentData()
        if combo_value is None:
            return ""
        value = str(combo_value).strip()
        if not value:
            return ""
        if "no profiles found" in combo_text:
            return ""
        return value

    def _handle_roxy_profile_changed(self, _index: int) -> None:
        selected_id = self._combo_roxy_profile_id()
        if hasattr(self, "roxy_profile_id_edit"):
            if selected_id:
                # Keep manual id in sync with the selected profile by default.
                self.roxy_profile_id_edit.setText(selected_id)
            elif not self.roxy_profile_id_edit.hasFocus():
                self.roxy_profile_id_edit.clear()

    def _selected_roxy_profile_id(self) -> str:
        combo_selected = self._combo_roxy_profile_id()
        if combo_selected:
            return combo_selected
        manual = self.roxy_profile_id_edit.text().strip() if hasattr(self, "roxy_profile_id_edit") else ""
        if manual:
            return manual
        return ""

    def _remember_roxy_settings(self) -> None:
        if hasattr(self, "roxy_api_host_edit"):
            self._settings.setValue("roxy/api_host", self.roxy_api_host_edit.text().strip())
        if hasattr(self, "roxy_api_key_edit"):
            self._settings.setValue("roxy/api_key", self.roxy_api_key_edit.text().strip())
        if hasattr(self, "roxy_workspace_spin"):
            self._settings.setValue("roxy/workspace_id", int(self.roxy_workspace_spin.value()))
        selected_profile = self._selected_roxy_profile_id()
        if selected_profile:
            self._settings.setValue("roxy/profile_id", selected_profile)
        if self.roxy_video_path is not None:
            self._settings.setValue("roxy/video_path", str(self.roxy_video_path))
        self._settings.sync()

    def _handle_roxy_select_video(self, _checked: bool = False) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select video to upload",
            str(self.roxy_video_path.parent if self.roxy_video_path else Path.home()),
            "Video files (*.mp4 *.mov *.m4v *.mkv *.avi *.webm);;All files (*)",
        )
        if path:
            self._set_roxy_video_path(Path(path))
            self._remember_roxy_settings()

    def _handle_roxy_load_profiles(self, silent: bool = False) -> None:
        if not hasattr(self, "roxy_profile_combo"):
            return

        api_host = self.roxy_api_host_edit.text().strip() if hasattr(self, "roxy_api_host_edit") else ""
        api_key = self.roxy_api_key_edit.text().strip() if hasattr(self, "roxy_api_key_edit") else ""
        workspace_id = self.roxy_workspace_spin.value() if hasattr(self, "roxy_workspace_spin") else 1

        if not api_host:
            if not silent:
                QMessageBox.information(self, "Missing API host", "Please fill in Roxy API host first.")
            return
        if not api_key:
            if not silent:
                QMessageBox.information(self, "Missing API key", "Please fill in Roxy API key first.")
            return

        previous_profile = self._selected_roxy_profile_id()
        self._set_job_controls_state(True)
        try:
            client = RoxyApiClient(api_host, api_key)
            workspace_note = ""
            workspaces = client.list_workspaces()
            if workspaces:
                available_ids = {item.workspace_id for item in workspaces}
                if workspace_id not in available_ids:
                    workspace_id = workspaces[0].workspace_id
                    if hasattr(self, "roxy_workspace_spin"):
                        self.roxy_workspace_spin.setValue(workspace_id)
                    label = workspaces[0].workspace_name or str(workspace_id)
                    workspace_note = f"Auto-selected workspace: {label} ({workspace_id}). "
            profiles = client.list_profiles(workspace_id)
        except RoxyUploadError as exc:
            if hasattr(self, "roxy_result_label"):
                self.roxy_result_label.setText(f"Load profiles failed: {exc}")
            if not silent:
                QMessageBox.warning(self, "Roxy API error", str(exc))
            return
        finally:
            self._set_job_controls_state(False)

        self.roxy_profile_combo.blockSignals(True)
        self.roxy_profile_combo.clear()
        if profiles:
            for profile in profiles:
                self.roxy_profile_combo.addItem(
                    f"{profile.display_name} · {profile.dir_id[:10]}...",
                    profile.dir_id,
                )
            target_profile = previous_profile or self._settings.value("roxy/profile_id", "", type=str)
            if target_profile:
                index = self.roxy_profile_combo.findData(target_profile)
                if index >= 0:
                    self.roxy_profile_combo.setCurrentIndex(index)
            message = f"{workspace_note}Loaded {len(profiles)} profile(s) from workspace {workspace_id}."
        else:
            self.roxy_profile_combo.addItem("No profiles found", "")
            message = f"{workspace_note}No profiles found in workspace {workspace_id}."
        self.roxy_profile_combo.blockSignals(False)
        self._handle_roxy_profile_changed(self.roxy_profile_combo.currentIndex())

        if hasattr(self, "roxy_result_label"):
            self.roxy_result_label.setText(message)
        self._remember_roxy_settings()

    def _run_roxy_preflight(
        self,
        *,
        api_host: str,
        api_key: str,
        workspace_id: int,
        profile_id: str,
        video_path: Path,
    ) -> RoxyPreflightResult | None:
        progress = QProgressDialog("Running upload preflight...", None, 0, 0, self)
        progress.setWindowTitle("Roxy Preflight")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        try:
            result = run_roxy_upload_preflight(
                api_host=api_host,
                api_token=api_key,
                workspace_id=workspace_id,
                profile_id=profile_id,
                video_path=video_path,
            )
        except RoxyUploadError as exc:
            if hasattr(self, "roxy_result_label"):
                self.roxy_result_label.setText(f"Preflight failed: {exc}")
            QMessageBox.warning(self, "Preflight failed", str(exc))
            return None
        finally:
            progress.close()
            progress.deleteLater()

        if hasattr(self, "roxy_result_label"):
            self.roxy_result_label.setText(
                f"Preflight OK · workspace {result.workspace_id} · profile {result.profile_display_name}"
            )
        return result

    def _show_roxy_upload_progress_dialog(self) -> None:
        dialog = QProgressDialog("Preparing Roxy upload...", None, 0, 0, self)
        dialog.setWindowTitle("Upload via Roxy")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.show()
        self._roxy_upload_progress = dialog
        QApplication.processEvents()

    def _handle_roxy_upload_progress(self, message: str) -> None:
        if self._roxy_upload_progress is not None:
            self._roxy_upload_progress.setLabelText(message)
            QApplication.processEvents()
        if hasattr(self, "roxy_result_label"):
            self.roxy_result_label.setText(message)
        self._refresh_roxy_rate_limit_label()

    def _finish_roxy_upload_ui_state(self) -> None:
        self._set_job_controls_state(False)
        if self._roxy_upload_progress is not None:
            self._roxy_upload_progress.close()
            self._roxy_upload_progress.deleteLater()
            self._roxy_upload_progress = None

        if self._roxy_upload_worker is not None:
            try:
                self._roxy_upload_worker.wait(1000)
            except Exception:
                pass
            self._roxy_upload_worker.deleteLater()
            self._roxy_upload_worker = None
        self._refresh_roxy_rate_limit_label()

    def _handle_roxy_upload_finished(self, success: bool, error_message: str, summary_obj: object) -> None:
        self._finish_roxy_upload_ui_state()
        if not success:
            if hasattr(self, "roxy_result_label"):
                self.roxy_result_label.setText(f"Upload failed: {error_message}")
            self._show_roxy_notification_dialog(
                title="Upload failed",
                message=error_message,
                warning=True,
            )
            return

        summary = summary_obj
        result_lines = [
            f"Profile: {summary.profile_id}",
            f"Video: {summary.video_path}",
            f"Debugger: {summary.debugger_address}",
            summary.message,
        ]
        result_text = "\n".join(result_lines)
        if hasattr(self, "roxy_result_label"):
            self.roxy_result_label.setText(result_text)
        self._show_roxy_notification_dialog(
            title="Upload started",
            message=result_text,
            warning=False,
        )

    def _handle_roxy_upload(self, _checked: bool = False) -> None:
        if self._roxy_upload_worker and self._roxy_upload_worker.isRunning():
            QMessageBox.warning(self, "Upload in progress", "Roxy upload is already running.")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.warning(self, "Sync in progress", "Please wait for current sync job to finish.")
            return
        if self._render_worker and self._render_worker.isRunning():
            QMessageBox.warning(self, "Render in progress", "Please wait for current render job to finish.")
            return

        api_host = self.roxy_api_host_edit.text().strip() if hasattr(self, "roxy_api_host_edit") else ""
        api_key = self.roxy_api_key_edit.text().strip() if hasattr(self, "roxy_api_key_edit") else ""
        workspace_id = self.roxy_workspace_spin.value() if hasattr(self, "roxy_workspace_spin") else 1
        profile_id = self._selected_roxy_profile_id()
        close_after_start = (
            self.roxy_close_profile_check.isChecked()
            if hasattr(self, "roxy_close_profile_check")
            else False
        )

        if not self.roxy_video_path:
            QMessageBox.information(self, "Missing video", "Please select a video file first.")
            return
        if not self.roxy_video_path.exists() or not self.roxy_video_path.is_file():
            QMessageBox.warning(self, "Invalid video path", f"Video file not found:\n{self.roxy_video_path}")
            return
        if not api_host:
            QMessageBox.information(self, "Missing API host", "Please fill in Roxy API host.")
            return
        if not api_key:
            QMessageBox.information(self, "Missing API key", "Please fill in Roxy API key.")
            return
        if not profile_id:
            QMessageBox.information(
                self,
                "Missing profile",
                "Please load profiles and select one, or paste profile dirId manually.",
            )
            return

        preflight = self._run_roxy_preflight(
            api_host=api_host,
            api_key=api_key,
            workspace_id=workspace_id,
            profile_id=profile_id,
            video_path=self.roxy_video_path,
        )
        if preflight is None:
            return

        workspace_id = preflight.workspace_id
        profile_id = preflight.profile_id
        if hasattr(self, "roxy_workspace_spin"):
            self.roxy_workspace_spin.setValue(workspace_id)
        if hasattr(self, "roxy_profile_id_edit"):
            self.roxy_profile_id_edit.setText(profile_id)
        self._remember_roxy_settings()

        self._set_job_controls_state(True)
        self._show_roxy_upload_progress_dialog()
        debug_root = Path.cwd() / "logs" / "roxy_debug"
        self._roxy_upload_worker = RoxyUploadWorker(
            api_host=api_host,
            api_key=api_key,
            workspace_id=workspace_id,
            profile_id=profile_id,
            video_path=preflight.video_path,
            close_profile_after_start=close_after_start,
            debug_root=debug_root,
        )
        self._roxy_upload_worker.progress_message.connect(self._handle_roxy_upload_progress)
        self._roxy_upload_worker.upload_finished.connect(self._handle_roxy_upload_finished)
        if hasattr(self, "roxy_result_label"):
            self.roxy_result_label.setText("Starting upload worker...")
        self._roxy_upload_worker.start()

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

    def _show_srt_success_dialog(self, output_path: Path, entry_count: int, skipped_count: int = 0) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle("SRT created")
        message = f"Created successfully: {entry_count} entries."
        if skipped_count > 0:
            noun = "line" if skipped_count == 1 else "lines"
            message = f"{message}\nSkipped during strict review: {skipped_count} content {noun}."
        dialog.setText(message)
        dialog.setInformativeText(str(output_path))
        open_folder_btn = dialog.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        close_btn = dialog.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
        dialog.setDefaultButton(close_btn)
        dialog.exec()

        if dialog.clickedButton() == open_folder_btn:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path.parent)))
            if not opened:
                QMessageBox.warning(self, "Open folder failed", f"Could not open folder:\n{output_path.parent}")

    @staticmethod
    def _format_srt_strict_review_metrics(review: StrictMatchReview) -> str:
        lines = [f"Lỗi: {review.detail}"]
        if review.candidate_start_segment is not None and review.candidate_end_segment is not None:
            lines.append(
                "Gợi ý segment: "
                f"{review.candidate_start_segment} -> {review.candidate_end_segment}"
            )
        metric_items: list[str] = []
        if review.score is not None:
            metric_items.append(f"score {review.score:.2f}")
        if review.full_ratio is not None:
            metric_items.append(f"full {review.full_ratio:.2f}")
        if review.prefix_ratio is not None:
            metric_items.append(f"prefix {review.prefix_ratio:.2f}")
        if review.coverage is not None:
            metric_items.append(f"coverage {review.coverage:.2f}")
        if review.score_delta is not None:
            metric_items.append(f"delta {review.score_delta:.2f}")
        if metric_items:
            lines.append("Chỉ số: " + " | ".join(metric_items))
        return "\n".join(lines)

    def _show_srt_strict_review_dialog(self, review: StrictMatchReview) -> str:
        title_map = {
            "low-confidence": f"Độ tin cậy ghép thấp ở content #{review.content_index}",
            "no-candidate": f"Không thể ghép chắc chắn content #{review.content_index}",
            "empty-normalized-content": f"Content #{review.content_index} không hợp lệ sau chuẩn hóa",
        }
        dialog = QDialog(self)
        dialog.setObjectName("srtStrictReviewDialog")
        dialog.setWindowTitle("Strict Match Review")
        dialog.setModal(True)
        dialog.resize(760, 480)
        dialog.setMinimumSize(700, 430)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("strictReviewHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(18, 18, 18, 18)
        hero_layout.setSpacing(16)

        icon_label = QLabel()
        icon_label.setObjectName("strictReviewIcon")
        icon_label.setPixmap(
            dialog.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(36, 36)
        )
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(58, 58)
        hero_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(6)

        eyebrow = QLabel("STRICT MODE REVIEW")
        eyebrow.setObjectName("strictReviewEyebrow")
        hero_text.addWidget(eyebrow)

        title = QLabel(title_map.get(review.reason, f"Cần xem lại content #{review.content_index}"))
        title.setObjectName("strictReviewTitle")
        title.setWordWrap(True)
        hero_text.addWidget(title)

        body = QLabel(
            "Skip sẽ bỏ qua dòng content này và tiếp tục tạo SRT cho các dòng sau. "
            "Cancel sẽ dừng toàn bộ quá trình tạo file."
        )
        body.setObjectName("strictReviewBody")
        body.setWordWrap(True)
        hero_text.addWidget(body)
        hero_layout.addLayout(hero_text, 1)
        outer.addWidget(hero)

        content_card = QFrame()
        content_card.setObjectName("strictReviewCard")
        content_layout = QVBoxLayout(content_card)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(8)

        content_title = QLabel("CONTENT ĐANG GẶP VẤN ĐỀ")
        content_title.setObjectName("strictReviewCardTitle")
        content_layout.addWidget(content_title)

        content_preview = QTextEdit()
        content_preview.setObjectName("strictReviewText")
        content_preview.setReadOnly(True)
        content_preview.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        content_preview.setMaximumHeight(118)
        content_preview.setPlainText(review.content_text.strip() or "(content rỗng)")
        content_layout.addWidget(content_preview)
        outer.addWidget(content_card)

        metrics_card = QFrame()
        metrics_card.setObjectName("strictReviewCard")
        metrics_layout = QVBoxLayout(metrics_card)
        metrics_layout.setContentsMargins(16, 16, 16, 16)
        metrics_layout.setSpacing(8)

        metrics_title = QLabel("CHẨN ĐOÁN MATCHER")
        metrics_title.setObjectName("strictReviewCardTitle")
        metrics_layout.addWidget(metrics_title)

        metrics = QLabel(self._format_srt_strict_review_metrics(review))
        metrics.setObjectName("strictReviewMetrics")
        metrics.setWordWrap(True)
        metrics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        metrics_layout.addWidget(metrics)
        outer.addWidget(metrics_card)

        actions = QHBoxLayout()
        actions.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("variant", "danger")
        cancel_btn.clicked.connect(dialog.reject)
        actions.addWidget(cancel_btn)

        skip_btn = QPushButton("Skip")
        skip_btn.setProperty("variant", "primary")
        skip_btn.clicked.connect(dialog.accept)
        actions.addWidget(skip_btn)

        outer.addLayout(actions)

        skip_btn.setDefault(True)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            return "skip"
        return "cancel"

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

        try:
            total_content_items = len(parse_content(content_text))
        except Exception:
            total_content_items = 0

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

        def review_strict_failure(review: StrictMatchReview) -> str:
            progress.hide()
            QApplication.processEvents()
            try:
                return self._show_srt_strict_review_dialog(review)
            finally:
                progress.show()
                QApplication.processEvents()

        # Generate
        try:
            update_progress(4, "Preparing generation inputs...")
            self._generated_srt = generate_merged_srt(
                self.srt_input_path,
                content_text,
                progress_cb=update_progress,
                strict_review_cb=review_strict_failure,
            )
            update_progress(96, "Saving output SRT file...")
            output_path.write_text(self._generated_srt, encoding="utf-8")
            entry_count = self._generated_srt.count("\n\n") + 1
            skipped_count = max(total_content_items - entry_count, 0)
            if hasattr(self, "srt_result_label"):
                summary = f"Generated and saved: {output_path}"
                if skipped_count > 0:
                    noun = "line" if skipped_count == 1 else "lines"
                    summary = f"{summary} · skipped {skipped_count} content {noun}"
                self.srt_result_label.setText(summary)
            update_progress(100, "SRT generated successfully.")
            progress.close()
            self._show_srt_success_dialog(output_path, entry_count, skipped_count)
        except SRTGenerationCancelled as exc:
            self._generated_srt = None
            if hasattr(self, "srt_result_label"):
                self.srt_result_label.setText(str(exc))
            QMessageBox.information(self, "SRT generation cancelled", str(exc))
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

        if self._automate_worker and self._automate_worker.isRunning():
            QMessageBox.warning(
                self,
                "Automation in progress",
                "Please wait for the current automate workflow to complete.",
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
        self._show_render_log_dialog(len(selected), title="Auto Render Progress")
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
                f"⚠️ {failed} project(s) failed. Check render logs for details.",
            )
        if self._render_log_text is not None:
            self._append_render_log(f"Render summary: completed {completed}/{total}.")

    def _handle_automate_clicked(self) -> None:
        selected = self._selected_projects_in_order()
        if not selected:
            QMessageBox.information(
                self,
                "No Video Selected",
                "Please tick at least one video before running Automate.",
            )
            return

        if self._automate_worker and self._automate_worker.isRunning():
            QMessageBox.warning(
                self,
                "Automation in progress",
                "Another automate job is already running.",
            )
            return

        if self._render_worker and self._render_worker.isRunning():
            QMessageBox.warning(
                self,
                "Render in progress",
                "Please wait for the current auto render job to complete.",
            )
            return

        if self._worker and self._worker.isRunning():
            QMessageBox.warning(
                self,
                "Sync in progress",
                "Please wait for the current sync operation to complete.",
            )
            return

        if self._roxy_upload_worker and self._roxy_upload_worker.isRunning():
            QMessageBox.warning(
                self,
                "Upload in progress",
                "Please wait for the current Roxy upload to finish first.",
            )
            return

        api_host = self.roxy_api_host_edit.text().strip() if hasattr(self, "roxy_api_host_edit") else ""
        api_key = self.roxy_api_key_edit.text().strip() if hasattr(self, "roxy_api_key_edit") else ""
        workspace_id = self.roxy_workspace_spin.value() if hasattr(self, "roxy_workspace_spin") else 1
        close_profile_after_start = (
            self.roxy_close_profile_check.isChecked()
            if hasattr(self, "roxy_close_profile_check")
            else False
        )
        if not api_host:
            QMessageBox.information(self, "Missing API host", "Please fill in Roxy API host.")
            return
        if not api_key:
            QMessageBox.information(self, "Missing API key", "Please fill in Roxy API key/token.")
            return

        profile_id = DEFAULT_AUTOMATE_ROXY_PROFILE_ID
        if hasattr(self, "roxy_profile_id_edit"):
            self.roxy_profile_id_edit.setText(profile_id)

        unassigned = sum(1 for project in selected if not str(project.assigned_project_id or "").strip())
        if unassigned:
            warning_reply = QMessageBox.question(
                self,
                "Videos Without Project",
                f"{unassigned} selected video(s) have no project assigned.\n"
                "Raw SEO for those videos will run with empty defaults.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if warning_reply != QMessageBox.StandardButton.Yes:
                return

        jobs = [self._build_automate_job_item(project) for project in selected]
        raw_enabled = sum(1 for item in jobs if item.raw_seo_enabled)
        raw_skipped = len(jobs) - raw_enabled

        reply = QMessageBox.question(
            self,
            "Start Automate",
            f"This will process {len(selected)} video(s) in sequence:\n"
            "1) Auto Render\n"
            "2) Raw SEO\n"
            "3) Upload Roxy\n\n"
            f"Roxy profile: {profile_id}\n"
            f"Raw SEO enabled: {raw_enabled} video(s)\n"
            f"Raw SEO empty defaults: {raw_skipped} video(s)\n\n"
            "Workflow will stop immediately on first failure.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for project in selected:
            project.status = ProjectStatus.pending
            project.notes = ""
        self._refresh_status_cells()

        self._remember_roxy_settings()
        self._last_job_projects = list(selected)

        debug_root = Path.cwd() / "logs" / "roxy_debug"
        self._automate_worker = AutomateWorker(
            jobs,
            api_host=api_host,
            api_key=api_key,
            workspace_id=workspace_id,
            profile_id=profile_id,
            close_profile_after_start=close_profile_after_start,
            strict_raw_seo=False,
            debug_root=debug_root,
        )
        self._automate_worker.status_updated.connect(self._refresh_status_cells)
        self._automate_worker.job_finished.connect(self._on_automate_finished)
        self._automate_worker.log_message.connect(self._append_render_log)
        self._automate_worker.progress_updated.connect(self._update_render_progress)

        self._set_job_controls_state(True)
        self._update_status_label(f"Automating… ({len(selected)} video(s))")
        self._show_render_log_dialog(len(selected), title="Automate Workflow Progress")
        self._append_render_log(
            f"Automate settings: workspace={workspace_id}, profile={profile_id}, selected={len(selected)}"
        )
        self._automate_worker.start()

    def _on_automate_finished(self, completed: int, failed: int, total: int, stopped_early: bool) -> None:
        self._status_message_override = None
        self._refresh_status_cells()
        self._set_job_controls_state(False)

        if self._automate_worker is not None:
            try:
                self._automate_worker.wait(2000)
            except Exception:
                pass
            self._automate_worker = None

        self._update_status_label()
        processed = completed + failed
        remaining = max(total - processed, 0)

        if completed == total and failed == 0 and not stopped_early:
            QMessageBox.information(
                self,
                "Automate Complete",
                f"✅ Successfully automated all {completed} video(s)!",
            )
        else:
            lines = [
                f"Completed: {completed}/{total}",
                f"Failed: {failed}",
            ]
            if remaining:
                lines.append(f"Not processed: {remaining}")
            if stopped_early:
                lines.append("Workflow stopped early after failure/cancel.")
            QMessageBox.warning(self, "Automate Complete", "\n".join(lines))

        if self._render_log_text is not None:
            self._append_render_log(
                f"Automate summary: completed={completed}, failed={failed}, total={total}, "
                f"stopped_early={stopped_early}"
            )

    def _handle_stop_clicked(self) -> None:
        if self._automate_worker and self._automate_worker.isRunning():
            self._automate_worker.cancel()
            self._update_status_label("Stopping automate workflow…")
            return
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

    def _update_render_status_widgets(self) -> None:
        remaining = max(self._render_total_jobs - self._render_done_jobs - self._render_failed_jobs, 0)
        if self._render_total_chip is not None:
            self._render_total_chip.setText(f"Total: {self._render_total_jobs}")
        if self._render_done_chip is not None:
            self._render_done_chip.setText(f"Done: {self._render_done_jobs}")
        if self._render_failed_chip is not None:
            self._render_failed_chip.setText(f"Failed: {self._render_failed_jobs}")
        if self._render_remaining_chip is not None:
            self._render_remaining_chip.setText(f"Remaining: {remaining}")
        if self._render_current_video_label is not None:
            self._render_current_video_label.setText(f"Current Video: {self._render_current_video}")
        if self._render_current_stage_label is not None:
            self._render_current_stage_label.setText(f"Current Stage: {self._render_current_stage}")

    def _reset_render_log_state(self, *, total: int, title: str) -> None:
        self._render_total_jobs = max(total, 0)
        self._render_done_jobs = 0
        self._render_failed_jobs = 0
        self._render_current_video = "-"
        self._render_current_stage = "Preparing..."
        if "automate" in title.casefold():
            self._render_current_stage = "Starting automate workflow..."
        elif "render" in title.casefold():
            self._render_current_stage = "Starting auto render..."
        self._update_render_status_widgets()

    @staticmethod
    def _render_log_level(message: str) -> tuple[str, str, str]:
        text = message.strip()
        if "[ERROR]" in text:
            return "ERROR", "#7F1D1D", "#FEE2E2"
        if "[SUCCESS]" in text:
            return "SUCCESS", "#065F46", "#D1FAE5"
        if "[STOP]" in text:
            return "STOP", "#7C2D12", "#FFEDD5"
        if "[STEP" in text:
            return "STEP", "#1E3A8A", "#DBEAFE"
        if "[CLEANUP]" in text:
            return "CLEANUP", "#334155", "#E2E8F0"
        if "[Roxy]" in text:
            return "ROXY", "#4C1D95", "#EDE9FE"
        return "INFO", "#334155", "#E2E8F0"

    def _track_render_log_state(self, message: str) -> None:
        text = message.strip()
        if not text:
            return

        project_match = re.search(r"------\s+(?:PROJECT|VIDEO)\s+\d+/\d+:\s+(.+?)\s+------", text)
        if project_match:
            self._render_current_video = project_match.group(1).strip()

        step_match = re.search(r"\[(STEP \d+/\d+|CLEANUP|STOP)\]\s*(.*)", text)
        if step_match:
            kind = step_match.group(1).strip()
            detail = step_match.group(2).strip()
            self._render_current_stage = f"{kind} {detail}".strip()
        elif text.startswith("[SUCCESS]"):
            self._render_current_stage = text
            self._render_done_jobs += 1
        elif text.startswith("[ERROR]"):
            self._render_current_stage = text
            self._render_failed_jobs += 1

        self._update_render_status_widgets()

    def _ensure_render_log_dialog(self) -> None:
        if self._render_log_dialog is not None:
            return
        dialog = QDialog(self)
        dialog.setObjectName("renderLogDialog")
        dialog.setWindowTitle("Auto Render Progress")
        dialog.setModal(False)
        dialog.resize(880, 560)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        status_card = QFrame()
        status_card.setObjectName("renderStatusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(8)

        self._render_current_video_label = QLabel("Current Video: -")
        self._render_current_video_label.setObjectName("renderCurrentVideoLabel")
        status_layout.addWidget(self._render_current_video_label)

        self._render_current_stage_label = QLabel("Current Stage: Waiting...")
        self._render_current_stage_label.setObjectName("renderCurrentStageLabel")
        self._render_current_stage_label.setWordWrap(True)
        status_layout.addWidget(self._render_current_stage_label)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self._render_total_chip = QLabel("Total: 0")
        self._render_total_chip.setObjectName("renderStatChip")
        chips_row.addWidget(self._render_total_chip)

        self._render_done_chip = QLabel("Done: 0")
        self._render_done_chip.setObjectName("renderStatChip")
        chips_row.addWidget(self._render_done_chip)

        self._render_failed_chip = QLabel("Failed: 0")
        self._render_failed_chip.setObjectName("renderStatChipDanger")
        chips_row.addWidget(self._render_failed_chip)

        self._render_remaining_chip = QLabel("Remaining: 0")
        self._render_remaining_chip.setObjectName("renderStatChip")
        chips_row.addWidget(self._render_remaining_chip)
        chips_row.addStretch(1)
        status_layout.addLayout(chips_row)
        layout.addWidget(status_card)

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

    def _show_render_log_dialog(self, total: int = 0, *, title: str = "Auto Render Progress") -> None:
        self._ensure_render_log_dialog()
        if self._render_log_dialog is None:
            return
        self._render_log_dialog.setWindowTitle(title)
        self._reset_render_log_state(total=total, title=title)
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

        self._track_render_log_state(message)
        level, fg, bg = self._render_log_level(message)
        stamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        safe_message = escape(message.strip() or "(empty)")
        html_line = (
            "<div style='margin: 2px 0;'>"
            f"<span style='color:#64748B;'>[{stamp}]</span> "
            f"<span style='display:inline-block; background:{bg}; color:{fg}; "
            "border-radius:6px; padding:1px 7px; font-size:11px; font-weight:700;'>"
            f"{escape(level)}</span> "
            f"<span style='color:#0F172A;'>{safe_message}</span>"
            "</div>"
        )
        self._render_log_text.moveCursor(QTextCursor.MoveOperation.End)
        self._render_log_text.insertHtml(html_line)
        self._render_log_text.insertPlainText("\n")
        self._render_log_text.moveCursor(QTextCursor.MoveOperation.End)

    def _update_render_progress(self, completed: int, total: int) -> None:
        if self._render_progress_bar is None or self._render_progress_label is None:
            self._ensure_render_log_dialog()
        if self._render_progress_bar is None or self._render_progress_label is None:
            return
        normalized_total = max(total, 1)
        self._render_progress_bar.setRange(0, normalized_total)
        self._render_progress_bar.setValue(min(completed, total))
        self._render_progress_label.setText(f"Progress: {completed}/{total}")
        self._render_total_jobs = max(total, 0)
        self._update_render_status_widgets()

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
            parts.append("Hover video name to view failure details.")
        else:
            parts.append("All projects synced successfully.")
        return "\n".join(parts)

    # endregion
