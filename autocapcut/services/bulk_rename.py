"""Bulk renaming helpers for CapCut assets."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
from uuid import uuid4

from loguru import logger


@dataclass
class RenameSummary:
    folder: Path
    prefix: str
    renamed: int
    skipped: int
    start: int
    end: int
    width: int
    total_files: int


class BulkRenameError(Exception):
    """Raised when bulk renaming fails."""


def bulk_rename(folder: Path, prefix: str, start: int = 1, end: int | None = None) -> RenameSummary:
    """Rename files within *folder* to ``{prefix}_{index:0Nd}.ext``.

    Parameters
    ----------
    folder: Path
        Target folder containing files to rename.
    prefix: str
        Prefix to use, e.g. ``image`` → ``image_001``.
    start: int
        Number to start counting from (default: 1).
    end: int | None
        Optional number to stop at. If omitted, the sequence runs until all files are renamed.
    """

    if not folder.exists():
        raise BulkRenameError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise BulkRenameError(f"Path is not a directory: {folder}")
    if start < 1:
        raise BulkRenameError("Start number must be at least 1")
    if end is not None and end < start:
        raise BulkRenameError("End number must be greater than or equal to start number")

    files = [p for p in folder.iterdir() if p.is_file()]
    if not files:
        raise BulkRenameError(f"No files found in {folder}")

    files.sort(key=_natural_key)
    total_files = len(files)

    if end is not None:
        available = end - start + 1
        if available < total_files:
            raise BulkRenameError(
                f"Not enough numbers in range {start}–{end} for {total_files} file(s)"
            )

    last_index = start + total_files - 1
    max_index = end if end is not None else last_index
    width = max(3, len(str(max_index)))

    renamed = 0
    skipped = 0

    for offset, path in enumerate(files):
        index = start + offset
        target_name = f"{prefix}_{index:0{width}d}{path.suffix.lower()}"
        target_path = path.with_name(target_name)
        if path.name == target_name:
            skipped += 1
            continue

        if target_path.exists():
            temp_path = path.with_name(f".__tmp_{uuid4().hex}{path.suffix.lower()}")
            path.rename(temp_path)
            temp_path.rename(target_path)
        else:
            path.rename(target_path)
        renamed += 1
        logger.debug("Renamed %s -> %s", path.name, target_name)

    logger.info(
        "Bulk rename completed for %s | renamed=%s skipped=%s start=%s end_used=%s width=%s requested_end=%s",
        folder,
        renamed,
        skipped,
        start,
        last_index,
        width,
        end,
    )
    return RenameSummary(
        folder=folder,
        prefix=prefix,
        renamed=renamed,
        skipped=skipped,
        start=start,
        end=last_index,
        width=width,
        total_files=total_files,
    )


def _natural_key(path: Path) -> List[object]:
    name = path.name
    parts = re.findall(r'\d+|\D+', name)
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return key
