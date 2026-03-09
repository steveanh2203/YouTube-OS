"""Sync audio-only GUI for AutoCapcut prototype."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re
from typing import List
from uuid import uuid4

from loguru import logger
from PySide6.QtCore import QDateTime, QSettings, QThread, Qt, QTimer, QUrl, Signal
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

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoCapCut")
        self.resize(1280, 740)

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
        self._font_family: str = "Space Grotesk"
        self._updating_srt_paste_text: bool = False
        self._settings = QSettings("AutoCapCut", "AutoCapCut")
        self._last_srt_output_dir: Path = self._load_last_srt_output_dir()
        self.project_presets: list[dict[str, object]] = []
        self.project_assignment_by_path: dict[str, str] = {}
        self.video_child_settings_by_path: dict[str, dict[str, object]] = {}
        self.project_hierarchy_scroll: QScrollArea | None = None
        self.project_hierarchy_root: QWidget | None = None
        self.project_hierarchy_layout: QVBoxLayout | None = None
        self._load_project_dashboard_settings()

        self._build_ui()
        self.refresh_projects()

    # region Qt overrides
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
            ("project", "Project"),
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
            ("raw_seo", "Raw SEO"),
            ("srt", "SRT Generator"),
            ("roxy_upload", "Upload Roxy"),
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
        panel_layout.setContentsMargins(24, 24, 24, 20)
        panel_layout.setSpacing(16)

        title = QLabel("Videos")
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
        action_grid = QGridLayout(action_panel)
        action_grid.setContentsMargins(14, 14, 14, 14)
        action_grid.setHorizontalSpacing(12)
        action_grid.setVerticalSpacing(12)

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
            button.setMinimumHeight(44)
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

            #actionPanel {{
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
            }}

            QPushButton[actionRole="quick"] {{
                min-height: 44px;
                padding: 0px 16px;
                font-size: 14px;
                font-weight: 700;
                text-align: center;
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

            #projectHierarchyScroll {{
                border: 1px solid #CBD5E1;
                border-radius: 12px;
                background: #F8FAFC;
            }}

            #projectHierarchyRoot {{
                background: transparent;
            }}

            QFrame#projectParentCard {{
                border: 1px solid #E2E8F0;
                border-radius: 12px;
                background: #FFFFFF;
            }}

            QLabel#projectParentTitle {{
                font-size: 15px;
                font-weight: 700;
                color: #0F172A;
                background: transparent;
                border: none;
            }}

            QLabel#projectParentCount {{
                font-size: 11px;
                font-weight: 700;
                color: #1D4ED8;
                background: #DBEAFE;
                border: 1px solid #BFDBFE;
                border-radius: 999px;
                padding: 2px 8px;
            }}

            QLabel#projectParentMeta {{
                font-size: 12px;
                color: #64748B;
                background: transparent;
                border: none;
            }}

            QTableWidget#projectChildrenTable {{
                font-size: 12px;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                background: #FFFFFF;
                alternate-background-color: #F8FAFC;
            }}

            QTableWidget#projectChildrenTable::item {{
                padding: 4px 6px;
                color: #334155;
            }}

            QTableWidget#projectChildrenTable QHeaderView::section {{
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.04em;
                background: #F1F5F9;
                color: #475569;
                border: none;
                border-right: 1px solid #E2E8F0;
                border-bottom: 1px solid #E2E8F0;
                padding: 6px 6px;
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

            QTableWidget QComboBox#rowProjectPresetCombo {{
                min-height: 24px;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 2px 22px 2px 8px;
                background: #EFF6FF;
                color: #1E3A8A;
                font-size: 12px;
                font-weight: 600;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo[hasProject="false"] {{
                background: #F8FAFC;
                color: #64748B;
                font-weight: 500;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo:hover {{
                border-color: #BFDBFE;
                background: #E0F2FE;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo:focus {{
                border: 1px solid #2563EB;
                background: #DBEAFE;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 18px;
                border: none;
                background: transparent;
            }}

            QTableWidget QComboBox#rowProjectPresetCombo QAbstractItemView {{
                border: 1px solid #CBD5E1;
                background: #FFFFFF;
                selection-background-color: #DBEAFE;
                selection-color: #1E3A8A;
                padding: 4px;
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

    def _open_project_children_dialog(self, preset_id: str) -> None:
        preset = self._find_project_preset_by_id(preset_id)
        if preset is None:
            QMessageBox.information(self, "No project", "Project not found.")
            return

        children = [
            project for project in self.projects
            if str(project.assigned_project_id or "").strip() == preset_id
        ]
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Child Projects - {str(preset.get('name', '')).strip()}")
        dialog.resize(1120, 760)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel(f"{str(preset.get('name', '')).strip()} · {len(children)} child project(s)")
        heading.setObjectName("projectParentTitle")
        layout.addWidget(heading)

        table = QTableWidget()
        table.setObjectName("projectChildrenTable")
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Video", "Source", "Status", "Edit"])
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.ResizeToContents)

        table.setRowCount(len(children))
        for row, project in enumerate(children):
            name_item = QTableWidgetItem(project.name)
            child_settings = self._video_child_settings_for_path(project.path)
            name_item.setToolTip(
                "Description: "
                + (str(child_settings.get("description", "")).strip() or "(empty)")
                + "\nKeywords: "
                + (str(child_settings.get("keywords_raw", "")).strip() or "(empty)")
            )
            source_item = QTableWidgetItem(project.source.label())
            status_item = QTableWidgetItem(project.status.label())
            status_item.setForeground(QBrush(self._status_color(project.status)))
            for item in (name_item, source_item, status_item):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, source_item)
            table.setItem(row, 2, status_item)

            edit_btn = QPushButton("Edit")
            edit_btn.setProperty("variant", "secondary")
            edit_btn.clicked.connect(
                lambda _checked=False, p=project: self._open_video_child_editor(p)
            )
            table.setCellWidget(row, 3, edit_btn)

        layout.addWidget(table, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setProperty("variant", "primary")
        close_btn.clicked.connect(dialog.accept)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        dialog.setWindowState(dialog.windowState() | Qt.WindowState.WindowMaximized)
        dialog.exec()

    def _refresh_project_preset_tab(self, selected_preset_id: str = "") -> None:
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

        children_by_parent: dict[str, list[ProjectItem]] = {
            str(preset.get("id", "")): [] for preset in self.project_presets
        }
        for project in self.projects:
            parent_id = str(project.assigned_project_id or "").strip()
            if parent_id in children_by_parent:
                children_by_parent[parent_id].append(project)

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

            count = QLabel(f"{len(children)} video(s)")
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
