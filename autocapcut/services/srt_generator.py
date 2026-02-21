"""Generate a merged SRT file by aligning content items to CapCut SRT segments."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import List

from loguru import logger

# ---------------------------------------------------------------------------
# Number word → digit mapping (for normalisation)
# ---------------------------------------------------------------------------
_WORD_TO_NUM: dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SRTSegment:
    index: int
    start: str   # e.g. "00:00:00,066"
    end: str     # e.g. "00:00:03,066"
    text: str


@dataclass
class ContentItem:
    index: int
    text: str


@dataclass
class OutputEntry:
    index: int
    start: str
    end: str
    text: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SRTGeneratorError(Exception):
    """Raised when SRT generation fails."""


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Normalise text for fuzzy comparison."""
    text = text.lower()

    # Remove accents / diacritics (e.g. "sauté" → "saute")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")

    # "No. 8" / "No.8" / "No 8" → "number 8"
    text = re.sub(r"\bno\.?\s*(\d+)\b", r"number \1", text)

    # Number words → digits
    for word, digit in _WORD_TO_NUM.items():
        text = re.sub(r"\b" + word + r"\b", digit, text)

    # Remove punctuation / special chars
    text = re.sub(r"[^\w\s]", " ", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_srt(content: str) -> List[SRTSegment]:
    """Parse SRT file text into a list of SRTSegment."""
    segments: List[SRTSegment] = []
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = [ln.rstrip() for ln in block.strip().splitlines()]
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        time_line = lines[1].strip()
        if "-->" not in time_line:
            continue

        start, end = [t.strip() for t in time_line.split("-->", 1)]
        text = " ".join(ln.strip() for ln in lines[2:] if ln.strip())
        segments.append(SRTSegment(index=index, start=start, end=end, text=text))

    return segments


def _strip_rtf(rtf: str) -> str:
    """Strip RTF markup and return plain text (handles macOS RTF from Pages/TextEdit)."""
    # Extract text lines that follow \strokec3 (macOS RTF content marker)
    lines = re.findall(r"\\strokec3\s+(.+?)\\?\s*$", rtf, re.MULTILINE)

    if lines:
        # Decode RTF character escapes (e.g. \'97 → em-dash, \'e9 → é)
        def _decode(m: re.Match) -> str:
            try:
                return bytes([int(m.group(1), 16)]).decode("cp1252", errors="replace")
            except (ValueError, OverflowError):
                return ""

        result = []
        for ln in lines:
            ln = re.sub(r"\\'([0-9a-fA-F]{2})", _decode, ln)
            ln = ln.strip()
            if ln:
                result.append(ln)
        return "\n".join(result)

    # Fallback: generic RTF strip
    text = re.sub(r"\{\\[^{}]*\}", "", rtf)
    text = re.sub(r"\\[a-z]+\d*\s?", " ", text)
    text = re.sub(r"\\[^a-z\s]", "", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_content(raw: str) -> List[ContentItem]:
    """
    Parse content text into numbered ContentItem list.

    Accepts:
      • "1. text"  /  "1) text"  /  "1  text"  (numbered)
      • Plain lines — auto-numbered from 1
      • RTF files — stripped first
    """
    # Detect and strip RTF
    if raw.lstrip().startswith("{\\rtf"):
        raw = _strip_rtf(raw)

    items: List[ContentItem] = []
    auto_index = 1

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        # Match leading number: "1.", "1)", "1:", "1 "
        m = re.match(r"^(\d+)[.):\s]\s*(.+)$", line)
        if m:
            index = int(m.group(1))
            text = m.group(2).strip()
        else:
            index = auto_index
            text = line

        items.append(ContentItem(index=index, text=text))
        auto_index = index + 1

    return items


# ---------------------------------------------------------------------------
# Matching algorithm
# ---------------------------------------------------------------------------

def match_content_to_srt(
    content_items: List[ContentItem],
    srt_segments: List[SRTSegment],
) -> List[OutputEntry]:
    """
    Greedily match each content item to one or more consecutive SRT segments.

    Strategy
    --------
    For each content item (in order), consume SRT segments one-by-one,
    accumulating text, until:
      • similarity ≥ 0.85  (good enough match), OR
      • accumulated words ≥ content words × 2  (overshoot guard).
    The SRT pointer for the next content item starts right after the
    best-matched segment for the current one.
    """
    results: List[OutputEntry] = []
    srt_ptr = 0
    total = len(srt_segments)

    for content in content_items:
        if srt_ptr >= total:
            logger.warning("Ran out of SRT segments at content item #%d", content.index)
            break

        content_norm = _normalize(content.text)
        n_content_words = max(len(content_norm.split()), 1)

        start_time = srt_segments[srt_ptr].start
        accumulated_words: List[str] = []
        best_ratio = 0.0
        best_end_ptr = srt_ptr
        look = srt_ptr

        while look < total:
            seg_words = _normalize(srt_segments[look].text).split()
            accumulated_words.extend(seg_words)
            acc_norm = " ".join(accumulated_words)

            ratio = _similarity(acc_norm, content_norm)
            if ratio > best_ratio:
                best_ratio = ratio
                best_end_ptr = look

            look += 1

            # Stop: match is good enough
            if ratio >= 0.85:
                break

            # Stop: accumulated text is already much longer than content
            if len(accumulated_words) >= n_content_words * 2:
                break

        srt_ptr = best_end_ptr + 1
        end_time = srt_segments[best_end_ptr].end

        logger.debug(
            "Content #%d → SRT[%d..%d] ratio=%.2f",
            content.index, srt_ptr - (best_end_ptr - srt_ptr + 2),
            best_end_ptr, best_ratio,
        )

        results.append(OutputEntry(
            index=content.index,
            start=start_time,
            end=end_time,
            text=content.text,
        ))

    return results


# ---------------------------------------------------------------------------
# Output formatter
# ---------------------------------------------------------------------------

def _to_srt_string(entries: List[OutputEntry]) -> str:
    """Render OutputEntry list to SRT file string."""
    blocks: List[str] = []
    for i, entry in enumerate(entries, 1):
        blocks.append(f"{i}\n{entry.start} --> {entry.end}\n{entry.text}\n")
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Encoding helper
# ---------------------------------------------------------------------------

def _read_file_text(path: Path) -> str:
    """
    Read a text file with smart encoding detection.
    Priority: UTF-8 BOM → UTF-8 → cp1252 (Windows) → latin-1 (fallback).
    """
    raw_bytes = path.read_bytes()

    # Strip UTF-8 BOM if present (common on Windows Notepad saves)
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]

    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue

    # Last resort
    return raw_bytes.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_merged_srt(srt_path: Path, content_text: str) -> str:
    """
    Read *srt_path* (CapCut SRT) and *content_text* (raw content string),
    return the merged SRT as a string.

    Raises SRTGeneratorError on any failure.
    """
    # --- Parse SRT ---
    try:
        srt_raw = _read_file_text(srt_path)
    except OSError as exc:
        raise SRTGeneratorError(f"Không đọc được file SRT: {exc}") from exc

    srt_segments = parse_srt(srt_raw)
    if not srt_segments:
        raise SRTGeneratorError("File SRT không có segment nào hợp lệ.")

    # --- Parse content ---
    content_items = parse_content(content_text)
    if not content_items:
        raise SRTGeneratorError("Không tìm thấy câu content nào.")

    logger.info(
        "SRT segments: %d | Content items: %d",
        len(srt_segments), len(content_items),
    )

    # --- Match ---
    entries = match_content_to_srt(content_items, srt_segments)

    if not entries:
        raise SRTGeneratorError("Không thể ghép content với SRT segments.")

    logger.info("Generated %d output entries.", len(entries))
    return _to_srt_string(entries)
