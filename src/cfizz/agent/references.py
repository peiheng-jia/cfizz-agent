"""Reference assemblies and gene lookup used by the Agent data workflow.

Reference data is deliberately separated from experiment data.  A user can
bring a Hi-C/BigWig directory while the Agent supplies stable assembly
metadata and, when available, a bundled annotation file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import difflib
from pathlib import Path
import gzip
import json
import re
import tempfile
import threading
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class GeneLocation:
    gene: str
    assembly: str
    chrom: str
    start: int
    end: int
    source: str
    annotation_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReferenceBuild:
    id: str
    species: str
    assembly: str
    annotation: str
    chromosome_count: int
    bundled_annotation: Optional[str] = None
    bundled_genes: tuple[str, ...] = ()
    complete_annotation: Optional[str] = None
    annotation_release: Optional[str] = None
    gene_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["bundled_genes"] = list(result["bundled_genes"])
        result["available"] = self.bundled_annotation is not None
        result["complete"] = self.complete_annotation is not None
        return result


# These coordinates are an intentionally small safety net for the bundled
# demo gene.  A real GTF, when present, remains the authoritative source.
_BUILTIN_GENES = {
    "hg38": {
        "FOXJ1": ("chr17", 76_136_333, 76_141_245),
    },
}

_HG38_CHROMS = {
    "chr1": 248_956_422, "chr2": 242_193_529, "chr3": 198_295_559,
    "chr4": 190_214_555, "chr5": 181_538_259, "chr6": 170_805_979,
    "chr7": 159_345_973, "chr8": 145_138_636, "chr9": 138_394_717,
    "chr10": 133_797_422, "chr11": 135_086_622, "chr12": 133_275_309,
    "chr13": 114_364_328, "chr14": 107_043_718, "chr15": 101_991_189,
    "chr16": 90_338_345, "chr17": 83_257_441, "chr18": 80_373_285,
    "chr19": 58_617_616, "chr20": 64_444_167, "chr21": 46_709_983,
    "chr22": 50_818_468, "chrX": 156_040_895, "chrY": 57_227_415,
}


class ReferenceRegistry:
    """Describe built-in references without requiring a genome FASTA."""

    def __init__(self, project_root: str | Path, cache_root: Optional[str | Path] = None):
        self.project_root = Path(project_root).expanduser().resolve()
        self.cache_root = Path(cache_root or self.project_root / "agent_runtime" / "reference_cache").expanduser().resolve()
        self._gene_index: Optional[Dict[str, Any]] = None
        self._index_lock = threading.RLock()

    def catalog(self) -> list[Dict[str, Any]]:
        return [self._build("hg38").to_dict()]

    def bundled_gene_names(self, build: str = "hg38") -> list[str]:
        """Return genes for which this project ships a small GTF annotation."""
        if build != "hg38":
            raise ValueError("当前内置参考版本为 hg38/GRCh38。")
        names = set(_BUILTIN_GENES.get(build, {}))
        for directory in (self.project_root / "demo" / "data", self.project_root / "data"):
            if not directory.is_dir():
                continue
            names.update(path.stem.upper() for path in directory.glob("*.gtf") if path.is_file())
            names.update(path.stem.upper() for path in directory.glob("*.gff*") if path.is_file())
        return sorted(names)

    def _build(self, build: str) -> ReferenceBuild:
        if build != "hg38":
            raise ValueError("当前内置参考版本为 hg38/GRCh38。")
        annotation = self._bundled_annotation("FOXJ1")
        complete = self._complete_annotation()
        index = self._load_gene_index()
        return ReferenceBuild(
            id="hg38",
            species="human",
            assembly="GRCh38",
            annotation="项目级 Ensembl 110 / GRCh38.p14 完整基因注释",
            chromosome_count=len(_HG38_CHROMS),
            bundled_annotation=str(annotation) if annotation else None,
            bundled_genes=tuple(self.bundled_gene_names(build)),
            complete_annotation=str(complete) if complete else None,
            annotation_release=index.get("annotation") if index else None,
            gene_count=int(index.get("gene_records", 0)) if index else None,
        )

    def locate_gene(
        self,
        gene: str,
        build: str = "hg38",
        annotation_path: Optional[str | Path] = None,
    ) -> Optional[GeneLocation]:
        name = str(gene).strip().upper()
        if not name:
            return None
        if build not in _BUILTIN_GENES:
            raise ValueError("当前内置参考版本为 hg38/GRCh38。")

        candidates: list[Path] = []
        if annotation_path:
            candidates.append(Path(annotation_path).expanduser().resolve())
        bundled = self._bundled_annotation(name)
        if bundled:
            candidates.append(bundled)
        for path in candidates:
            location = self._parse_gene(path, name, build)
            if location:
                return location

        index = self._load_gene_index()
        record = index.get("aliases", {}).get(name) if index else None
        complete = self._complete_annotation()
        if record and complete:
            return GeneLocation(
                str(record.get("gene") or name),
                build,
                str(record["chrom"]),
                int(record["start"]),
                int(record["end"]),
                str(index.get("annotation") or "完整项目参考"),
                str(complete),
            )

        fallback = _BUILTIN_GENES[build].get(name)
        if fallback:
            chrom, start, end = fallback
            return GeneLocation(name, build, chrom, start, end, "内置坐标（请用完整 GTF 校验转录本）")
        return None

    def chromosome_sizes(self, build: str = "hg38") -> Dict[str, int]:
        if build != "hg38":
            raise ValueError("当前内置参考版本为 hg38/GRCh38。")
        return dict(_HG38_CHROMS)

    def has_complete_annotation(self, build: str = "hg38") -> bool:
        if build != "hg38":
            return False
        return self._complete_annotation() is not None and self._load_gene_index() is not None

    def complete_gene_count(self, build: str = "hg38") -> int:
        if build != "hg38":
            return 0
        index = self._load_gene_index()
        return int(index.get("gene_records", 0)) if index else 0

    def suggest_genes(self, query: str, build: str = "hg38", limit: int = 5) -> list[str]:
        """Suggest authoritative symbols for a mistyped gene without guessing coordinates."""
        if build != "hg38":
            return []
        wanted = str(query).strip().upper()
        if not wanted:
            return []
        index = self._load_gene_index() or {}
        names = sorted({
            str(record.get("gene") or "").upper()
            for record in index.get("aliases", {}).values()
            if isinstance(record, dict) and record.get("gene")
        } | set(self.bundled_gene_names(build)))
        # Transposed letters are common in short symbols (MCY -> MYC), while
        # generic fuzzy matching alone scores such three-letter names poorly.
        transpositions = []
        for position in range(len(wanted) - 1):
            variant = wanted[:position] + wanted[position + 1] + wanted[position] + wanted[position + 2:]
            if variant in names and variant not in transpositions:
                transpositions.append(variant)
        fuzzy = difflib.get_close_matches(wanted, names, n=limit, cutoff=0.65)
        return (transpositions + [name for name in fuzzy if name not in transpositions])[:limit]

    def annotation_track(self, location: GeneLocation, start: int, end: int) -> str:
        """Return a single-gene GTF matching CFIZZ integrated examples.

        The viewport may contain many neighbouring genes, but the CFIZZ 7_1 and
        7_3 single-gene examples pass an independent ``GENE.gtf`` track.  Keep
        that data contract here instead of changing the track renderer.
        """
        source = Path(location.annotation_path).resolve() if location.annotation_path else None
        if source is not None and source.stem.upper() == location.gene.upper() and source.suffix.lower() in {".gtf", ".gff", ".gff3"}:
            return str(source)
        full = self._complete_annotation()
        if full is None:
            if source is None:
                raise ValueError(f"{location.gene} 没有可用的注释文件。")
            return str(source)

        safe_gene = re.sub(r"[^A-Za-z0-9_.-]", "_", location.gene.upper())
        cache_path = self.cache_root / "hg38" / "genes" / f"{safe_gene}.gtf"
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return str(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import pysam
        except ImportError as exc:
            raise ValueError("完整参考注释需要 pysam 才能按区域提取。") from exc
        lines: list[str] = []
        with pysam.TabixFile(str(full)) as handle:
            try:
                candidates = handle.fetch(location.chrom, max(0, int(location.start)), int(location.end))
                wanted = location.gene.upper()
                for line in candidates:
                    fields = line.rstrip("\n").split("\t")
                    if len(fields) < 9:
                        continue
                    aliases = re.findall(r'(?:gene_name|gene_id)\s+["=]?([^"; ]+)', fields[8])
                    if wanted in {alias.upper() for alias in aliases}:
                        lines.append(line)
            except ValueError as exc:
                raise ValueError(f"完整参考注释中没有染色体 {location.chrom}。") from exc
        if not lines:
            raise ValueError(f"完整参考注释中没有找到 {location.gene} 的 GTF 记录。")
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=".region_", suffix=".gtf", dir=cache_path.parent)
        try:
            with open(file_descriptor, "w", encoding="utf-8", closefd=True) as handle:
                handle.write(f"# Ensembl 110 / GRCh38.p14 · {location.gene}\n")
                handle.write("\n".join(lines))
                handle.write("\n")
            Path(temporary_name).replace(cache_path)
        finally:
            temporary = Path(temporary_name)
            if temporary.exists():
                temporary.unlink()
        return str(cache_path)

    @staticmethod
    def annotation_gene_count(path: str) -> int:
        """Count distinct genes in a GTF/GFF track for CFIZZ height sizing."""
        annotation = Path(path)
        open_func = gzip.open if annotation.suffix.lower() == ".gz" else open
        genes = set()
        with open_func(annotation, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line or line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 9:
                    continue
                match = re.search(r'(?:gene_id|gene_name)\s+["=]?([^"; ]+)', fields[8])
                if match:
                    genes.add(match.group(1))
        return len(genes)

    def region_annotation_track(self, chrom: str, start: int, end: int, build: str = "hg38") -> str:
        """Extract every GTF record overlapping a viewport for CFIZZ's GTF track."""
        if build != "hg38":
            raise ValueError("当前内置参考版本为 hg38/GRCh38。")
        full = self._complete_annotation()
        if full is None:
            raise ValueError("项目中没有可用的完整 hg38 GTF，无法绘制区域内全部基因。")
        if start < 0 or end <= start:
            raise ValueError("基因注释区域无效。")
        safe_chrom = re.sub(r"[^A-Za-z0-9_.-]", "_", chrom)
        cache_path = self.cache_root / "hg38" / "regions" / f"{safe_chrom}_{start}_{end}.gtf"
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return str(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import pysam
        except ImportError as exc:
            raise ValueError("完整参考注释需要 pysam 才能按区域提取。") from exc
        try:
            with pysam.TabixFile(str(full)) as handle:
                lines = list(handle.fetch(chrom, start, end))
        except ValueError as exc:
            raise ValueError(f"完整参考注释中没有染色体 {chrom}。") from exc
        if not lines:
            raise ValueError(f"完整参考注释在 {chrom}:{start:,}-{end:,} 内没有基因记录。")
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=".region_", suffix=".gtf", dir=cache_path.parent)
        try:
            with open(file_descriptor, "w", encoding="utf-8", closefd=True) as handle:
                handle.write(f"# Ensembl 110 / GRCh38.p14 · {chrom}:{start}-{end}\n")
                handle.write("\n".join(lines))
                handle.write("\n")
            Path(temporary_name).replace(cache_path)
        finally:
            temporary = Path(temporary_name)
            if temporary.exists():
                temporary.unlink()
        return str(cache_path)

    def _complete_annotation(self) -> Optional[Path]:
        path = self.project_root / "references" / "hg38" / "Homo_sapiens.GRCh38.110.add_chr.sorted.gtf.gz"
        tabix = Path(str(path) + ".tbi")
        return path if path.is_file() and tabix.is_file() else None

    def _load_gene_index(self) -> Optional[Dict[str, Any]]:
        with self._index_lock:
            if self._gene_index is not None:
                return self._gene_index
            path = self.project_root / "references" / "hg38" / "gene_index.json.gz"
            if not path.is_file():
                return None
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    self._gene_index = json.load(handle)
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
            return self._gene_index

    def _bundled_annotation(self, gene: str) -> Optional[Path]:
        gene = gene.upper()
        paths = (
            self.project_root / "demo" / "data" / f"{gene}.gtf",
            self.project_root / "data" / f"{gene}.gtf",
        )
        exact = next((path for path in paths if path.is_file()), None)
        if exact:
            return exact
        # Permit lower-case filenames and GFF/GFF3 companions without making
        # gene lookup depend on a particular naming convention.
        for directory in (self.project_root / "demo" / "data", self.project_root / "data"):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*"), key=lambda item: item.name.lower()):
                if path.is_file() and path.suffix.lower() in {".gtf", ".gff", ".gff3"} and path.stem.upper() == gene:
                    return path
        return None

    @staticmethod
    def _parse_gene(path: Path, gene: str, build: str) -> Optional[GeneLocation]:
        suffixes = "".join(path.suffixes[-2:]).lower()
        if path.suffix.lower() not in {".gtf", ".gff", ".gff3"} and suffixes not in {".gtf.gz", ".gff.gz", ".gff3.gz"}:
            return None
        if not path.is_file():
            return None
        starts: list[int] = []
        ends: list[int] = []
        chromosome: Optional[str] = None
        try:
            opener = gzip.open if path.suffix.lower() == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip() or line.startswith("#"):
                        continue
                    fields = line.rstrip("\n").split("\t")
                    if len(fields) < 9:
                        continue
                    attributes = fields[8]
                    names = re.findall(r'(?:gene_name|gene_id)\s+["=]?([^"; ]+)', attributes)
                    if gene not in {item.upper() for item in names}:
                        continue
                    try:
                        start, end = int(fields[3]) - 1, int(fields[4])
                    except ValueError:
                        continue
                    chromosome = fields[0]
                    starts.append(start)
                    ends.append(end)
        except (OSError, UnicodeError):
            return None
        if not starts or chromosome is None:
            return None
        return GeneLocation(gene, build, chromosome, min(starts), max(ends), "GTF", str(path))


def normalize_chromosome(chrom: str, available: Iterable[str]) -> Optional[str]:
    """Match chr17 and 17 (and case variants) against a file's chromosomes."""
    values = list(available)
    if chrom in values:
        return chrom
    wanted = str(chrom).lower().removeprefix("chr")
    for value in values:
        if str(value).lower().removeprefix("chr") == wanted:
            return str(value)
    return None
