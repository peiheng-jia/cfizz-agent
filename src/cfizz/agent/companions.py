"""Discover precomputed files that belong to a Hi-C matrix."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from ..utils.range_utils import calc_anchor_range, calc_smart_union_range, calc_symmetric_range


@dataclass(frozen=True)
class CompanionFile:
    path: Path
    resolution: Optional[int] = None


@dataclass(frozen=True)
class ViewportSuggestion:
    """A data-backed genomic viewport suggestion.

    ``reason`` is intentionally kept alongside the coordinates.  The web UI
    can show users why a range was selected (for example, ``TAD 差异事件``)
    instead of making an automatic choice look like an unexplained constant.
    """

    start: int
    end: int
    reason: str
    method: str
    source_paths: tuple[str, ...] = ()


def _search_roots(source_path: Path) -> Iterable[Path]:
    seen = set()
    candidates = [source_path.parent]
    for parent in list(source_path.parents)[:3]:
        candidates.extend((parent / "output", parent))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in seen and resolved.exists() and resolved.is_dir():
            seen.add(resolved)
            yield resolved


def _resolution_from_name(path: Path) -> Optional[int]:
    name = path.name.lower()
    insulation_resolution = re.findall(r"[._-](\d+)(?=\.insulation(?:\.|$))", name)
    if insulation_resolution:
        return int(insulation_resolution[-1])
    # Boundary exports use the compact ``<resolution>.<window>b.boundaries``
    # spelling (for example ``...10000.10b.boundaries.tsv``), so the generic
    # ``10b`` token must not be mistaken for the matrix bin width.  The token
    # immediately before the window/``boundaries`` suffix is the resolution.
    boundary_resolution = re.findall(
        r"[._-](\d+)(?=\.\d+(?:\.\d+)?b\.boundar(?:y|ies)(?:\.|$))",
        name,
    )
    if boundary_resolution:
        return int(boundary_resolution[-1])
    # Prefer delimiter-separated integer tokens. This avoids interpreting the
    # sample suffix in ``sample_1000.100kb.E1.tsv`` as decimal ``1000.100kb``.
    matches = re.findall(r"(?:^|[._-])(\d+)\s*(kb|k|mb|m)(?=$|[._-])", name)
    if not matches:
        matches = re.findall(r"(?:^|[._-])(\d+\.\d+)\s*(mb|m)(?=$|[._-])", name)
    if not matches:
        return None
    value, unit = matches[-1]
    scale = 1_000_000 if unit in {"mb", "m"} else 1_000
    return int(float(value) * scale)


def companion_resolution(path: str | Path) -> Optional[int]:
    """Return the bin width encoded in a standard CFIZZ result filename."""

    return _resolution_from_name(Path(path))


def _sample_aliases(source_path: Path) -> list[str]:
    stem = source_path.stem.lower()
    aliases = [stem]
    without_resolution = re.sub(r"[_-](?:1000|\d+(?:kb|k|mb|m))$", "", stem)
    if len(without_resolution) >= 3 and without_resolution != stem:
        aliases.append(without_resolution)
    return aliases


def discover_companion(source_path: str | Path, kind: str, chrom: Optional[str] = None) -> Optional[CompanionFile]:
    """Find the best matching CFIZZ analysis result near a cool/mcool file."""
    source = Path(source_path).expanduser().resolve()
    aliases = _sample_aliases(source)
    candidates: list[tuple[int, Path]] = []

    for root in _search_roots(source):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            full = str(path).lower()
            if kind == "compartment":
                if path.suffix.lower() != ".tsv" or not ("e1" in name or "eigenvector" in name):
                    continue
                score = 80
            elif kind == "loops":
                if path.suffix.lower() not in {".txt", ".tsv", ".bedpe"} or "loop" not in name:
                    continue
                score = 60
                if ".loops." in name or name.endswith("loops.txt"):
                    score += 30
            elif kind == "oe":
                if path.suffix.lower() not in {".txt", ".npy"} or not ("observed_expected_normalized" in name or ".oe." in name):
                    continue
                score = 90
                if chrom and chrom.lower() in name:
                    score += 80
            elif kind == "insulation":
                if path.suffix.lower() != ".tsv" or "insulation" not in name:
                    continue
                score = 90
                if "1_computation" in full and "/tad/" in full.replace("\\", "/"):
                    score += 40
            elif kind == "boundaries":
                if path.suffix.lower() != ".tsv" or "boundar" not in name:
                    continue
                score = 80
                if ".10b.boundaries." in name:
                    score += 20
                if "1_computation" in full and "/tad/" in full.replace("\\", "/"):
                    score += 40
            else:
                raise ValueError(f"未知配套数据类型：{kind}")

            if any(alias in full for alias in aliases):
                score += 120
            if "1_computation" in full or "primary_analysis" in full:
                score += 20
            if any(token in full for token in ("differential", "diff_result", "plot_data", "visualization")):
                score -= 100
            candidates.append((score, path))

    if not candidates:
        return None
    _, best = max(candidates, key=lambda item: (item[0], -len(str(item[1]))))
    return CompanionFile(best, _resolution_from_name(best))


def suggest_compartment_viewport(
    eigenvector_path: str | Path,
    chrom: str,
    start: int,
    end: int,
    minimum_width: int = 20_000_000,
) -> tuple[int, int]:
    """Keep an informative viewport or choose the densest valid E1 window."""
    import numpy as np
    import pandas as pd

    frame = pd.read_csv(eigenvector_path, sep="\t", usecols=["chrom", "start", "end", "E1"])
    frame = frame[frame["chrom"].astype(str) == chrom].copy()
    frame["E1"] = pd.to_numeric(frame["E1"], errors="coerce")
    valid = frame[frame["E1"].notna()].sort_values("start")
    if valid.empty:
        return start, end
    current = valid[(valid["start"] < end) & (valid["end"] > start)]
    # Compartment is a broad-scale signal; a tiny 2 Mb heatmap may technically
    # contain a few E1 bins but still produces the sparse, misleading view seen
    # in the UI. Keep only sufficiently broad, populated user viewports.
    if end - start >= minimum_width and len(current) >= 5:
        return start, end

    width = max(end - start, minimum_width)
    positions = valid["start"].to_numpy(dtype=np.int64)
    left = 0
    best_left = 0
    best_count = 0
    for right, position in enumerate(positions):
        while position - positions[left] >= width:
            left += 1
        count = right - left + 1
        if count > best_count:
            best_left, best_count = left, count
    suggested_start = max(0, int(positions[best_left] // 1_000_000) * 1_000_000)
    chrom_end = int(frame["end"].max())
    suggested_end = min(chrom_end, suggested_start + width)
    suggested_start = max(0, suggested_end - width)
    return suggested_start, suggested_end


def suggest_feature_viewport(
    feature_path: str | Path,
    chrom: str,
    start: int,
    end: int,
    *,
    minimum_width: int = 20_000_000,
) -> tuple[int, int]:
    """Choose a useful default window from an insulation/boundary/loop file.

    The UI starts with a small neutral range so the field remains convenient,
    but broad TAD and loop summaries are not meaningful in a 2 Mb window.  We
    therefore use the coordinates in the already-discovered CFIZZ companion
    file to place a fixed-width window over the densest feature region.  This
    only changes an automatic default; an explicitly entered user range never
    calls this helper.
    """
    import pandas as pd

    path = Path(feature_path)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline().strip().lower().split("\t")
        has_header = any(token in {"chrom", "chr", "chrom1"} for token in first)
        frame = pd.read_csv(path, sep="\t", header=0 if has_header else None, comment="#")
    except (OSError, ValueError, pd.errors.ParserError):
        return start, end
    if frame.empty or frame.shape[1] < 3:
        return start, end

    def col_index(names: set[str], fallback: int | None = None) -> int | None:
        if not has_header:
            return fallback
        normalised = {str(name).strip().lower(): index for index, name in enumerate(frame.columns)}
        for name in names:
            if name in normalised:
                return normalised[name]
        return fallback

    c1 = col_index({"chrom", "chr", "chrom1"}, 0)
    s1 = col_index({"start", "start1", "x1"}, 1)
    e1 = col_index({"end", "end1", "x2"}, 2)
    if c1 is None or s1 is None or e1 is None:
        return start, end

    # BEDPE/loop files store the second anchor in columns 3–4.  Include both
    # anchors so a loop spanning the window is represented by its midpoint.
    c2 = col_index({"chrom2", "chr2"}, 3 if not has_header and frame.shape[1] >= 5 else None)
    s2 = col_index({"start2", "y1"}, 4 if not has_header and frame.shape[1] >= 5 else None)
    e2 = col_index({"end2", "y2"}, 5 if not has_header and frame.shape[1] >= 6 else None)

    def numeric(index: int) -> pd.Series:
        return pd.to_numeric(frame.iloc[:, index], errors="coerce")

    chroms = frame.iloc[:, c1].astype(str)
    mask = chroms == chrom
    starts = numeric(s1)
    ends = numeric(e1)
    if c2 is not None and s2 is not None and e2 is not None:
        chroms2 = frame.iloc[:, c2].astype(str)
        starts2, ends2 = numeric(s2), numeric(e2)
        both = mask & (chroms2 == chrom) & starts.notna() & ends.notna() & starts2.notna() & ends2.notna()
        positions = ((starts[both] + ends[both] + starts2[both] + ends2[both]) / 4).to_numpy(dtype="int64")
        max_end = int(max(ends[both].max() if both.any() else 0, ends2[both].max() if both.any() else 0))
    else:
        valid = mask & starts.notna() & ends.notna()
        positions = ((starts[valid] + ends[valid]) / 2).to_numpy(dtype="int64")
        max_end = int(ends[valid].max()) if valid.any() else 0
    if not len(positions):
        return start, end

    width = max(int(minimum_width), int(end - start))
    positions.sort()
    left = best_left = best_count = 0
    for right, position in enumerate(positions):
        while position - positions[left] >= width and left < right:
            left += 1
        count = right - left + 1
        if count > best_count:
            best_left, best_count = left, count
    suggested_start = max(0, int(positions[best_left] // 1_000_000) * 1_000_000)
    suggested_end = max(max_end, suggested_start + width)
    suggested_end = min(suggested_end, suggested_start + width)
    suggested_start = max(0, suggested_end - width)
    return suggested_start, suggested_end


# ---------------------------------------------------------------------------
# Viewport selection for the Agent
# ---------------------------------------------------------------------------
#
# The example workflow ``examples/diff/6_2_differential_visualization.py``
# does not pick a chromosome window from a UI constant.  It derives one from
# the selected event: TAD rows use ``calc_smart_union_range``, loop anchors use
# ``calc_anchor_range`` and compartment events use ``calc_symmetric_range``.
# The helpers below keep that same policy in the Agent while still accepting
# ordinary (non-differential) CFIZZ result tables.  They deliberately operate
# only on paths already present in a FigureSpec; they never search for a
# different companion file behind the user's back.


def _table_with_header(path: str | Path):
    """Read a small CFIZZ result table and report whether it has a header."""
    import pandas as pd

    table_path = Path(path)
    try:
        with table_path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline().strip()
        tokens = [token.strip().casefold() for token in first.split("\t")]
        header_tokens = {
            "chrom", "chr", "chrom1", "start", "start1", "end", "end1",
            "sample1_start", "sample2_start", "is_boundary", "e1",
            "final_classification", "compartment_change", "classification",
            "class", "status", "change_type", "is_stable", "diff_type",
        }
        has_header = bool(set(tokens) & header_tokens)
        frame = pd.read_csv(
            table_path,
            sep="\t",
            header=0 if has_header else None,
            comment="#",
            low_memory=False,
        )
    except (OSError, ValueError, pd.errors.ParserError):
        return None, False
    return frame, has_header


def is_differential_tad_table(path: str | Path) -> bool:
    """Return whether *path* is a differential TAD/boundary result table.

    Ordinary boundary exports also use ``.boundaries.tsv`` and therefore cannot
    be distinguished reliably from a filename alone.  The differential
    example, however, has either the four sample-specific boundary columns or
    an explicit classification column.  Keeping this check in one place lets
    both the viewport selector and the workflow adapter apply the same rule.
    """
    frame, has_header = _table_with_header(path)
    if frame is None or frame.empty or not has_header:
        return False
    names = {
        re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
        for value in frame.columns
    }
    if {"sample1_start", "sample1_end", "sample2_start", "sample2_end"} <= names:
        return True
    has_interval = bool(names & {"start", "start1", "bin_start"}) and bool(
        names & {"end", "end1", "bin_end"}
    )
    return has_interval and bool(
        names & {"final_classification", "primary_label", "diff_type", "change_type"}
    )


def _column(frame, has_header: bool, names: set[str], fallback: int | None = None) -> int | None:
    if not has_header:
        return fallback
    lookup = {str(value).strip().casefold(): index for index, value in enumerate(frame.columns)}
    for name in names:
        if name.casefold() in lookup:
            return lookup[name.casefold()]
    return fallback


def _number(frame, index: int | None):
    import pandas as pd

    if index is None or index < 0 or index >= frame.shape[1]:
        return None
    return pd.to_numeric(frame.iloc[:, index], errors="coerce")


def _text(frame, index: int | None):
    if index is None or index < 0 or index >= frame.shape[1]:
        return None
    return frame.iloc[:, index].astype(str).str.strip()


def _boundary_column(
    frame,
    has_header: bool,
    *,
    resolution: int | None = None,
    source_path: str | Path | None = None,
) -> int | None:
    """Select the boundary flag matching the selected matrix resolution.

    A CFIZZ boundary table can contain several flags (50 kb, 100 kb and
    500 kb) in the same file.  The old set-based lookup picked one
    nondeterministically, which made the same input produce different ranges.
    Prefer the exact flag, otherwise choose the flag closest to the encoded
    ``<window>b`` column used by the filename (``10000.10b`` means 100 kb).
    """
    exact = _column(frame, has_header, {"is_boundary", "boundary"})
    if exact is not None:
        return exact
    if not has_header:
        return None
    candidates: list[tuple[int, int]] = []
    for index, name in enumerate(frame.columns):
        token = str(name).strip().casefold()
        match = re.fullmatch(r"is_boundary_(\d+)", token)
        if match:
            candidates.append((int(match.group(1)), index))
    if not candidates:
        return None
    target: int | None = None
    if source_path is not None and resolution:
        window = re.search(r"\.(\d+(?:\.\d+)?)b\.boundar(?:y|ies)(?:\.|$)", str(source_path).casefold())
        if window:
            target = int(round(float(window.group(1)) * int(resolution)))
    if target is None:
        return min(candidates, key=lambda item: (item[0], item[1]))[1]
    return min(candidates, key=lambda item: (abs(item[0] - target), item[0], item[1]))[1]


def _truthy(values):
    if values is None:
        return None
    return values.str.casefold().isin({"true", "1", "yes", "y", "t"})


def _densest_window(
    positions: Sequence[int],
    width: int,
    start: int,
    end: int,
    *,
    resolution: int | None = None,
    max_end: int | None = None,
) -> tuple[int, int] | None:
    """Return a density window without imposing a chromosome-wide constant."""
    if not positions:
        return None
    ordered = sorted(int(position) for position in positions if int(position) >= 0)
    if not ordered:
        return None
    width = max(1, int(width))
    left = best_left = best_right = 0
    best_count = -1
    current_center = (int(start) + int(end)) / 2
    best_distance = float("inf")
    for right, position in enumerate(ordered):
        while left < right and position - ordered[left] >= width:
            left += 1
        count = right - left + 1
        center = (ordered[left] + position) / 2
        distance = abs(center - current_center)
        if count > best_count or (count == best_count and distance < best_distance):
            best_left, best_right, best_count, best_distance = left, right, count, distance
    window_start = ordered[best_left]
    window_end = max(window_start + width, ordered[best_right] + 1)
    # Align ordinary windows to a readable genomic boundary.  Event-derived
    # ranges are already aligned by the official range helpers and bypass this
    # path when they are available.
    alignment = max(1_000_000, int(resolution or 1))
    window_start = max(0, (window_start // alignment) * alignment)
    window_end = window_start + width
    if max_end is not None and max_end > 0:
        window_end = min(window_end, int(max_end))
        window_start = max(0, window_end - width)
    return int(window_start), int(window_end)


def _align_viewport_range(
    start: int,
    end: int,
    resolution: int,
    *,
    chrom_end: int | None = None,
) -> tuple[int, int]:
    """Align an automatically selected interval to the matrix bin width.

    CFIZZ's matrix readers require the requested interval length (and usually
    its boundaries) to be representable at the selected resolution.  Event
    helpers intentionally return biological coordinates, so the final UI
    viewport is rounded here rather than failing later in the renderer with
    ``Interval length must match resolution``.
    """
    step = max(1, int(resolution or 1))
    left = max(0, (int(start) // step) * step)
    right = max(left + step, int(math.ceil(max(left + step, int(end)) / step) * step))
    if chrom_end is not None and int(chrom_end) > 0:
        limit = max(step, (int(chrom_end) // step) * step)
        if left >= limit:
            left = max(0, limit - step)
        right = min(right, limit)
        if right <= left:
            left = max(0, right - step)
    return int(left), int(right)


def _tad_event_ranges(frame, has_header: bool, chrom: str, resolution: int) -> list[tuple[int, int, int]]:
    """Read differential TAD context rows as ``(start, end, priority)``."""
    chrom_col = _column(frame, has_header, {"chrom", "chr"}, 0)
    sample1_start = _column(frame, has_header, {"sample1_start"})
    sample1_end = _column(frame, has_header, {"sample1_end"})
    sample2_start = _column(frame, has_header, {"sample2_start"})
    sample2_end = _column(frame, has_header, {"sample2_end"})
    chrom_values = _text(frame, chrom_col)
    if chrom_values is None:
        return []
    mask = chrom_values == str(chrom)
    classification_col = _column(
        frame,
        has_header,
        {
            "final_classification", "classification", "class", "diff_type",
            "change_type", "primary_label", "status", "is_stable",
        },
    )
    classification = _text(frame, classification_col)

    # A number of CFIZZ exports contain a compact differential table with one
    # ``start/end`` pair instead of the four sample-specific coordinates.  It
    # is still an event table: use changed/non-stable rows and give each event
    # the same resolution-aware flank as the full two-sample format.
    if None in {sample1_start, sample1_end, sample2_start, sample2_end}:
        start_col = _column(frame, has_header, {"start", "start1", "bin_start"}, 1)
        end_col = _column(frame, has_header, {"end", "end1", "bin_end"}, 2)
        starts, ends = _number(frame, start_col), _number(frame, end_col)
        if starts is None or ends is None:
            return []
        # Without an explicit change/state column this is an ordinary boundary
        # grid, not an event table.  Treating every row as a differential event
        # was the source of the arbitrary 15--35 Mb windows in the UI.
        if classification is None:
            return []
        labels = classification.str.casefold()
        if str(frame.columns[classification_col]).casefold() == "is_stable":
            truth = _truthy(labels)
            changed = ~truth if truth is not None else ~labels.str.contains(
                r"true|stable|unchanged", na=False, regex=True
            )
        else:
            changed = ~labels.str.contains(
                r"stable|unchanged|no[_ -]?change|no[_ -]?shift|same|not[_ -]?changed",
                na=False,
                regex=True,
            )
        if not bool((mask & changed).any()):
            return []
        mask &= changed
        flank = max(200_000, 20 * int(resolution))
        half_span_bins = max(1, int(math.ceil(flank / max(1, int(resolution)))))
        records: list[tuple[int, int, int]] = []
        valid = mask & starts.notna() & ends.notna()
        for index in frame.index[valid]:
            try:
                center = int((float(starts.loc[index]) + float(ends.loc[index])) / 2)
                left, right = calc_symmetric_range(center, half_span_bins, int(resolution))
            except (TypeError, ValueError, OverflowError):
                continue
            records.append((max(0, left), max(left + int(resolution), right), 2))
        return records

    if classification is not None:
        non_stable = mask & ~classification.str.casefold().str.contains("stable", na=False)
        if bool(non_stable.any()):
            mask = non_stable
    fields = {
        "s1_start": _number(frame, sample1_start),
        "s1_end": _number(frame, sample1_end),
        "s2_start": _number(frame, sample2_start),
        "s2_end": _number(frame, sample2_end),
        "s1_up": _number(frame, _column(frame, has_header, {"sample1_dist_upstream", "sample1_dist_up"})),
        "s1_down": _number(frame, _column(frame, has_header, {"sample1_dist_downstream", "sample1_dist_down"})),
        "s2_up": _number(frame, _column(frame, has_header, {"sample2_dist_upstream", "sample2_dist_up"})),
        "s2_down": _number(frame, _column(frame, has_header, {"sample2_dist_downstream", "sample2_dist_down"})),
    }
    if any(value is None for value in fields.values()):
        return []
    default_flank = max(200_000, 20 * int(resolution))
    records: list[tuple[int, int, int]] = []
    for index in frame.index[mask]:
        try:
            plot_start, plot_end = calc_smart_union_range(
                int(fields["s1_start"].loc[index]), int(fields["s1_end"].loc[index]),
                int(fields["s2_start"].loc[index]), int(fields["s2_end"].loc[index]),
                float(fields["s1_up"].loc[index]), float(fields["s1_down"].loc[index]),
                float(fields["s2_up"].loc[index]), float(fields["s2_down"].loc[index]),
                default_flank=default_flank,
                resolution=int(resolution),
            )
        except (TypeError, ValueError, OverflowError):
            continue
        priority = 2 if classification is not None and not classification.loc[index].casefold().startswith("stable") else 1
        records.append((max(0, plot_start), max(plot_start + resolution, plot_end), priority))
    return records


def _tad_feature_positions(
    frame,
    has_header: bool,
    chrom: str,
    *,
    resolution: int | None = None,
    source_path: str | Path | None = None,
) -> tuple[list[int], int]:
    chrom_col = _column(frame, has_header, {"chrom", "chr"}, 0)
    start_col = _column(frame, has_header, {"start", "start1", "bin_start"}, 1)
    end_col = _column(frame, has_header, {"end", "end1", "bin_end"}, 2)
    chrom_values = _text(frame, chrom_col)
    starts, ends = _number(frame, start_col), _number(frame, end_col)
    if chrom_values is None or starts is None or ends is None:
        return [], 0
    all_mask = (chrom_values == str(chrom)) & starts.notna() & ends.notna()
    mask = all_mask.copy()
    boundary_col = _boundary_column(
        frame,
        has_header,
        resolution=resolution,
        source_path=source_path,
    )
    if boundary_col is not None:
        boundary = _truthy(_text(frame, boundary_col))
        if boundary is not None and bool((mask & boundary).any()):
            mask &= boundary
    positions = [int((left + right) / 2) for left, right in zip(starts[mask], ends[mask])]
    # The chromosome extent must come from all valid rows, not only boundary
    # rows.  Otherwise an event near the end of a table is clamped to the last
    # boundary flag and the automatically selected interval becomes too short.
    max_end = int(ends[all_mask].max()) if bool(all_mask.any()) else 0
    return positions, max_end


def _compartment_change_column(frame, has_header: bool) -> int | None:
    """Find the change-state column used by CFIZZ compartment exports.

    The differential example writes names such as
    ``compartment_change_51_5`` rather than a literal ``compartment_change``
    header.  Matching the prefix here keeps the viewport logic independent of
    sample names while avoiding treating every ordinary E1 bin as a change.
    """
    column = _column(frame, has_header, {"compartment_change", "change", "classification"})
    if column is not None or not has_header:
        return column
    for index, name in enumerate(frame.columns):
        token = re.sub(r"[^a-z0-9]+", "_", str(name).casefold()).strip("_")
        if (
            token.startswith("compartment_change")
            or token.startswith("change_type")
            or token in {"state_change", "transition"}
        ):
            return index
    return None


def _compartment_event_ranges(
    frame,
    has_header: bool,
    chrom: str,
    resolution: int,
) -> list[tuple[int, int, int]]:
    """Return official-style windows around changed compartment blocks.

    ``6_2_differential_visualization.py`` first merges adjacent bins with the
    same change label, then expands the block centre by 45 bins at the E1
    resolution.  Repeating that policy here is important: choosing a broad
    density window from all E1 values can silently land on a stable block.
    """
    chrom_col = _column(frame, has_header, {"chrom", "chr"}, 0)
    start_col = _column(frame, has_header, {"start", "start1"}, 1)
    end_col = _column(frame, has_header, {"end", "end1"}, 2)
    change_col = _compartment_change_column(frame, has_header)
    if change_col is None:
        return []
    chrom_values = _text(frame, chrom_col)
    starts, ends = _number(frame, start_col), _number(frame, end_col)
    changes = _text(frame, change_col)
    if any(value is None for value in (chrom_values, starts, ends, changes)):
        return []

    selected = (chrom_values == str(chrom)) & starts.notna() & ends.notna()
    labels = changes.str.casefold()
    # Stable labels are deliberately excluded.  For an unfamiliar producer
    # that uses labels like ``gain``/``loss``/``transition`` this is still
    # conservative: anything not explicitly stable is an event candidate.
    stable = labels.str.contains(
        r"stable|unchanged|no[_ -]?(?:change|shift)|same|not[_ -]?changed",
        na=False,
        regex=True,
    )
    selected &= ~stable
    if not bool(selected.any()):
        return []

    rows = []
    for index in frame.index[selected]:
        try:
            left = int(starts.loc[index])
            right = int(ends.loc[index])
            if right <= left:
                continue
            rows.append((left, right, str(changes.loc[index]).strip()))
        except (TypeError, ValueError, OverflowError):
            continue
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    if not rows:
        return []

    blocks: list[tuple[int, int, str]] = []
    for left, right, label in rows:
        if blocks and blocks[-1][2] == label and left <= blocks[-1][1]:
            blocks[-1] = (blocks[-1][0], max(blocks[-1][1], right), label)
        else:
            blocks.append((left, right, label))

    # Keep the same 45-bin context as the official differential example.  If
    # an unusual E1 grid is used, preserve the 4.5 Mb context in bp while
    # still aligning it to the selected resolution.
    half_span_bins = 45 if int(resolution) == 100_000 else max(
        1, int(round(4_500_000 / max(1, int(resolution))))
    )
    records: list[tuple[int, int, int]] = []
    for left, right, _label in blocks:
        center = (left + right) // 2
        try:
            plot_start, plot_end = calc_symmetric_range(
                center, half_span_bins=half_span_bins, resolution=int(resolution)
            )
        except (TypeError, ValueError, OverflowError):
            continue
        records.append((max(0, plot_start), max(plot_start + int(resolution), plot_end), 2))
    return records


def _dedupe_event_centers(centers: Sequence[int], tolerance: int) -> list[int]:
    """Collapse nearby coordinates while preserving deterministic ordering."""
    ordered = sorted(int(value) for value in centers if int(value) >= 0)
    if not ordered:
        return []
    result = [ordered[0]]
    for value in ordered[1:]:
        if value - result[-1] <= max(1, int(tolerance)):
            result[-1] = (result[-1] + value) // 2
        else:
            result.append(value)
    return result


def _ordinary_tad_diff_event_ranges(
    parsed_tables: Sequence[tuple[object, bool, str]],
    chrom: str,
    resolution: int,
) -> list[tuple[int, int, int]]:
    """Infer TAD events by comparing ordinary boundary grids.

    The UI often receives per-sample ``*.boundaries.tsv`` files rather than
    the optional global ``*_boundary_classification_final.tsv`` result. Those
    files are sufficient: compare the selected boundary positions and expand
    changed anchors using the same 200 kb fallback as the official example.
    A single table can never be mistaken for a comparison.
    """
    samples: list[list[int]] = []
    for frame, has_header, source_path in parsed_tables:
        if is_differential_tad_table(source_path):
            continue
        positions, _ = _tad_feature_positions(
            frame, has_header, chrom, resolution=resolution, source_path=source_path
        )
        if positions:
            samples.append(sorted(set(positions)))
    if len(samples) < 2:
        return []
    tolerance = max(2 * int(resolution), 50_000)
    centers: list[int] = []
    for index, current in enumerate(samples):
        others = [
            position
            for other_index, values in enumerate(samples)
            if other_index != index
            for position in values
        ]
        if not others:
            continue
        for position in current:
            nearest = min(others, key=lambda value: abs(value - position))
            distance = abs(nearest - position)
            if distance > tolerance:
                centers.append(position)
            elif distance > int(resolution):
                centers.append((position + nearest) // 2)
    centers = _dedupe_event_centers(centers, tolerance)
    if not centers:
        return []
    half_span_bins = max(
        1,
        int(math.ceil(max(200_000, 20 * int(resolution)) / max(1, int(resolution)))),
    )
    records: list[tuple[int, int, int]] = []
    for center in centers:
        left, right = calc_symmetric_range(center, half_span_bins, int(resolution))
        records.append((max(0, left), max(left + int(resolution), right), 2))
    return records


def _ordinary_compartment_diff_event_ranges(
    parsed_tables: Sequence[tuple[object, bool, str]],
    chrom: str,
    resolution: int,
) -> list[tuple[int, int, int]]:
    """Infer changed A/B blocks from two ordinary E1 tables."""
    samples: list[dict[int, float]] = []
    for frame, has_header, _source_path in parsed_tables:
        chrom_col = _column(frame, has_header, {"chrom", "chr"}, 0)
        start_col = _column(frame, has_header, {"start", "start1"}, 1)
        e1_col = _column(frame, has_header, {"e1", "eigenvector"}, 3)
        chrom_values = _text(frame, chrom_col)
        starts, values = _number(frame, start_col), _number(frame, e1_col)
        if chrom_values is None or starts is None or values is None:
            continue
        sample: dict[int, float] = {}
        mask = (chrom_values == str(chrom)) & starts.notna() & values.notna()
        for index in frame.index[mask]:
            try:
                sample[int(starts.loc[index])] = float(values.loc[index])
            except (TypeError, ValueError, OverflowError):
                continue
        if sample:
            samples.append(sample)
    if len(samples) < 2:
        return []
    first, second = samples[0], samples[1]
    centers: list[int] = []
    for position in sorted(set(first) & set(second)):
        left, right = first[position], second[position]
        if not math.isfinite(left) or not math.isfinite(right) or left == 0 or right == 0:
            continue
        if (left < 0) != (right < 0):
            centers.append(position + max(1, int(resolution)) // 2)
    if not centers:
        return []
    half_span_bins = 45 if int(resolution) == 100_000 else max(
        1, int(round(4_500_000 / max(1, int(resolution))))
    )
    return [
        (*calc_symmetric_range(center, half_span_bins, int(resolution)), 2)
        for center in _dedupe_event_centers(centers, max(1, int(resolution)))
    ]


def _ordinary_loop_diff_event_ranges(
    parsed_tables: Sequence[tuple[object, bool, str]],
    chrom: str,
) -> list[tuple[int, int, int]]:
    """Infer loop-specific regions by comparing two ordinary BEDPE tables."""
    samples: list[list[tuple[int, int, int, int]]] = []
    for frame, has_header, _source_path in parsed_tables:
        chrom1 = _column(frame, has_header, {"chrom1", "chr1", "chrom"}, 0)
        start1 = _column(frame, has_header, {"start1", "x1"}, 1)
        end1 = _column(frame, has_header, {"end1", "x2"}, 2)
        chrom2 = _column(frame, has_header, {"chrom2", "chr2"}, 3)
        start2 = _column(frame, has_header, {"start2", "y1"}, 4)
        end2 = _column(frame, has_header, {"end2", "y2"}, 5)
        values = [
            _text(frame, chrom1), _text(frame, chrom2), _number(frame, start1),
            _number(frame, end1), _number(frame, start2), _number(frame, end2),
        ]
        if any(value is None for value in values):
            continue
        c1, c2, s1, e1, s2, e2 = values
        mask = (c1 == str(chrom)) & (c2 == str(chrom)) & s1.notna() & e1.notna() & s2.notna() & e2.notna()
        rows: list[tuple[int, int, int, int]] = []
        for index in frame.index[mask]:
            try:
                rows.append((int(s1.loc[index]), int(e1.loc[index]), int(s2.loc[index]), int(e2.loc[index])))
            except (TypeError, ValueError, OverflowError):
                continue
        if rows:
            samples.append(rows)
    if len(samples) < 2:
        return []
    second = samples[1]
    tolerance = 50_000
    records: list[tuple[int, int, int]] = []
    for start1, end1, start2, end2 in samples[0]:
        center_a = ((start1 + end1) // 2, (start2 + end2) // 2)
        matched = any(
            abs(center_a[0] - (row[0] + row[1]) // 2) <= tolerance
            and abs(center_a[1] - (row[2] + row[3]) // 2) <= tolerance
            for row in second
        )
        if matched:
            continue
        try:
            left, right = calc_anchor_range(start1, end1, start2, end2)
        except (TypeError, ValueError, OverflowError):
            continue
        records.append((max(0, left), max(left + 1, right), 2))
    return records


def _loop_anchor_ranges(
    frame,
    has_header: bool,
    chrom: str,
    *,
    differential: bool = False,
) -> list[tuple[int, int, int]]:
    chrom1 = _column(frame, has_header, {"chrom1", "chr1", "chrom"}, 0)
    start1 = _column(frame, has_header, {"start1", "x1"}, 1)
    end1 = _column(frame, has_header, {"end1", "x2"}, 2)
    chrom2 = _column(frame, has_header, {"chrom2", "chr2"}, 3)
    start2 = _column(frame, has_header, {"start2", "y1"}, 4)
    end2 = _column(frame, has_header, {"end2", "y2"}, 5)
    values = [_text(frame, chrom1), _text(frame, chrom2), _number(frame, start1), _number(frame, end1), _number(frame, start2), _number(frame, end2)]
    if any(value is None for value in values):
        return []
    c1, c2, s1, e1, s2, e2 = values
    mask = (c1 == str(chrom)) & (c2 == str(chrom)) & s1.notna() & e1.notna() & s2.notna() & e2.notna()
    if differential:
        stable_col = _column(frame, has_header, {"is_stable", "stable", "status", "classification", "diff_type"})
        stable_values = _text(frame, stable_col)
        # An ordinary loop BEDPE has no state column.  It is useful for a
        # single-sample loop view, but it is not a differential event table;
        # let the paired-file fallback derive changed anchors instead.
        if stable_values is None:
            return []
        labels = stable_values.str.casefold()
        # Boolean is_stable columns are common in CFIZZ unique-loop exports.
        # Keep only rows explicitly marked false; for textual labels, discard
        # stable/unchanged rows and retain gain/lost/diff.
        false_mask = labels.isin({"false", "0", "no", "n"})
        text_change = labels.str.contains(
            r"gain|lost|loss|unique|diff|change|rearrang|unstable",
            na=False,
            regex=True,
        )
        if bool((mask & false_mask).any()):
            mask &= false_mask
        elif bool((mask & text_change).any()):
            mask &= text_change
        elif bool((mask & labels.str.contains("stable|unchanged", na=False)).any()):
            mask &= ~labels.str.contains("stable|unchanged", na=False)
        else:
            return []
    records: list[tuple[int, int, int]] = []
    for index in frame.index[mask]:
        try:
            left, right = calc_anchor_range(int(s1.loc[index]), int(e1.loc[index]), int(s2.loc[index]), int(e2.loc[index]))
        except (TypeError, ValueError, OverflowError):
            continue
        records.append((max(0, left), max(left + 1, right), 1))
    return records


def suggest_selected_viewport(
    feature_paths: Sequence[str | Path],
    chrom: str,
    start: int,
    end: int,
    *,
    kind: str,
    figure_type: str = "",
    resolution: int | None = None,
) -> ViewportSuggestion:
    """Choose a viewport from the exact selected CFIZZ result files.

    ``kind`` is one of ``compartment``, ``insulation``, ``boundaries`` or
    ``loops``.  Differential tables get event-aware ranges; ordinary result
    tables use a density window whose width is appropriate for that feature
    class.  If a table cannot be parsed, the caller receives its current range
    and an explicit fallback reason instead of a fabricated coordinate.
    """
    paths = tuple(str(Path(path)) for path in feature_paths if str(path).strip())
    if not paths:
        return ViewportSuggestion(int(start), int(end), "未找到已选配套结果，保留当前范围", "fallback")
    is_diff = "diff" in str(figure_type).casefold() or "differential" in str(figure_type).casefold()
    resolution = int(resolution or 10_000)

    # Event-derived tables are preferred over ordinary companion grids.
    event_records: list[tuple[int, int, int]] = []
    event_method = "official_event_range"
    positions: list[int] = []
    max_end = 0
    parsed_paths: list[str] = []
    parsed_tables: list[tuple[object, bool, str]] = []
    for raw_path in paths:
        frame, has_header = _table_with_header(raw_path)
        if frame is None or frame.empty:
            continue
        parsed_paths.append(raw_path)
        parsed_tables.append((frame, has_header, raw_path))
        if kind == "boundaries" or kind == "insulation":
            event_records.extend(_tad_event_ranges(frame, has_header, chrom, resolution))
            feature_positions, feature_end = _tad_feature_positions(
                frame,
                has_header,
                chrom,
                resolution=resolution,
                source_path=raw_path,
            )
            positions.extend(feature_positions)
            max_end = max(max_end, feature_end)
        elif kind == "loops":
            new_records = _loop_anchor_ranges(
                frame, has_header, chrom, differential=is_diff
            )
            event_records.extend(new_records)
            for left, right, _ in new_records:
                positions.append((left + right) // 2)
        elif kind == "compartment":
            chrom_col = _column(frame, has_header, {"chrom", "chr"}, 0)
            start_col = _column(frame, has_header, {"start", "start1"}, 1)
            end_col = _column(frame, has_header, {"end", "end1"}, 2)
            e1_col = _column(frame, has_header, {"e1", "eigenvector"}, 3)
            chrom_values = _text(frame, chrom_col)
            starts, ends = _number(frame, start_col), _number(frame, end_col)
            if chrom_values is None or starts is None or ends is None:
                continue
            mask = (chrom_values == str(chrom)) & starts.notna() & ends.notna()
            if e1_col is not None:
                values = _number(frame, e1_col)
                if values is not None:
                    mask &= values.notna()
            change_col = _compartment_change_column(frame, has_header)
            if is_diff and change_col is not None:
                event_records.extend(
                    _compartment_event_ranges(frame, has_header, chrom, resolution)
                )
            for left, right in zip(starts[mask], ends[mask]):
                positions.append(int((left + right) / 2))
                max_end = max(max_end, int(right))

    # If the optional global differential table was not selected, compare the
    # exact ordinary files the user selected.  This keeps the workflow useful
    # with the common per-sample CFIZZ exports while refusing to invent a
    # comparison from a lone file.
    if is_diff and not event_records:
        if kind == "boundaries":
            event_records = _ordinary_tad_diff_event_ranges(parsed_tables, chrom, resolution)
        elif kind == "compartment":
            event_records = _ordinary_compartment_diff_event_ranges(parsed_tables, chrom, resolution)
        elif kind == "loops":
            event_records = _ordinary_loop_diff_event_ranges(parsed_tables, chrom)
        if event_records:
            event_method = "derived_event_range"

    if event_records:
        # TAD/loop differential rows can span an entire chromosome.  Select
        # the densest event cluster, then union the official event ranges in
        # that cluster.  This is the one-window UI equivalent of the example
        # script's per-event plots.
        if kind == "compartment":
            # The official compartment workflow emits one plot per changed
            # block.  A single UI figure must choose one block rather than
            # unioning several already-expanded 9 Mb windows (which would
            # immediately turn into an arbitrary chromosome-wide range).
            current_center = (int(start) + int(end)) / 2
            selected_records = [
                min(
                    event_records,
                    key=lambda record: abs((record[0] + record[1]) / 2 - current_center),
                )
            ]
        else:
            width = 2_000_000 if kind in {"boundaries", "insulation"} else 5_000_000
            centers = [(left + right) // 2 for left, right, _ in event_records]
            selected = _densest_window(
                centers, width, start, end, resolution=resolution, max_end=max_end or None
            )
            selected_records = []
            if selected is not None:
                cluster_start, cluster_end = selected
                selected_records = [
                    record for record in event_records
                    if cluster_start <= (record[0] + record[1]) // 2 <= cluster_end
                ]
                # Avoid a sparse outlier stretching a dense event cluster.
                if selected_records:
                    event_start = min(record[0] for record in selected_records)
                    event_end = max(record[1] for record in selected_records)
                    if event_end - event_start > max(width * 2, 4 * resolution):
                        selected_records = []
        if selected_records:
            event_start = min(record[0] for record in selected_records)
            event_end = max(record[1] for record in selected_records)
            aligned_start, aligned_end = _align_viewport_range(
                event_start, event_end, resolution, chrom_end=max_end or None
            )
            return ViewportSuggestion(
                aligned_start, aligned_end,
                f"依据已选 {kind} 差异事件自动定位", event_method, tuple(parsed_paths),
            )

    if positions:
        if kind == "compartment":
            width = 10_000_000 if is_diff else 20_000_000
        elif kind in {"boundaries", "insulation"}:
            width = 2_000_000
        else:
            width = 5_000_000
        suggested = _densest_window(positions, width, start, end, resolution=resolution, max_end=max_end or None)
        if suggested is not None:
            reason = f"依据已选 {kind} 数据的特征密度自动定位"
            aligned_start, aligned_end = _align_viewport_range(
                suggested[0], suggested[1], resolution, chrom_end=max_end or None
            )
            return ViewportSuggestion(aligned_start, aligned_end, reason, "feature_density", tuple(parsed_paths))

    aligned_start, aligned_end = _align_viewport_range(start, end, resolution)
    return ViewportSuggestion(aligned_start, aligned_end, f"已选 {kind} 文件无可用坐标，保留当前范围", "fallback", tuple(parsed_paths))
