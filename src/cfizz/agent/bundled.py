"""Paths and loaders for the small resources shipped with CFIZZ Agent.

Keeping these paths inside the Python package makes the installed console
script independent of the current working directory.  Wheels are installed
unpacked, so the scientific libraries can consume the returned filesystem
paths directly.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Dict


RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
DEMO_ROOT = RESOURCE_ROOT / "demo"
DEMO_DATA_ROOT = DEMO_ROOT / "data"
DEMO_SPEC_PATH = DEMO_ROOT / "figure-spec.integrated-demo.json"
REFERENCE_ROOT = RESOURCE_ROOT / "references"

DEMO_FILENAMES = (
    "hiPSC_nor_chr17.mcool",
    "hiPSC_var_chr17.mcool",
    "FOXJ1.gtf",
    "concordant_enhancer.chr17_75.4-76.34M.bed",
    "hiPSC_nor_ATAC-Seq_chr17_mean.bw",
    "hiPSC_var_ATAC-Seq_chr17_mean.bw",
    "hiPSC_nor_CUTTag-H3K27ac_chr17_mean.bw",
    "hiPSC_var_CUTTag-H3K27ac_chr17_mean.bw",
    "hiPSC_nor_CUTTag-CTCF_chr17_mean.bw",
    "hiPSC_var_CUTTag-CTCF_chr17_mean.bw",
    "hiPSC_nor_chr17_mean.bw",
    "hiPSC_var_chr17_mean.bw",
)

HG38_REFERENCE_FILENAMES = (
    "Homo_sapiens.GRCh38.110.add_chr.sorted.gtf.gz",
    "Homo_sapiens.GRCh38.110.add_chr.sorted.gtf.gz.tbi",
    "gene_index.json.gz",
)


def bundled_reference_root(build: str = "hg38") -> Path:
    """Return the package directory containing one complete reference build."""
    return REFERENCE_ROOT / str(build)


def load_demo_spec() -> Dict[str, Any]:
    """Load the bundled FOXJ1 spec and resolve every input to package data."""
    with DEMO_SPEC_PATH.open("r", encoding="utf-8") as handle:
        spec = json.load(handle)
    spec = deepcopy(spec)
    for source in spec.get("data_sources", []):
        filename = Path(str(source.get("path", ""))).name
        candidate = DEMO_DATA_ROOT / filename
        if not filename or not candidate.is_file():
            raise FileNotFoundError(f"内置示例缺少数据文件：{filename or '<empty>'}")
        source["path"] = str(candidate.resolve())
    return spec


def resource_checks() -> list[tuple[str, Path]]:
    """Return all files that must exist in a complete Agent installation."""
    checks = [("FOXJ1 示例配置", DEMO_SPEC_PATH)]
    checks.extend((f"FOXJ1 示例/{name}", DEMO_DATA_ROOT / name) for name in DEMO_FILENAMES)
    checks.extend(
        (f"hg38 参考/{name}", bundled_reference_root("hg38") / name)
        for name in HG38_REFERENCE_FILENAMES
    )
    return checks
