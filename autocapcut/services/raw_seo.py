"""Helpers for applying "Raw SEO" metadata via ExifTool."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from loguru import logger


class RawSEOError(RuntimeError):
    """Raised when raw SEO metadata actions fail."""


@dataclass
class RawSEOResult:
    file: Path
    success: bool
    message: str


@dataclass
class RawSEOSummary:
    processed: int
    succeeded: int
    failed: int
    title: str
    description: str
    keywords: list[str]
    results: list[RawSEOResult]


ProgressCallback = Callable[[int, int, Path | None], None]
_QUICKTIME_FAMILY_EXTS = {".mp4", ".mov", ".m4v", ".m4a", ".3gp", ".3g2", ".qt"}


def parse_keywords(raw: str) -> list[str]:
    """Parse raw keyword text (comma/newline/semicolon separated) into unique values."""
    parts = re.split(r"[,\n;]+", raw)
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        keyword = part.strip()
        if not keyword:
            continue
        lowered = keyword.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(keyword)
    return result


def _is_quicktime_family(path: Path) -> bool:
    return path.suffix.lower() in _QUICKTIME_FAMILY_EXTS


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:180].strip() if cleaned else "untitled"


def _sanitize_tag_name(tag: str) -> str:
    cleaned = tag.strip().lstrip("-")
    if not cleaned:
        raise RawSEOError("Metadata tag name cannot be empty.")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9:_-]*$", cleaned):
        raise RawSEOError(f"Invalid metadata tag name: {tag}")
    return cleaned


def _rename_to_title(path: Path, title: str) -> Path:
    stem = _sanitize_filename(title)
    candidate = path.with_name(f"{stem}{path.suffix}")
    if candidate == path:
        return path
    if not candidate.exists():
        path.rename(candidate)
        return candidate

    for idx in range(1, 1000):
        variant = path.with_name(f"{stem} ({idx}){path.suffix}")
        if not variant.exists():
            path.rename(variant)
            return variant
    raise RawSEOError(f"Could not rename file due to repeated name collisions: {path.name}")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _coerce_to_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _first_metadata_value(metadata: dict[str, object], keys: list[str]) -> str:
    for key in keys:
        values = _coerce_to_list(metadata.get(key))
        if values:
            return values[0]
    return ""


def _collect_metadata_keywords(metadata: dict[str, object]) -> list[str]:
    gathered: list[str] = []
    for key in ("Keywords", "Subject", "XMP-dc:Subject"):
        gathered.extend(_coerce_to_list(metadata.get(key)))
    # preserve order while deduplicating (case-insensitive)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in gathered:
        normalized = item.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


def _candidate_keys_for_tag(tag: str) -> list[str]:
    key = _sanitize_tag_name(tag)
    candidates = [key]
    if ":" in key:
        candidates.append(key.split(":", 1)[1])
    return candidates


def _verify_raw_seo_fields(
    metadata: dict[str, object],
    *,
    expected_title: str,
    expected_description: str,
    expected_keywords: list[str],
    expected_extra_tags: dict[str, str] | None = None,
) -> list[str]:
    issues: list[str] = []

    if expected_title:
        actual_title = _first_metadata_value(
            metadata,
            ["Title", "Title-en-US", "QuickTime:Title", "ItemList:Title", "Keys:Title", "XMP-dc:Title"],
        )
        if not actual_title:
            issues.append("title not found")
        elif _normalize_text(actual_title) != _normalize_text(expected_title):
            issues.append(f"title mismatch (got: {actual_title})")

    if expected_description:
        actual_description = _first_metadata_value(
            metadata,
            [
                "Description",
                "Description-en-US",
                "QuickTime:Description",
                "ItemList:Description",
                "Keys:Description",
                "XMP-dc:Description",
            ],
        )
        if not actual_description:
            issues.append("description not found")
        elif _normalize_text(actual_description) != _normalize_text(expected_description):
            issues.append(f"description mismatch (got: {actual_description})")

    if expected_keywords:
        actual_keywords = _collect_metadata_keywords(metadata)
        expected_norm = {item.casefold() for item in expected_keywords}
        actual_norm = {item.casefold() for item in actual_keywords}
        missing = sorted(expected_norm - actual_norm)
        if missing:
            issues.append("keywords missing: " + ", ".join(missing))

    if expected_extra_tags:
        for tag, expected_value in expected_extra_tags.items():
            actual_value = _first_metadata_value(metadata, _candidate_keys_for_tag(tag))
            if not actual_value:
                issues.append(f"{tag} not found")
                continue
            if _normalize_text(actual_value) != _normalize_text(expected_value):
                issues.append(f"{tag} mismatch (got: {actual_value})")

    return issues


def apply_raw_seo(
    files: list[Path],
    *,
    title: str,
    description: str,
    keywords: list[str],
    extra_tags: dict[str, str] | None = None,
    rename_to_title: bool = False,
    strict_verify: bool = False,
    exiftool: str = "exiftool",
    progress_callback: ProgressCallback | None = None,
) -> RawSEOSummary:
    """Apply basic SEO metadata to each file using ExifTool."""
    targets = [path for path in files if isinstance(path, Path)]
    if not targets:
        raise RawSEOError("No files selected.")
    if not any((title.strip(), description.strip(), keywords)):
        raise RawSEOError("Please provide at least one field: title, description, or keywords.")

    executable = shutil.which(exiftool)
    if executable is None:
        raise RawSEOError("ExifTool was not found on PATH. Install ExifTool and try again.")

    cleaned_title = title.strip()
    cleaned_description = description.strip()
    cleaned_keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    cleaned_extra_tags: dict[str, str] = {}
    for raw_tag, raw_value in (extra_tags or {}).items():
        tag = _sanitize_tag_name(raw_tag)
        value = str(raw_value).strip()
        if not value:
            continue
        cleaned_extra_tags[tag] = value
    total = len(targets)
    results: list[RawSEOResult] = []

    def _process_file(index_path: tuple[int, Path]) -> RawSEOResult:
        index, path = index_path
        if not path.exists():
            return RawSEOResult(file=path, success=False, message="File does not exist")
        if not path.is_file():
            return RawSEOResult(file=path, success=False, message="Path is not a file")

        cmd = [executable, "-overwrite_original", "-m"]
        if cleaned_title:
            cmd.extend(
                [
                    f"-Title={cleaned_title}",
                    f"-XMP-dc:Title={cleaned_title}",
                ]
            )
            if _is_quicktime_family(path):
                cmd.extend(
                    [
                        f"-QuickTime:Title={cleaned_title}",
                        f"-ItemList:Title={cleaned_title}",
                        f"-Keys:Title={cleaned_title}",
                    ]
                )
        if cleaned_description:
            cmd.extend(
                [
                    f"-Description={cleaned_description}",
                    f"-XMP-dc:Description={cleaned_description}",
                ]
            )
            if _is_quicktime_family(path):
                cmd.extend(
                    [
                        f"-QuickTime:Description={cleaned_description}",
                        f"-ItemList:Description={cleaned_description}",
                        f"-Keys:Description={cleaned_description}",
                    ]
                )
        for keyword in cleaned_keywords:
            cmd.extend(
                [
                    f"-Keywords={keyword}",
                    f"-XMP-dc:Subject={keyword}",
                ]
            )
        for tag, value in cleaned_extra_tags.items():
            cmd.append(f"-{tag}={value}")
        cmd.append(str(path))

        try:
            completed = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ExifTool timeout for {}", path)
            return RawSEOResult(file=path, success=False, message="ExifTool timed out")
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or str(exc)).strip()
            if not details:
                details = "ExifTool failed"
            logger.warning("ExifTool failed for {}: {}", path, details)
            return RawSEOResult(file=path, success=False, message=details)

        final_path = path
        message = (completed.stdout or "Metadata updated").strip()
        if rename_to_title and cleaned_title:
            try:
                final_path = _rename_to_title(path, cleaned_title)
                if final_path != path:
                    message = f"{message} | renamed to {final_path.name}"
            except OSError as exc:
                return RawSEOResult(file=path, success=False, message=f"Rename failed: {exc}")
            except RawSEOError as exc:
                return RawSEOResult(file=path, success=False, message=str(exc))

        if strict_verify:
            try:
                metadata = read_raw_seo_metadata(
                    final_path,
                    exiftool=executable,
                    additional_tags=list(cleaned_extra_tags.keys()),
                )
            except RawSEOError as exc:
                return RawSEOResult(file=final_path, success=False, message=f"Strict verify failed: {exc}")

            issues = _verify_raw_seo_fields(
                metadata,
                expected_title=cleaned_title,
                expected_description=cleaned_description,
                expected_keywords=cleaned_keywords,
                expected_extra_tags=cleaned_extra_tags,
            )
            if issues:
                return RawSEOResult(
                    file=final_path,
                    success=False,
                    message="Strict verify failed: " + " | ".join(issues),
                )
            message = f"{message} | strict verify: PASS"

        return RawSEOResult(file=final_path, success=True, message=message)

    # Determine worker count: ExifTool is subprocess-bound (not GIL-bound),
    # so parallel execution yields significant speedup.
    max_workers = min(8, max(1, (os.cpu_count() or 4)))

    if progress_callback is not None:
        progress_callback(0, total, None)

    # Submit all files in parallel; collect results preserving submission order
    indexed = list(enumerate(targets, 1))
    futures_map: dict = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for item in indexed:
            fut = executor.submit(_process_file, item)
            futures_map[fut] = item

        completed_count = 0
        for future in as_completed(futures_map):
            result = future.result()
            results.append(result)
            completed_count += 1
            if progress_callback is not None:
                progress_callback(completed_count, total, result.file)

    succeeded = sum(1 for item in results if item.success)
    failed = len(results) - succeeded
    logger.info(
        "Raw SEO complete | processed={} succeeded={} failed={} title={} keywords={} extra={} strict={}",
        len(results),
        succeeded,
        failed,
        bool(cleaned_title),
        len(cleaned_keywords),
        len(cleaned_extra_tags),
        strict_verify,
    )
    return RawSEOSummary(
        processed=len(results),
        succeeded=succeeded,
        failed=failed,
        title=cleaned_title,
        description=cleaned_description,
        keywords=cleaned_keywords,
        results=results,
    )


def write_raw_seo_payload(
    output_path: Path,
    *,
    files: list[Path],
    title: str,
    description: str,
    keywords: list[str],
    extra_tags: dict[str, str] | None = None,
) -> Path:
    """Write a JSON payload that can be reused for YouTube upload metadata."""
    payload = {
        "title": title.strip(),
        "description": description.strip(),
        "tags": [keyword.strip() for keyword in keywords if keyword.strip()],
        "extra_tags": extra_tags or {},
        "files": [str(path) for path in files],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Raw SEO payload saved to {}", output_path)
    return output_path


def read_raw_seo_metadata(
    file: Path,
    *,
    exiftool: str = "exiftool",
    additional_tags: list[str] | None = None,
) -> dict[str, object]:
    """Read key SEO metadata fields from a media file using ExifTool."""
    if not file.exists():
        raise RawSEOError(f"File does not exist: {file}")
    if not file.is_file():
        raise RawSEOError(f"Path is not a file: {file}")

    executable = shutil.which(exiftool)
    if executable is None:
        raise RawSEOError("ExifTool was not found on PATH. Install ExifTool and try again.")

    cmd = [
        executable,
        "-j",
        "-a",
        "-s",
        "-Title",
        "-Description",
        "-Keywords",
        "-Subject",
        "-XMP-dc:Title",
        "-XMP-dc:Description",
        "-XMP-dc:Subject",
        "-XMP-dc:Creator",
        "-Artist",
        "-XMP-dc:Publisher",
        "-XMP-dc:Rights",
        "-Copyright",
        "-Comment",
        "-XMP-xmp:Rating",
        "-XMP-dc:Language",
        "-URL",
        "-XMP-xmpRights:WebStatement",
        "-XMP-photoshop:DateCreated",
        "-XMP-photoshop:City",
        "-XMP-photoshop:Country",
        "-QuickTime:Title",
        "-QuickTime:Description",
        "-ItemList:Title",
        "-ItemList:Description",
        "-Keys:Title",
        "-Keys:Description",
    ]
    if additional_tags:
        for raw_tag in additional_tags:
            tag = _sanitize_tag_name(raw_tag)
            cmd.append(f"-{tag}")
    cmd.append(str(file))
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RawSEOError("ExifTool timed out while reading metadata.") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RawSEOError(f"Could not read metadata: {details}") from exc

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RawSEOError("Failed to parse metadata output from ExifTool.") from exc

    if not payload or not isinstance(payload, list):
        raise RawSEOError("ExifTool returned no metadata.")

    data = payload[0]
    if not isinstance(data, dict):
        raise RawSEOError("Unexpected metadata format returned by ExifTool.")
    return data
