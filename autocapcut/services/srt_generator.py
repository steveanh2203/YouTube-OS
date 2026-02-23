"""Generate a merged SRT file by aligning content items to CapCut SRT segments."""
from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, List

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
# Strict matching config (CapCut timestamp is source of truth)
# ---------------------------------------------------------------------------
_STRICT_START_SCAN_SEGMENTS = 22
_STRICT_MAX_LOOKAHEAD_SEGMENTS = 48
_STRICT_PREFIX_MAX_WORDS = 8
_STRICT_MIN_FULL_RATIO = 0.52
_STRICT_MIN_PREFIX_RATIO = 0.46
_STRICT_MIN_SCORE = 0.54
_STRICT_MIN_COVERAGE = 0.46
_STRICT_MARGIN_FALLBACK_SCORE = 0.48
_STRICT_MARGIN_FALLBACK_FULL = 0.44
_STRICT_MARGIN_DELTA = 0.09

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


ProgressCallback = Callable[[int, str], None]


def _emit_progress(progress_cb: ProgressCallback | None, percent: int, message: str) -> None:
    if not progress_cb:
        return
    progress_cb(max(0, min(100, percent)), message)


def _resolve_engine_mode() -> str:
    mode = (os.getenv("AUTOCAPCUT_SRT_ENGINE", "auto") or "auto").strip().lower()
    if mode not in {"auto", "python", "rust"}:
        return "auto"
    return mode


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
    # Common abbreviation harmonisation for speech transcripts.
    text = re.sub(r"\bdr\.?\b", "doctor", text)

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


def _coverage_ratio(acc_words: List[str], content_words: List[str]) -> float:
    """
    Token coverage of content within accumulated SRT words.
    Uses multiset overlap so repeated words are handled reasonably.
    """
    if not content_words:
        return 0.0
    content_counter = Counter(content_words)
    acc_counter = Counter(acc_words)
    overlap = sum(min(content_counter[token], acc_counter.get(token, 0)) for token in content_counter)
    return overlap / len(content_words)


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
    progress_cb: ProgressCallback | None = None,
    progress_start: int = 45,
    progress_end: int = 88,
) -> List[OutputEntry]:
    """
    Strictly match each content item to CapCut SRT segments.

    CapCut timestamps are treated as source of truth. We choose segment ranges
    by fuzzy scoring, but keep strict monotonic ordering and confidence guards
    so timestamps stay aligned to the source transcript.
    """
    if not content_items:
        return []
    if not srt_segments:
        return []

    normalized_segment_words: List[List[str]] = [
        _normalize(seg.text).split() for seg in srt_segments
    ]

    if not any(normalized_segment_words):
        raise SRTGeneratorError("SRT không có nội dung chữ hợp lệ để đối chiếu.")

    def score_start_candidate(
        start_idx: int,
        content_norm: str,
        content_words: List[str],
    ) -> tuple[float, float, float, float, int]:
        n_content_words = max(len(content_words), 1)
        max_words = max(int(n_content_words * 1.9), n_content_words + 12)

        accumulated_words: List[str] = []
        best_score = -1.0
        best_full_ratio = 0.0
        best_prefix_ratio = 0.0
        best_coverage = 0.0
        best_end_idx = start_idx

        look = start_idx
        while look < len(srt_segments) and (look - start_idx) < _STRICT_MAX_LOOKAHEAD_SEGMENTS:
            seg_words = normalized_segment_words[look]
            if seg_words:
                accumulated_words.extend(seg_words)
                acc_norm = " ".join(accumulated_words)
                full_ratio = _similarity(acc_norm, content_norm)

                prefix_words = min(_STRICT_PREFIX_MAX_WORDS, len(accumulated_words), len(content_words))
                prefix_ratio = 0.0
                if prefix_words > 0:
                    prefix_ratio = _similarity(
                        " ".join(accumulated_words[:prefix_words]),
                        " ".join(content_words[:prefix_words]),
                    )
                coverage = _coverage_ratio(accumulated_words, content_words)

                len_penalty = abs(len(accumulated_words) - n_content_words) / n_content_words
                score = (
                    (full_ratio * 0.60)
                    + (prefix_ratio * 0.20)
                    + (coverage * 0.20)
                    - (len_penalty * 0.08)
                )

                if score > best_score:
                    best_score = score
                    best_full_ratio = full_ratio
                    best_prefix_ratio = prefix_ratio
                    best_coverage = coverage
                    best_end_idx = look

                if (
                    full_ratio >= 0.95
                    and coverage >= 0.80
                    and len(accumulated_words) >= int(n_content_words * 0.8)
                ):
                    break
                if len(accumulated_words) >= max_words:
                    break
            look += 1

        return best_score, best_full_ratio, best_prefix_ratio, best_coverage, best_end_idx

    results: List[OutputEntry] = []
    srt_ptr = 0
    total = len(srt_segments)
    content_total = len(content_items)

    for content_idx, content in enumerate(content_items, 1):
        if content_total > 0:
            pct = progress_start + int(((content_idx - 1) / content_total) * max(progress_end - progress_start, 1))
            _emit_progress(progress_cb, pct, f"Matching content line {content_idx}/{content_total}...")

        if srt_ptr >= total:
            raise SRTGeneratorError(
                f"Thiếu segment SRT để ghép cho content #{content.index}."
            )

        content_norm = _normalize(content.text)
        content_words = content_norm.split()
        if not content_words:
            raise SRTGeneratorError(f"Content #{content.index} trống sau chuẩn hoá.")

        chosen_start: int | None = None
        chosen_end: int | None = None
        chosen_score = -1.0
        chosen_full_ratio = 0.0
        chosen_prefix_ratio = 0.0
        chosen_coverage = 0.0
        second_best_score = -1.0

        scan_end = min(total, srt_ptr + _STRICT_START_SCAN_SEGMENTS)
        for start_idx in range(srt_ptr, scan_end):
            if not normalized_segment_words[start_idx]:
                continue
            score, full_ratio, prefix_ratio, coverage, end_idx = score_start_candidate(
                start_idx,
                content_norm,
                content_words,
            )

            better = score > chosen_score + 0.005
            tie = abs(score - chosen_score) <= 0.005
            if better or (
                tie and (
                    prefix_ratio > chosen_prefix_ratio + 0.01
                    or (
                        abs(prefix_ratio - chosen_prefix_ratio) <= 0.01
                        and coverage > chosen_coverage + 0.01
                    )
                    or (
                        abs(prefix_ratio - chosen_prefix_ratio) <= 0.01
                        and abs(coverage - chosen_coverage) <= 0.01
                        and full_ratio > chosen_full_ratio + 0.01
                    )
                    or (
                        abs(prefix_ratio - chosen_prefix_ratio) <= 0.01
                        and abs(coverage - chosen_coverage) <= 0.01
                        and abs(full_ratio - chosen_full_ratio) <= 0.01
                        and start_idx > (chosen_start if chosen_start is not None else -1)
                    )
                )
            ):
                if chosen_score > second_best_score:
                    second_best_score = chosen_score
                chosen_start = start_idx
                chosen_end = end_idx
                chosen_score = score
                chosen_full_ratio = full_ratio
                chosen_prefix_ratio = prefix_ratio
                chosen_coverage = coverage
            elif score > second_best_score:
                second_best_score = score

        if chosen_start is None or chosen_end is None:
            raise SRTGeneratorError(
                f"Không thể ghép chắc chắn content #{content.index} với SRT gốc (Strict CapCut)."
            )
        strong_confidence = (
            chosen_full_ratio >= _STRICT_MIN_FULL_RATIO
            and chosen_prefix_ratio >= _STRICT_MIN_PREFIX_RATIO
            and chosen_coverage >= _STRICT_MIN_COVERAGE
            and chosen_score >= _STRICT_MIN_SCORE
        )
        margin_confidence = (
            chosen_score >= _STRICT_MARGIN_FALLBACK_SCORE
            and chosen_full_ratio >= _STRICT_MARGIN_FALLBACK_FULL
            and (chosen_score - second_best_score) >= _STRICT_MARGIN_DELTA
        )
        if not (strong_confidence or margin_confidence):
            raise SRTGeneratorError(
                f"Độ tin cậy ghép thấp ở content #{content.index} (Strict CapCut)."
            )

        logger.debug(
            "Strict match content #{} -> SRT[{}..{}] score={:.2f} full={:.2f} prefix={:.2f} cov={:.2f} delta={:.2f}",
            content.index,
            chosen_start,
            chosen_end,
            chosen_score,
            chosen_full_ratio,
            chosen_prefix_ratio,
            chosen_coverage,
            chosen_score - second_best_score,
        )

        results.append(
            OutputEntry(
                index=content.index,
                start=srt_segments[chosen_start].start,
                end=srt_segments[chosen_end].end,
                text=content.text,
            )
        )
        srt_ptr = chosen_end + 1
        if content_total > 0:
            pct = progress_start + int((content_idx / content_total) * max(progress_end - progress_start, 1))
            _emit_progress(progress_cb, pct, f"Matched content line {content_idx}/{content_total}.")

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

def generate_merged_srt(
    srt_path: Path,
    content_text: str,
    progress_cb: ProgressCallback | None = None,
) -> str:
    """
    Read *srt_path* (CapCut SRT) and *content_text* (raw content string),
    return the merged SRT as a string.

    Raises SRTGeneratorError on any failure.
    """
    _emit_progress(progress_cb, 6, "Reading CapCut SRT file...")

    # --- Parse SRT ---
    try:
        srt_raw = _read_file_text(srt_path)
    except OSError as exc:
        raise SRTGeneratorError(f"Không đọc được file SRT: {exc}") from exc

    _emit_progress(progress_cb, 18, "Parsing CapCut SRT segments...")
    srt_segments = parse_srt(srt_raw)
    if not srt_segments:
        raise SRTGeneratorError("File SRT không có segment nào hợp lệ.")

    _emit_progress(progress_cb, 32, f"Parsed {len(srt_segments)} SRT segments.")

    # --- Parse content ---
    _emit_progress(progress_cb, 38, "Parsing pasted content...")
    content_items = parse_content(content_text)
    if not content_items:
        raise SRTGeneratorError("Không tìm thấy câu content nào.")
    _emit_progress(progress_cb, 44, f"Parsed {len(content_items)} content lines.")

    logger.info(
        "SRT segments: {} | Content items: {}",
        len(srt_segments), len(content_items),
    )

    # --- Match ---
    _emit_progress(progress_cb, 45, "Aligning content lines to CapCut timestamps...")
    entries: List[OutputEntry] | None = None
    engine_mode = _resolve_engine_mode()

    if engine_mode in {"auto", "rust"}:
        from autocapcut.services.rust_srt_engine import match_content_to_srt_rust

        rust_entries = match_content_to_srt_rust(
            content_items,
            srt_segments,
            progress_cb=progress_cb,
            progress_start=45,
            progress_end=88,
        )
        if rust_entries is not None:
            entries = rust_entries
            logger.info("SRT matching engine: Rust")
        elif engine_mode == "rust":
            raise SRTGeneratorError(
                "Rust engine được bật bắt buộc nhưng không chạy được. "
                "Kiểm tra binary hoặc dùng AUTOCAPCUT_SRT_ENGINE=auto/python."
            )

    if entries is None:
        _emit_progress(progress_cb, 45, "Rust engine unavailable, using Python matcher...")
        entries = match_content_to_srt(content_items, srt_segments, progress_cb, 45, 88)
        logger.info("SRT matching engine: Python")

    if not entries:
        raise SRTGeneratorError("Không thể ghép content với SRT segments.")
    if len(entries) != len(content_items):
        raise SRTGeneratorError(
            "Ghép SRT chưa đầy đủ trong chế độ Strict CapCut; dừng để tránh sai timestamp."
        )

    _emit_progress(progress_cb, 92, "Rendering output SRT...")
    output = _to_srt_string(entries)
    logger.info("Generated {} output entries.", len(entries))
    _emit_progress(progress_cb, 94, "SRT generation complete.")
    return output
