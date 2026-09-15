"""Safe, read-only inspection of user-provided genomics files."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
import math
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence


_SUFFIX_TYPES = {
    ".cool": "cool",
    ".mcool": "mcool",
    ".bw": "bigwig",
    ".bigwig": "bigwig",
    ".gtf": "gtf",
    ".gff": "gff",
    ".gff3": "gff",
    ".bed": "bed",
    ".bedpe": "bedpe",
    ".tsv": "tsv",
}


@dataclass(frozen=True)
class InspectionResult:
    """Serializable metadata returned by :class:`DataInspector`."""

    path: str
    type: str
    exists: bool
    readable: bool
    size_bytes: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.exists and self.readable and self.error is None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["usable"] = self.usable
        return result


class DataInspector:
    """Inspect files without allowing access outside explicitly allowed roots."""

    def __init__(
        self,
        allowed_roots: Optional[Sequence[str]] = None,
        max_text_rows: int = 10_000,
        max_cached_files: int = 4_096,
    ):
        roots = allowed_roots or [str(Path.cwd())]
        self.allowed_roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self._roots_lock = threading.RLock()
        self.max_text_rows = max_text_rows
        self.max_cached_files = max(1, int(max_cached_files))
        # Dataset discovery can inspect the same large collection several
        # times while the user changes figure type, region, or file selection.
        # Cache metadata by immutable file identity so those UI operations do
        # not repeatedly sample up to 10,000 text rows or reopen HDF5 files.
        self._inspection_cache: OrderedDict[
            tuple[str, str, int, int], InspectionResult
        ] = OrderedDict()
        self._inspection_cache_lock = threading.RLock()

    def authorize_root(self, path: str) -> Path:
        """Authorize one user-selected directory for the current process."""
        candidate = self.platform_path(path).expanduser().resolve()
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError("授权路径必须是存在的文件夹。")
        with self._roots_lock:
            if candidate not in self.allowed_roots:
                self.allowed_roots = (*self.allowed_roots, candidate)
        return candidate

    def resolve_path(self, path: str) -> Path:
        candidate = self.platform_path(path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        resolved = candidate.resolve()
        with self._roots_lock:
            roots_snapshot = self.allowed_roots
        if not any(self._is_within(resolved, root) for root in roots_snapshot):
            roots = ", ".join(str(root) for root in roots_snapshot)
            raise PermissionError(f"数据文件不在已授权目录中。已授权目录：{roots}")
        return resolved

    @staticmethod
    def platform_path(path: str) -> Path:
        """Translate a Windows drive path when the service runs inside WSL."""
        raw = str(path).strip().strip('"').strip("'")
        match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
        if os.name != "nt" and match:
            drive = match.group(1).lower()
            relative = match.group(2).replace("\\", "/")
            return Path("/mnt") / drive / relative
        return Path(raw)

    def inspect(self, path: str, declared_type: Optional[str] = None) -> InspectionResult:
        try:
            resolved = self.resolve_path(path)
        except (OSError, PermissionError) as exc:
            return InspectionResult(path=path, type=declared_type or "unknown", exists=False, readable=False, error=str(exc))

        data_type = declared_type or self.detect_type(resolved)
        if not resolved.exists():
            return InspectionResult(
                path=str(resolved),
                type=data_type,
                exists=False,
                readable=False,
                error="找不到数据文件，请检查路径或重新选择文件。",
            )
        if not resolved.is_file():
            return InspectionResult(
                path=str(resolved),
                type=data_type,
                exists=True,
                readable=False,
                error="当前路径不是文件。",
            )

        try:
            stat = resolved.stat()
        except OSError as exc:
            return InspectionResult(
                path=str(resolved),
                type=data_type,
                exists=True,
                readable=False,
                error=f"文件无法读取：{exc}",
            )
        cache_key = (str(resolved), data_type, stat.st_mtime_ns, stat.st_size)
        with self._inspection_cache_lock:
            cached = self._inspection_cache.get(cache_key)
            if cached is not None:
                self._inspection_cache.move_to_end(cache_key)
                return cached

        try:
            if data_type in {"cool", "mcool"}:
                result = self._inspect_cooler(resolved, data_type)
            elif data_type == "bigwig":
                result = self._inspect_bigwig(resolved)
            elif data_type in {"gtf", "gff", "bed", "bedpe", "tsv", "insulation_tsv", "tad_tsv", "loop_tsv", "compartment_tsv"}:
                result = self._inspect_text(resolved, data_type)
            else:
                result = InspectionResult(
                    path=str(resolved),
                    type=data_type,
                    exists=True,
                    readable=True,
                    size_bytes=stat.st_size,
                    warnings=["暂时只能确认该文件存在，尚不能解析其内部元数据。"],
                )
        except (OSError, ValueError, RuntimeError) as exc:
            return InspectionResult(
                path=str(resolved),
                type=data_type,
                exists=True,
                readable=False,
                size_bytes=stat.st_size,
                error=f"文件无法解析：{exc}",
            )
        if result.usable:
            with self._inspection_cache_lock:
                self._inspection_cache[cache_key] = result
                self._inspection_cache.move_to_end(cache_key)
                while len(self._inspection_cache) > self.max_cached_files:
                    self._inspection_cache.popitem(last=False)
        return result

    def invalidate_cache(self, root: Optional[str] = None) -> None:
        """Forget cached metadata below *root*, or all metadata when omitted.

        File size and mtime already protect normal replacements.  Explicit
        invalidation is still used by the web app's Refresh and Upload actions
        so users always receive a fresh inspection when they ask for one.
        """
        with self._inspection_cache_lock:
            if root is None:
                self._inspection_cache.clear()
                return
            candidate = self.platform_path(str(root)).expanduser()
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            resolved = candidate.resolve()
            stale = [
                key for key in self._inspection_cache
                if self._is_within(Path(key[0]), resolved)
            ]
            for key in stale:
                self._inspection_cache.pop(key, None)

    def inspect_many(self, sources: Iterable[Dict[str, Any]]) -> Dict[str, InspectionResult]:
        return {
            source["id"]: self.inspect(source["path"], source.get("type"))
            for source in sources
            if "id" in source and "path" in source
        }

    @staticmethod
    def detect_type(path: Path) -> str:
        return _SUFFIX_TYPES.get(path.suffix.lower(), "unknown")

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _inspect_cooler(self, path: Path, data_type: str) -> InspectionResult:
        try:
            import cooler
        except ImportError:
            return InspectionResult(
                path=str(path),
                type=data_type,
                exists=True,
                readable=True,
                size_bytes=path.stat().st_size,
                warnings=["已识别为 Hi-C 文件；安装 cooler 后可读取染色体和分辨率。"],
                metadata={"inspection_level": "file_only"},
            )

        if data_type == "mcool":
            groups = cooler.fileops.list_coolers(str(path))
            resolutions = sorted(
                int(group.rsplit("/", 1)[-1])
                for group in groups
                if group.startswith("/resolutions/")
            )
            if not resolutions:
                raise ValueError("文件中没有发现 mcool resolution group")
            selected_resolution = resolutions[0]
            clr = cooler.Cooler(f"{path}::/resolutions/{selected_resolution}")
        else:
            clr = cooler.Cooler(str(path))
            resolutions = [int(clr.binsize)]
            selected_resolution = int(clr.binsize)

        chromsizes = {str(chrom): int(size) for chrom, size in clr.chromsizes.items()}
        bin_columns = list(clr.bins().columns)
        return InspectionResult(
            path=str(path),
            type=data_type,
            exists=True,
            readable=True,
            size_bytes=path.stat().st_size,
            metadata={
                "inspection_level": "full",
                "resolutions": resolutions,
                "selected_resolution": selected_resolution,
                "chromosomes": list(chromsizes),
                "chromsizes": chromsizes,
                "has_balance_weights": "weight" in bin_columns,
            },
        )

    def _inspect_bigwig(self, path: Path) -> InspectionResult:
        try:
            import pyBigWig
        except ImportError:
            return InspectionResult(
                path=str(path),
                type="bigwig",
                exists=True,
                readable=True,
                size_bytes=path.stat().st_size,
                warnings=["已识别为 BigWig；安装 pyBigWig 后可读取覆盖范围。"],
                metadata={"inspection_level": "file_only"},
            )

        handle = pyBigWig.open(str(path))
        try:
            chromsizes = {str(chrom): int(size) for chrom, size in handle.chroms().items()}
        finally:
            handle.close()
        return InspectionResult(
            path=str(path),
            type="bigwig",
            exists=True,
            readable=True,
            size_bytes=path.stat().st_size,
            metadata={
                "inspection_level": "full",
                "chromosomes": list(chromsizes),
                "chromsizes": chromsizes,
            },
        )

    def _inspect_text(self, path: Path, data_type: str) -> InspectionResult:
        chromosomes: List[str] = []
        seen_chromosomes = set()
        min_start: Optional[int] = None
        max_end: Optional[int] = None
        column_count = 0
        parsed_rows = 0
        truncated = False
        columns: List[str] = []
        coordinate_rows = 0
        bedpe_coordinate_rows = 0
        bin_widths: set[int] = set()
        previous_end: Dict[str, int] = {}
        comparable_rows = 0
        contiguous_rows = 0
        boundary_rows = 0
        boundary_observed_rows = 0

        def looks_like_header(fields: List[str]) -> bool:
            lowered = {value.strip().lower() for value in fields}
            first = fields[0].strip().lower() if fields else ""
            known = {
                "chrom", "chromosome", "chrom1", "seqname", "start", "end",
                "start1", "end1", "chrom2", "start2", "end2", "e1",
                "region", "is_bad_bin",
            }
            return first in {"chrom", "chromosome", "chrom1", "seqname"} or bool(lowered & known) and bool(
                lowered & {"start", "start1", "e1", "is_bad_bin"}
            )

        def integer(value: str) -> Optional[int]:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(number) or not number.is_integer():
                return None
            return int(number)

        def chromosome(value: str) -> bool:
            token = str(value).strip()
            return bool(
                re.fullmatch(r"(?i:chr(?:[0-9]{1,2}|x|y|m|mt))", token)
                or re.fullmatch(r"(?i:[0-9]{1,2}|x|y|m|mt)", token)
            )

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip() or line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                column_count = max(column_count, len(fields))
                if not columns and data_type not in {"gtf", "gff"} and looks_like_header(fields):
                    columns = [value.strip() for value in fields]
                    continue

                lowered_columns = [value.lower() for value in columns]
                column_lookup = {value: index for index, value in enumerate(lowered_columns)}
                if data_type in {"gtf", "gff"}:
                    chrom_index, start_index, end_index = 0, 3, 4
                elif {"chrom", "start", "end"}.issubset(column_lookup):
                    chrom_index = column_lookup["chrom"]
                    start_index = column_lookup["start"]
                    end_index = column_lookup["end"]
                elif {"chromosome", "start", "end"}.issubset(column_lookup):
                    chrom_index = column_lookup["chromosome"]
                    start_index = column_lookup["start"]
                    end_index = column_lookup["end"]
                elif {"chrom1", "start1", "end1"}.issubset(column_lookup):
                    chrom_index = column_lookup["chrom1"]
                    start_index = column_lookup["start1"]
                    end_index = column_lookup["end1"]
                else:
                    chrom_index, start_index, end_index = 0, 1, 2

                chrom = fields[chrom_index] if len(fields) > chrom_index else ""
                start = integer(fields[start_index]) if len(fields) > start_index else None
                end = integer(fields[end_index]) if len(fields) > end_index else None
                if chrom and start is not None and end is not None:
                    coordinate_rows += 1
                    if chrom not in seen_chromosomes:
                        seen_chromosomes.add(chrom)
                        chromosomes.append(chrom)
                    min_start = start if min_start is None else min(min_start, start)
                    max_end = end if max_end is None else max(max_end, end)
                    if end > start and len(bin_widths) < 64:
                        bin_widths.add(end - start)
                    if chrom in previous_end:
                        comparable_rows += 1
                        if previous_end[chrom] == start:
                            contiguous_rows += 1
                    previous_end[chrom] = end

                if {"chrom2", "start2", "end2"}.issubset(column_lookup):
                    second_values = (
                        fields[column_lookup["chrom2"]] if len(fields) > column_lookup["chrom2"] else "",
                        integer(fields[column_lookup["start2"]]) if len(fields) > column_lookup["start2"] else None,
                        integer(fields[column_lookup["end2"]]) if len(fields) > column_lookup["end2"] else None,
                    )
                    if second_values[0] and second_values[1] is not None and second_values[2] is not None:
                        bedpe_coordinate_rows += 1
                elif not columns and data_type not in {"gtf", "gff"} and len(fields) >= 6:
                    # CFIZZ's official Loop readers consume headerless BEDPE
                    # (the first six columns are the two anchors).  Detect it
                    # by structure so a renamed valid file still works, while
                    # a summary table whose filename merely contains "loop"
                    # is not offered as a renderer input.
                    second_start = integer(fields[4])
                    second_end = integer(fields[5])
                    if (
                        chromosome(fields[0])
                        and chromosome(fields[3])
                        and start is not None
                        and end is not None
                        and second_start is not None
                        and second_end is not None
                        and end >= start
                        and second_end >= second_start
                    ):
                        bedpe_coordinate_rows += 1

                boundary_indices = [
                    index for index, name in enumerate(lowered_columns)
                    if name.startswith("is_boundary")
                ]
                if boundary_indices:
                    boundary_observed_rows += 1
                    if any(
                        index < len(fields) and fields[index].strip().lower() in {"true", "1", "yes", "y"}
                        for index in boundary_indices
                    ):
                        boundary_rows += 1
                parsed_rows += 1
                if parsed_rows >= self.max_text_rows:
                    truncated = True
                    break

        warnings = []
        if parsed_rows == 0:
            warnings.append("文件中没有发现可解析的数据行。")
        if truncated:
            warnings.append(f"为保证响应速度，仅扫描了前 {self.max_text_rows} 行。")
        return InspectionResult(
            path=str(path),
            type=data_type,
            exists=True,
            readable=True,
            size_bytes=path.stat().st_size,
            metadata={
                "inspection_level": "sampled" if truncated else "full",
                "chromosomes": chromosomes,
                "min_start": min_start,
                "max_end": max_end,
                "column_count": column_count,
                "parsed_rows": parsed_rows,
                "columns": columns,
                "coordinate_rows": coordinate_rows,
                "bedpe_coordinate_rows": bedpe_coordinate_rows,
                "bin_widths": sorted(bin_widths),
                "contiguous_ratio": (contiguous_rows / comparable_rows) if comparable_rows else None,
                "boundary_row_fraction": (
                    boundary_rows / boundary_observed_rows if boundary_observed_rows else None
                ),
            },
            warnings=warnings,
        )
