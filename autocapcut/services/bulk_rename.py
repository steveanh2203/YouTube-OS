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


class BulkRenameError(Exception):
    """Raised when bulk renaming fails."""


def bulk_rename(folder: Path, prefix: str) -> RenameSummary:
    """Rename files within *folder* to ``{prefix}_{index:0Nd}.ext``."""

    if not folder.exists():
        raise BulkRenameError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise BulkRenameError(f"Path is not a directory: {folder}")

    files = [p for p in folder.iterdir() if p.is_file()]
    if not files:
        raise BulkRenameError(f"No files found in {folder}")

    files.sort(key=_natural_key)
    width = max(2, len(str(len(files))))

    renamed = 0
    skipped = 0

    for index, path in enumerate(files, start=1):
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
        "Bulk rename completed for %s | renamed=%s skipped=%s",
        folder,
        renamed,
        skipped,
    )
    return RenameSummary(folder=folder, prefix=prefix, renamed=renamed, skipped=skipped)


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
