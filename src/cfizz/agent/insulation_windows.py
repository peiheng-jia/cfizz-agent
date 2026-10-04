"""Window choices backed by columns in precomputed insulation tables."""

from __future__ import annotations

import gzip
from pathlib import Path
import re
from typing import Iterable


_SCORE_COLUMN = re.compile(r"(?:log2_)?insulation_score_(\d+)")
_BOUNDARY_COLUMN = re.compile(r"is_boundary_(\d+)")


def insulation_window_sizes(path: str | Path) -> tuple[int, ...]:
    """Read only the header; a usable boundary window needs both columns."""
    table = Path(path)
    opener = gzip.open if table.suffix.lower() == ".gz" else open
    with opener(table, "rt", encoding="utf-8-sig") as handle:
        header = next((line.strip() for line in handle if line.strip() and not line.startswith("#")), "")
    columns = (column.strip().casefold() for column in header.split("\t"))
    scores: set[int] = set()
    boundaries: set[int] = set()
    for column in columns:
        score = _SCORE_COLUMN.fullmatch(column)
        boundary = _BOUNDARY_COLUMN.fullmatch(column)
        if score:
            scores.add(int(score.group(1)))
        if boundary:
            boundaries.add(int(boundary.group(1)))
    return tuple(sorted(scores & boundaries))


def common_insulation_windows(paths: Iterable[str | Path]) -> tuple[int, ...]:
    """Return windows every selected sample can render."""
    choices = [set(insulation_window_sizes(path)) for path in dict.fromkeys(map(str, paths))]
    return tuple(sorted(set.intersection(*choices))) if choices else ()


def format_insulation_windows(windows: Iterable[int]) -> str:
    def label(size: int) -> str:
        if size % 1_000_000 == 0:
            return f"{size // 1_000_000} Mb"
        if size % 1_000 == 0:
            return f"{size // 1_000} kb"
        return f"{size} bp"

    return "、".join(label(size) for size in windows)


def require_insulation_window(paths: Iterable[str | Path], requested: int) -> tuple[int, ...]:
    """Reject a window absent from any bound insulation file."""
    paths = tuple(dict.fromkeys(map(str, paths)))
    if not paths:
        raise ValueError("当前图没有绑定 insulation 文件，无法确认 TAD 窗口。")
    available = common_insulation_windows(paths)
    if requested not in available:
        label = format_insulation_windows((requested,))
        if available:
            raise ValueError(
                f"当前 insulation 文件没有共同的 {label} 窗口列；"
                f"可选窗口：{format_insulation_windows(available)}。"
                "如需其他窗口，请先重新计算并导入包含对应列的 insulation 文件。"
            )
        raise ValueError("当前 insulation 文件没有可共同使用的窗口列；请检查各文件的 insulation score 和 is_boundary 列。")
    return available


def select_insulation_window(paths: Iterable[str | Path], requested: int | None = None) -> int:
    """Use an explicit available column, or pick a default from the file."""
    paths = tuple(dict.fromkeys(map(str, paths)))
    available = common_insulation_windows(paths)
    if not available:
        require_insulation_window(paths, requested if requested is not None else 100_000)
    if requested is not None:
        if requested not in available:
            require_insulation_window(paths, requested)
        return requested
    return 100_000 if 100_000 in available else available[0]
