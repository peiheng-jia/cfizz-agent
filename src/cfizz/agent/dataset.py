"""Experiment-directory discovery and automatic multi-omics FigureSpec building."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional

from .figure_types import FIGURE_TYPE_BY_ID, READY_FIGURE_TYPE_IDS
from .companions import companion_resolution
from .inspection import DataInspector, InspectionResult
from .references import GeneLocation, ReferenceRegistry, normalize_chromosome


_SCAN_SUFFIXES = {".cool", ".mcool", ".bw", ".bigwig", ".gtf", ".gff", ".gff3", ".bed", ".bedpe", ".tsv", ".txt", ".npy"}
_ASSAY_ORDER = {"ATAC": 0, "H3K27ac": 1, "CTCF": 2, "RNA": 3, "signal": 4}


@dataclass(frozen=True)
class DatasetFile:
    path: str
    name: str
    type: str
    role: str
    assay: Optional[str]
    sample: Optional[str]
    group: Optional[str]
    label: str
    usable: bool
    warnings: List[str] = field(default_factory=list)
    # Hi-C files expose their native grids during the lightweight scan.  The
    # picker uses this per-file list to calculate the resolutions shared by
    # the currently checked samples instead of presenting a misleading union.
    resolutions: List[int] = field(default_factory=list)
    # Scientific roles are inferred from file contents first and filenames
    # second.  The UI may only offer roles in ``allowed_roles``; this prevents
    # an arbitrary relabel from turning an incompatible table into a late
    # renderer failure.
    auto_role: Optional[str] = None
    allowed_roles: List[str] = field(default_factory=list)
    role_confidence: float = 1.0
    role_evidence: List[str] = field(default_factory=list)
    auto_sample: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetScan:
    root: str
    files: List[DatasetFile]
    reference: Dict[str, Any]
    gene: Optional[Dict[str, Any]]
    chromosomes: List[str]
    resolutions: List[int]
    capabilities: List[Dict[str, Any]]
    missing: List[str]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "files": [item.to_dict() for item in self.files],
            "reference": self.reference,
            "gene": self.gene,
            "chromosomes": self.chromosomes,
            "resolutions": self.resolutions,
            "capabilities": self.capabilities,
            "missing": self.missing,
            "warnings": self.warnings,
            "summary": _summary(self),
        }


def select_dataset_files(scan: DatasetScan, selected_paths: Optional[Iterable[str]] = None) -> DatasetScan:
    """Return a scan restricted to the files explicitly selected by the user.

    Directory scans are deliberately non-destructive: the complete inventory is
    returned to the browser, while figure creation can pass a checked subset.
    Paths are compared after normalisation so Windows and POSIX spellings do not
    accidentally select different files.
    """
    if selected_paths is None:
        return scan
    def normalise(value: str) -> str:
        return str(value).replace("\\", "/").rstrip("/").casefold()

    wanted = {normalise(str(path)) for path in selected_paths if str(path).strip()}
    files = [item for item in scan.files if normalise(item.path) in wanted]
    if not files:
        raise ValueError("请至少选择一个可用的数据文件。")
    hic = [item for item in files if item.role == "hic" and item.usable]
    missing = [item for item in scan.missing if not (item.startswith("至少需要一个") and hic)]
    selected_resolutions = _common_file_resolutions(hic)
    if not hic or not all(item.resolutions for item in hic):
        # Keep compatibility with scans produced by older clients that did
        # not include per-file metadata. The validator will still perform the
        # authoritative check when a session is created.
        selected_resolutions = scan.resolutions
    return replace(
        scan,
        files=files,
        chromosomes=scan.chromosomes,
        resolutions=selected_resolutions,
        capabilities=_capabilities(files),
        missing=missing,
    )


def prepare_workflow_selection(
    scan: DatasetScan,
    figure_type: str,
    workflow_bindings: Optional[List[Dict[str, Any]]] = None,
    pairings_confirmed: bool = False,
) -> tuple[DatasetScan, List[Dict[str, Any]]]:
    """Validate workflow inputs and normalize explicit sample pairings.

    The catalogue is the source of truth.  Filename inference may suggest a
    pairing, but paired workflows are rendered only after the browser (or an
    API caller) confirms exactly which companion belongs to each Hi-C sample.
    """
    item = FIGURE_TYPE_BY_ID.get(str(figure_type))
    if item is None:
        raise ValueError("该 CFIZZ 图类型未登记。")
    contract = item.get("input_contract") or {}
    role_rules = contract.get("roles") or {}
    usable = [file for file in scan.files if file.usable]
    allowed_roles = {str(role) for role in contract.get("allowed_roles") or role_rules}
    incompatible = [file for file in usable if file.role not in allowed_roles]
    if incompatible:
        names = "、".join(file.name for file in incompatible[:5])
        suffix = "……" if len(incompatible) > 5 else ""
        raise ValueError(
            f"“{item['label']}”不能使用已选的这些文件：{names}{suffix}。"
            "请只选择该工作流输入面板中列出的兼容文件。"
        )
    # Role labels alone are not enough to make a FigureSpec valid.  In
    # particular, CFIZZ's ``intervals`` layer is a BED track, while TAD and
    # insulation results are TSV data used by their own analysis interfaces.
    # Keep this check here (before pairing and before FigureSpec construction)
    # so a bad relabel can never reach the renderer as ``intervals + tsv``.
    source_incompatible = [
        file for file in usable
        if not _source_type_supports_role(file.type, file.role)
    ]
    if source_incompatible:
        details = "、".join(
            f"{file.name}（{file.role}/{file.type}）"
            for file in source_incompatible[:5]
        )
        suffix = "……" if len(source_incompatible) > 5 else ""
        raise ValueError(
            f"已选文件的 CFIZZ 输入格式不兼容：{details}{suffix}。"
            "区间轨道必须是 BED 文件；请取消该 TSV，或上传/转换为 BED 后再选择。"
        )
    by_role: Dict[str, List[DatasetFile]] = {}
    for file in usable:
        by_role.setdefault(file.role, []).append(file)

    for role, rule in role_rules.items():
        count = len(by_role.get(role, []))
        minimum = int(rule.get("min") or 0)
        maximum = rule.get("max")
        maximum = int(maximum) if maximum is not None else None
        label = str(rule.get("label") or role)
        per_anchor = bool(rule.get("per_anchor"))
        if per_anchor:
            anchor_role = str((contract.get("pairing") or {}).get("anchor_role") or "hic")
            minimum = minimum * len(by_role.get(anchor_role, []))
            maximum = maximum * len(by_role.get(anchor_role, [])) if maximum is not None else None
        if count < minimum:
            raise ValueError(f"“{item['label']}”需要 {minimum} 个{label}文件；当前选择了 {count} 个。")
        if maximum is not None and count > maximum:
            raise ValueError(f"“{item['label']}”最多允许 {maximum} 个{label}文件；当前选择了 {count} 个。")

    for choice in contract.get("any_of") or []:
        roles = [str(value) for value in choice.get("roles") or []]
        minimum = int(choice.get("min") or 1)
        count = sum(len(by_role.get(role, [])) for role in roles)
        if count < minimum:
            labels = "、".join(str((role_rules.get(role) or {}).get("label") or role) for role in roles)
            raise ValueError(f"“{item['label']}”还需要从 {labels} 中至少选择 {minimum} 个文件。")

    pairing = contract.get("pairing") or {}
    if not pairing:
        return scan, []
    anchor_role = str(pairing.get("anchor_role") or "hic")
    companion_roles = [str(value) for value in pairing.get("companion_roles") or []]
    anchors = by_role.get(anchor_role, [])
    if not anchors:
        raise ValueError(f"“{item['label']}”没有选中可配对的 Hi-C 文件。")

    normalized: list[Dict[str, Any]] = []
    provided = workflow_bindings or []
    if provided:
        binding_by_anchor = {
            _normalise_path_key(str(binding.get("anchor_path") or "")): binding
            for binding in provided
            if str(binding.get("anchor_path") or "").strip()
        }
        selected_by_path = {_normalise_path_key(file.path): file for file in usable}
        used_companions: set[str] = set()
        for anchor in anchors:
            binding = binding_by_anchor.get(_normalise_path_key(anchor.path))
            if not binding:
                raise ValueError(f"尚未确认 {anchor.name} 的样本配对。")
            sample = str(binding.get("sample") or anchor.sample or Path(anchor.path).stem).strip()
            companions: Dict[str, str] = {}
            raw_companions = binding.get("companions") or {}
            for role in companion_roles:
                companion_path = str(raw_companions.get(role) or "").strip()
                candidate = selected_by_path.get(_normalise_path_key(companion_path))
                if candidate is None:
                    raise ValueError(f"{anchor.name} 尚未选择对应的 {role} 文件。")
                if candidate.role != role:
                    raise ValueError(
                        f"{candidate.name} 的识别角色是 {candidate.role}，不能作为 {anchor.name} 的 {role} 输入。"
                    )
                key = _normalise_path_key(candidate.path)
                if key in used_companions:
                    raise ValueError(f"{candidate.name} 已配给另一个样本，不能重复使用。")
                used_companions.add(key)
                companions[role] = candidate.path
            normalized.append({
                "sample": sample,
                "anchor_role": anchor_role,
                "anchor_path": anchor.path,
                "companions": companions,
            })
        if pairing.get("requires_confirmation") and not pairings_confirmed:
            raise ValueError("请先确认 Hi-C 与配套分析结果的样本对应关系。")
    else:
        suggestions = _suggest_workflow_bindings(anchors, by_role, companion_roles)
        details = "；".join(
            f"{Path(binding['anchor_path']).name} → "
            + "，".join(Path(value).name for value in binding["companions"].values())
            for binding in suggestions
        )
        if pairing.get("requires_confirmation"):
            suffix = f"建议配对：{details}。" if details else ""
            raise ValueError(f"该工作流需要先确认每个 Hi-C 样本对应的分析结果。{suffix}")
        normalized = suggestions

    sample_by_path: Dict[str, str] = {}
    for binding in normalized:
        sample_by_path[_normalise_path_key(binding["anchor_path"])] = binding["sample"]
        for companion_path in binding["companions"].values():
            sample_by_path[_normalise_path_key(companion_path)] = binding["sample"]
    rebound_files = [
        replace(file, sample=sample_by_path.get(_normalise_path_key(file.path), file.sample))
        for file in scan.files
    ]
    return replace(scan, files=rebound_files, capabilities=_capabilities(rebound_files)), normalized


def _suggest_workflow_bindings(
    anchors: List[DatasetFile],
    by_role: Dict[str, List[DatasetFile]],
    companion_roles: List[str],
) -> List[Dict[str, Any]]:
    suggestions: list[Dict[str, Any]] = []
    used: set[str] = set()
    for anchor in anchors:
        sample = str(anchor.sample or Path(anchor.path).stem)
        sample_key = re.sub(r"[^a-z0-9]+", "", sample.casefold())
        companions: Dict[str, str] = {}
        for role in companion_roles:
            scored: list[tuple[int, DatasetFile]] = []
            for candidate in by_role.get(role, []):
                key = _normalise_path_key(candidate.path)
                if key in used:
                    continue
                candidate_sample = str(candidate.sample or Path(candidate.path).stem)
                candidate_key = re.sub(r"[^a-z0-9]+", "", candidate_sample.casefold())
                score = 3 if sample_key and candidate_key == sample_key else 2 if sample_key and sample_key in candidate_key else 1 if candidate_key and candidate_key in sample_key else 0
                scored.append((score, candidate))
            scored.sort(key=lambda value: (-value[0], value[1].name.casefold()))
            if not scored or scored[0][0] == 0 or len(scored) > 1 and scored[0][0] == scored[1][0]:
                continue
            candidate = scored[0][1]
            used.add(_normalise_path_key(candidate.path))
            companions[role] = candidate.path
        if len(companions) == len(companion_roles):
            suggestions.append({
                "sample": sample,
                "anchor_role": "hic",
                "anchor_path": anchor.path,
                "companions": companions,
            })
    return suggestions


def scan_dataset(
    root: str,
    inspector: DataInspector,
    references: ReferenceRegistry,
    gene: Optional[str] = None,
    build: str = "hg38",
    max_files: int = 500,
    file_overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> DatasetScan:
    """Scan an authorized directory without reading raw signal matrices."""
    resolved = inspector.resolve_path(root)
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("数据包路径必须是已授权的文件夹。")

    candidates = sorted(
        (path for path in resolved.rglob("*") if path.is_file() and path.suffix.lower() in _SCAN_SUFFIXES),
        key=lambda item: str(item).lower(),
    )
    # A demo/data convenience directory can contain symlinks to files that
    # also live below the scanned experiment root.  Treating both spellings as
    # independent samples makes an explicit two-file choice turn into four
    # FigureSpec sources.  Keep the first stable display path, but de-duplicate
    # by the canonical target before applying the scan limit.
    unique_candidates: list[Path] = []
    seen_targets: set[str] = set()
    for candidate in candidates:
        try:
            target = str(candidate.resolve()).replace("\\", "/").casefold()
        except OSError:
            target = str(candidate.absolute()).replace("\\", "/").casefold()
        if target in seen_targets:
            continue
        seen_targets.add(target)
        unique_candidates.append(candidate)
    candidates = unique_candidates
    warnings: list[str] = []
    if len(candidates) > max_files:
        warnings.append(f"目录文件较多，仅扫描前 {max_files} 个支持的文件。")
        candidates = candidates[:max_files]

    files: list[DatasetFile] = []
    inspections: list[InspectionResult] = []
    for path in candidates:
        detected = _detect_dataset_type(path)
        inspection = inspector.inspect(str(path), detected)
        inspections.append(inspection)
        classified = _classify_file(path, detected, inspection)
        files.append(_apply_file_override(classified, file_overrides or {}))

    hic = [item for item in files if item.role == "hic" and item.usable]
    chromosomes = _common_chromosomes(inspections, hic)
    reference_files = [item for item in files if item.role == "gene_annotation" and item.usable]
    annotation_path = _select_annotation(reference_files, gene)
    location = _resolve_query(gene, references, build, annotation_path, chromosomes) if gene else None
    reference = references._build(build).to_dict()
    if annotation_path:
        reference["user_annotation"] = annotation_path
    elif location and location.annotation_path:
        reference["bundled_annotation"] = location.annotation_path

    resolutions = _resolutions(inspections, hic)
    capabilities = _capabilities(files)
    missing: list[str] = []
    if not hic:
        missing.append("至少需要一个 .cool 或 .mcool Hi-C 文件")
    if gene and location is None and not _parse_region_query(gene):
        missing.append(f"未在当前 GTF 或内置参考中定位基因 {gene}")
    if len(hic) >= 1 and not reference_files:
        if references.has_complete_annotation(build):
            warnings.append(
                f"当前实验目录未发现 GTF；将使用项目公共参考 Ensembl 110 / GRCh38.p14，"
                f"可按名称定位和标注其中 {references.complete_gene_count(build):,} 条人类基因记录。"
            )
        else:
            bundled_genes = references.bundled_gene_names(build)
            if bundled_genes:
                warnings.append(
                    f"当前实验目录未发现 GTF；项目公共参考可直接标注 {len(bundled_genes)} 个示例基因："
                    f"{'、'.join(bundled_genes)}。如需标注任意人类基因，请配置完整的 {build} GTF/GFF。"
                )
            else:
                warnings.append(f"当前实验目录和项目公共参考均未发现基因注释；请提供匹配版本的 {build} GTF/GFF。")
    if not any(item.role == "signal" for item in files):
        warnings.append("未发现 BigWig 信号轨道；仍可绘制 Hi-C 和基因注释图。")

    return DatasetScan(
        root=str(resolved),
        files=files,
        reference=reference,
        gene=location.to_dict() if location else None,
        chromosomes=chromosomes,
        resolutions=resolutions,
        capabilities=capabilities,
        missing=missing,
        warnings=warnings,
    )


def build_integrated_spec(
    scan: DatasetScan,
    session_id: str,
    references: ReferenceRegistry,
    gene: Optional[str] = None,
    build: str = "hg38",
    window: int = 500_000,
    resolution: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a FOXJ1-like integrated FigureSpec from a scan result."""
    hic_files = [item for item in scan.files if item.role == "hic" and item.usable]
    if not hic_files:
        raise ValueError("数据包中没有可用的 .cool 或 .mcool 文件。")
    if not scan.resolutions:
        if len(hic_files) > 1:
            raise ValueError("当前选中的 Hi-C 样本没有共同分辨率，无法进行同尺度绘图；请减少样本或准备包含相同分辨率的文件。")
        raise ValueError("无法读取 Hi-C 分辨率，请确认已安装 cooler。")
    if resolution is not None:
        try:
            resolution = int(resolution)
        except (TypeError, ValueError) as exc:
            raise ValueError("分辨率必须是正整数。") from exc
        if resolution <= 0 or resolution not in scan.resolutions:
            choices = "、".join(f"{value:,} bp" for value in scan.resolutions)
            raise ValueError(f"所选 {resolution:,} bp 不是当前 Hi-C 样本共有的分辨率；可选：{choices}。")

    available_chromosomes = _hic_chromosomes(scan, hic_files[0])
    location = None
    if gene:
        annotation = _select_annotation(
            [item for item in scan.files if item.role == "gene_annotation" and item.usable],
            gene,
        )
        location = _resolve_query(gene, references, build, annotation, available_chromosomes)
        if location is None:
            raise ValueError(f"无法识别“{gene}”。请输入基因名，或范围如 chr1:1-2Mb。")
    chrom = location.chrom if location else (scan.chromosomes[0] if scan.chromosomes else "chr1")
    chrom = normalize_chromosome(chrom, available_chromosomes) or chrom
    chrom_size = _chrom_size(scan, chrom, hic_files[0])
    if location:
        if location.source == "用户指定范围":
            start, end = location.start, location.end
        else:
            start = max(0, location.start - window)
            end = min(chrom_size or location.end + window, location.end + window)
    else:
        start, end = 0, min(chrom_size or 2_000_000, 2_000_000)
    if end <= start:
        raise ValueError("无法从数据包确定有效绘图区域。")

    sources: list[Dict[str, Any]] = []
    hic_layers: list[Dict[str, Any]] = []
    for index, item in enumerate(_sort_hic(hic_files)):
        source_id = f"hic_{index + 1}"
        sources.append({"id": source_id, "type": item.type, "path": item.path, "sample": item.sample, "label": item.label})
        hic_layers.append({
            "id": f"{source_id}_layer", "kind": "hic", "source_id": source_id,
            "label": item.label, "visible": True,
            "style": {"cmap": "Reds", "triangle_ratio": 1, "flip_vertical": index % 2 == 1},
        })

    # Keep computed CFIZZ products in the FigureSpec even when they do not
    # need a visible layer yet.  Conversation workflows can then select the
    # real control/treatment E1, insulation, boundary, loop and O/E sources
    # instead of guessing paths from prose.
    auxiliary_roles = {"compartment", "insulation", "boundaries", "loops", "oe"}
    auxiliary_counts: Dict[str, int] = {}
    for item in scan.files:
        if not item.usable or item.role not in auxiliary_roles:
            continue
        auxiliary_counts[item.role] = auxiliary_counts.get(item.role, 0) + 1
        index = auxiliary_counts[item.role]
        sources.append({
            "id": f"{item.role}_{index}", "type": item.type, "path": item.path,
            "sample": item.sample, "label": item.label, "role": item.role,
        })

    annotation_layers: list[Dict[str, Any]] = []
    user_gtf_path = _select_annotation(
        [item for item in scan.files if item.role == "gene_annotation" and item.usable],
        gene,
    )
    user_gtf = next((item for item in scan.files if item.path == user_gtf_path), None)
    if user_gtf:
        sources.append({"id": "gene_annotation", "type": user_gtf.type, "path": user_gtf.path, "label": "Genes"})
        annotation_layers.append({
            "id": "genes_layer", "kind": "genes", "source_id": "gene_annotation", "label": "Genes", "visible": True,
            "height_cm": 0.5, "style": {"color": "#666666", "show_title": False, "labels": True, "fontsize": 5, "gtf_style": "flybase", "color_utr": "blue", "border_color": "black", "color_backbone": "black", "line_width": 1.0},
        })
    elif location and location.annotation_path:
        track_path = references.annotation_track(location, start, end)
        sources.append({"id": "gene_annotation", "type": "gtf", "path": track_path, "label": "Genes (项目参考)"})
        annotation_layers.append({
            "id": "genes_layer", "kind": "genes", "source_id": "gene_annotation", "label": "Genes", "visible": True,
            "height_cm": 0.5, "style": {"color": "#666666", "show_title": False, "labels": True, "fontsize": 5, "gtf_style": "flybase", "color_utr": "blue", "border_color": "black", "color_backbone": "black", "line_width": 1.0},
        })

    interval_layers: list[Dict[str, Any]] = []
    for index, item in enumerate(item for item in scan.files if item.role == "intervals" and item.usable):
        if not _source_type_supports_role(item.type, item.role):
            raise ValueError(
                f"{item.name} 不能作为 CFIZZ 区间轨道（当前类型为 {item.type}）；"
                "区间图层只接受 BED 文件。"
            )
        source_id = f"intervals_{index + 1}"
        sources.append({"id": source_id, "type": item.type, "path": item.path, "label": item.label})
        interval_layers.append({
            "id": f"{source_id}_layer", "kind": "intervals", "source_id": source_id,
            "label": item.label, "visible": True, "height_cm": 0.7,
            "style": {"color": "purple", "alpha": 0.8, "labels": False, "fontsize": 5},
        })

    signal_layers: list[Dict[str, Any]] = []
    for index, item in enumerate(_sort_signals(scan.files)):
        source_id = f"signal_{index + 1}"
        sources.append({"id": source_id, "type": item.type, "path": item.path, "sample": item.sample, "label": item.label})
        signal_layers.append({
            "id": f"{source_id}_layer", "kind": "bigwig", "source_id": source_id,
            "label": item.label, "visible": True, "height_cm": 0.75,
            "style": {"color": _signal_color(item), "assay_group": item.assay or "signal", "y_scale_group": f"{item.assay or 'signal'}_shared"},
        })

    panels: list[Dict[str, Any]] = [{"id": "hic_panel", "kind": "hic_heatmap", "label": "Hi-C", "height_cm": "auto", "layers": hic_layers}]
    if annotation_layers or interval_layers:
        panels.append({"id": "annotation_panel", "kind": "signal_tracks", "label": "Annotations", "height_cm": 1.4, "layers": annotation_layers + interval_layers})
    if signal_layers:
        panels.append({"id": "signal_panel", "kind": "signal_tracks", "label": "Multi-omics signals", "height_cm": max(2.0, len(signal_layers) * 0.75), "layers": signal_layers})

    gene_label = gene.upper() if gene else "region"
    has_enrichment = bool(annotation_layers or interval_layers or signal_layers)
    if has_enrichment:
        title = f"{gene_label} multi-omics comparison" if len(hic_files) > 1 else f"{gene_label} multi-omics view"
        intent_summary = "由 Agent 根据用户数据目录自动识别并生成的多组学区域图。"
    else:
        title = f"{gene_label} Hi-C comparison" if len(hic_files) > 1 else f"{gene_label} Hi-C view"
        intent_summary = "由 Agent 根据用户数据目录自动识别并生成的 Hi-C 区域图。"
    return {
        "schema_version": "0.1", "figure_type": "hic_triangle", "figure_id": f"{session_id}_figure",
        "title": title, "intent_summary": intent_summary,
        "data_sources": sources,
        "viewport": {"chrom": chrom, "start": int(start), "end": int(end), "coordinate_system": "0-based-half-open", "focus_label": gene_label if gene else None},
        "analysis": {"resolution": resolution or _choose_resolution(scan.resolutions, end - start), "balance": True, "normalization": "raw", "shared_color_scale": True},
        "panels": panels,
        "layout": {"width_cm": 8, "gap_cm": 0.1, "left_margin_cm": 1, "right_margin_cm": 2, "font_size": 5},
        "export": {"formats": ["svg", "png", "pdf"], "dpi": 300, "output_basename": f"{session_id}_figure"},
        "metadata": {
            "created_by": "cfizz-agent-dataset-discovery",
            "reference": scan.reference,
            "root": scan.root,
            "data_profile": "multi-omics" if has_enrichment else "hic-only",
            "dataset_inventory": _role_counts(scan.files),
            "hic_resolution_sets": {
                item.name: list(item.resolutions) for item in hic_files if item.resolutions
            },
            "available_capabilities": [item["id"] for item in scan.capabilities if item.get("ready")],
        },
    }


def build_workflow_spec(
    scan: DatasetScan,
    session_id: str,
    figure_type: str,
    references: ReferenceRegistry,
    gene: Optional[str] = None,
    build: str = "hg38",
    window: int = 500_000,
    resolution: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a FigureSpec for a workflow whose official API does not need Hi-C.

    ``build_integrated_spec`` intentionally requires a matrix because it is
    the constructor for the Hi-C canvas.  Difference analyses and standalone
    track renderers are different CFIZZ entry points: their inputs can be
    boundary/E1/loop/track files only.  Keeping this constructor separate
    makes that distinction explicit and, importantly, gives the UI and
    adapter the same source set instead of creating a temporary Hi-C spec and
    failing later with the misleading "missing .cool/.mcool" message.
    """
    item = FIGURE_TYPE_BY_ID.get(str(figure_type))
    if item is None or not item.get("ready"):
        raise ValueError("该 CFIZZ 工作流未登记或当前不可执行。")
    files = [file for file in scan.files if file.usable]
    if not files:
        raise ValueError("当前工作流没有选中的可用数据文件。")

    role_to_kind = {
        "signal": "bigwig",
        "gene_annotation": "genes",
        "intervals": "intervals",
        "boundaries": "tad_boundaries",
        "insulation": "tad_boundaries",
        "loops": "loops",
        "compartment": "compartment",
    }
    layers: list[Dict[str, Any]] = []
    sources: list[Dict[str, Any]] = []
    role_counts: Dict[str, int] = {}
    for file in files:
        role_counts[file.role] = role_counts.get(file.role, 0) + 1
        index = role_counts[file.role]
        source_id = f"{file.role}_{index}"
        # A scan can contain a legacy ``tsv`` type.  The role validator has
        # already established that it is a valid analysis input; preserve its
        # original type for the official adapter rather than relabelling it as
        # an unrelated BED/interval source.
        source = {
            "id": source_id,
            "type": file.type,
            "path": file.path,
            "sample": file.sample,
            "label": file.label,
            "role": file.role,
        }
        sources.append(source)
        kind = role_to_kind.get(file.role)
        if kind is None:
            continue
        style: Dict[str, Any] = {"labels": False}
        if kind == "bigwig":
            style.update({"color": _signal_color(file), "assay_group": file.assay or "signal"})
        elif kind == "genes":
            style.update({"color": "#666666", "labels": True, "fontsize": 5})
        elif kind == "intervals":
            style.update({"color": "purple", "alpha": 0.8})
        elif kind == "tad_boundaries":
            style.update({"color": "#2E86AB", "alpha": 0.9})
        elif kind == "loops":
            style.update({"color": "#2E86AB", "alpha": 0.9})
        elif kind == "compartment":
            style.update({"cmap": "RdBu_r"})
        layers.append({
            "id": f"{source_id}_layer",
            "kind": kind,
            "source_id": source_id,
            "label": file.label,
            "visible": True,
            "height_cm": 0.75 if kind in {"bigwig", "genes", "intervals"} else None,
            "style": style,
        })

    if not sources:
        raise ValueError("当前工作流没有选中的可用数据文件。")
    # ``None`` is omitted rather than sent to the validator as a non-positive
    # layer height.  It keeps analysis-only layers compact while retaining a
    # concrete height for track layers.
    for layer in layers:
        if layer.get("height_cm") is None:
            layer.pop("height_cm", None)

    # Companion filenames encode the grid used by CFIZZ's analysis products
    # (for example ``10000.10b.boundaries.tsv``).  Use that metadata when the
    # caller did not explicitly choose a resolution; standalone track files
    # fall back to a safe positive value accepted by the workflow adapter.
    if resolution is not None:
        try:
            selected_resolution = int(resolution)
        except (TypeError, ValueError) as exc:
            raise ValueError("分辨率必须是正整数。") from exc
        if selected_resolution <= 0:
            raise ValueError("分辨率必须是正整数。")
    else:
        encoded = [companion_resolution(file.path) for file in files]
        encoded = [int(value) for value in encoded if value]
        selected_resolution = min(encoded) if encoded else 10_000

    # The no-matrix workflows do not have a cooler object from which to read
    # chromosome sizes.  A user region/gene is still authoritative; absent
    # that, retain the same small, predictable starter region as the Hi-C
    # canvas and let the user refine it in the conversation.
    location = None
    if gene:
        annotation = _select_annotation(
            [file for file in files if file.role == "gene_annotation"],
            gene,
        )
        location = _resolve_query(gene, references, build, annotation, scan.chromosomes or ["chr1"])
        if location is None:
            raise ValueError(f"无法识别“{gene}”。请输入基因名，或范围如 chr1:1-2Mb。")
    if location:
        chrom = location.chrom
        if location.source == "用户指定范围":
            start, end = location.start, location.end
        else:
            chrom, start, end = location.chrom, max(0, location.start - window), location.end + window
    else:
        chrom = scan.chromosomes[0] if scan.chromosomes else "chr1"
        start, end = 0, 2_000_000

    item_label = str(item.get("label") or figure_type)
    panel_label = item_label
    return {
        "schema_version": "0.1",
        "figure_type": str(figure_type),
        "figure_id": f"{session_id}_figure",
        "title": item_label,
        "intent_summary": f"使用所选数据生成 {item_label}。",
        "data_sources": sources,
        "viewport": {
            "chrom": chrom,
            "start": int(start),
            "end": int(end),
            "coordinate_system": "0-based-half-open",
            "focus_label": gene or None,
        },
        "analysis": {
            "resolution": int(selected_resolution),
            "balance": True,
            "normalization": "raw",
            "shared_color_scale": True,
        },
        "panels": [{
            "id": "workflow_panel",
            "kind": "signal_tracks",
            "label": panel_label,
            "height_cm": max(1.5, len(layers) * 0.75),
            "layers": layers,
        }],
        "layout": {
            "width_cm": 12,
            "gap_cm": 0.15,
            "left_margin_cm": 1.5,
            "right_margin_cm": 2,
            "font_size": 5,
        },
        "export": {
            "formats": ["svg", "png", "pdf"],
            "dpi": 300,
            "output_basename": f"{session_id}_figure",
        },
        "metadata": {
            "created_by": "cfizz-agent-workflow-selection",
            "root": scan.root,
            "workflow_source_ids": [source["id"] for source in sources],
            "dataset_inventory": _role_counts(scan.files),
            "viewport_selection": "explicit_user_region" if gene else "workflow_default",
        },
    }


def build_track_patch(
    scan: DatasetScan,
    spec: Dict[str, Any],
    inspector: DataInspector,
    roles: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Add compatible files from a scan to an existing CFIZZ integrated figure.

    Unlike :func:`build_integrated_spec`, this operation deliberately does not
    require another Hi-C file: the current figure already supplies the Hi-C
    layer.  It only describes data sources/tracks; rendering remains entirely
    in ``quick_plot_integrated``.
    """
    wanted = roles or {"signal", "intervals"}
    viewport = spec.get("viewport", {})
    chrom = str(viewport.get("chrom") or "")
    existing_paths = {
        str(Path(str(source.get("path"))).expanduser().resolve())
        for source in spec.get("data_sources", [])
        if source.get("path")
    }
    all_ids = {spec.get("figure_id")}
    all_ids.update(source.get("id") for source in spec.get("data_sources", []))
    for panel in spec.get("panels", []):
        all_ids.add(panel.get("id"))
        all_ids.update(layer.get("id") for layer in panel.get("layers", []))

    candidates = []
    skipped_incompatible = []
    for item in scan.files:
        if not item.usable or item.role not in wanted:
            continue
        if not _source_type_supports_role(item.type, item.role):
            skipped_incompatible.append(f"{item.name}（区间轨道需要 BED）")
            continue
        resolved_path = str(Path(item.path).resolve())
        if resolved_path in existing_paths:
            continue
        inspection = inspector.inspect(item.path, item.type)
        chromosomes = inspection.metadata.get("chromosomes", [])
        if chromosomes and normalize_chromosome(chrom, chromosomes) is None:
            skipped_incompatible.append(item.name)
            continue
        candidates.append(item)

    operations: list[Dict[str, Any]] = []
    signal_panel = next((panel for panel in spec.get("panels", []) if panel.get("id") == "signal_panel"), None)
    interval_panel = next((panel for panel in spec.get("panels", []) if panel.get("id") == "annotation_panel"), None)
    new_signal_layers = []
    new_interval_layers = []

    def unique_id(prefix: str) -> str:
        index = 1
        while f"{prefix}_{index}" in all_ids:
            index += 1
        value = f"{prefix}_{index}"
        all_ids.add(value)
        return value

    for item in _sort_signals(candidates) if "signal" in wanted else []:
        if item.role != "signal":
            continue
        source_id = unique_id("signal_source")
        layer_id = unique_id("signal_layer")
        operations.append({"op": "add_source", "source": {
            "id": source_id, "type": item.type, "path": item.path,
            "sample": item.sample, "label": item.label,
        }})
        new_signal_layers.append({
            "id": layer_id, "kind": "bigwig", "source_id": source_id,
            "label": item.label, "visible": True, "height_cm": 0.75,
            "style": {"color": _signal_color(item), "assay_group": item.assay or "signal", "y_scale_group": f"{item.assay or 'signal'}_shared"},
        })

    for item in (item for item in candidates if item.role == "intervals"):
        source_id = unique_id("interval_source")
        layer_id = unique_id("interval_layer")
        operations.append({"op": "add_source", "source": {
            "id": source_id, "type": item.type, "path": item.path, "label": item.label,
        }})
        new_interval_layers.append({
            "id": layer_id, "kind": "intervals", "source_id": source_id,
            "label": item.label, "visible": True, "height_cm": 0.7,
            "style": {"color": "purple", "alpha": 0.8, "labels": False, "fontsize": 5},
        })

    if new_signal_layers:
        if signal_panel is None:
            operations.append({"op": "add_panel", "panel": {
                "id": unique_id("signal_panel"), "kind": "signal_tracks",
                "label": "Multi-omics signals", "height_cm": max(2.0, len(new_signal_layers) * 0.75),
                "layers": new_signal_layers,
            }})
        else:
            operations.extend({"op": "add_layer", "panel_id": signal_panel["id"], "layer": layer} for layer in new_signal_layers)
            current_count = len(signal_panel.get("layers", [])) + len(new_signal_layers)
            operations.append({"op": "update", "target_kind": "panel", "target_id": signal_panel["id"], "field": "height_cm", "value": max(2.0, current_count * 0.75)})
    if new_interval_layers:
        if interval_panel is None:
            operations.append({"op": "add_panel", "panel": {
                "id": unique_id("annotation_panel"), "kind": "signal_tracks",
                "label": "Annotations", "height_cm": max(1.0, len(new_interval_layers) * 0.7),
                "layers": new_interval_layers,
            }})
        else:
            operations.extend({"op": "add_layer", "panel_id": interval_panel["id"], "layer": layer} for layer in new_interval_layers)

    added = len(new_signal_layers) + len(new_interval_layers)
    if not added:
        suffix = f"；另有 {len(skipped_incompatible)} 个文件与当前染色体 {chrom} 不兼容" if skipped_incompatible else ""
        # Re-selecting files that are already part of the current figure is a
        # harmless no-op, not a render failure.  The UI can use this metadata
        # to report that nothing changed while keeping the session usable.
        return {
            "summary": f"当前图已包含所选轨道{suffix}。",
            "operations": [],
            "added": {"bigwig": 0, "bed": 0},
            "skipped_incompatible": skipped_incompatible,
            "already_present": True,
        }
    return {
        "summary": f"从数据目录添加 {added} 条 CFIZZ 轨道",
        "operations": operations,
        "added": {"bigwig": len(new_signal_layers), "bed": len(new_interval_layers)},
        "skipped_incompatible": skipped_incompatible,
    }


def _detect_dataset_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".bedpe"}:
        return "bedpe"
    if suffix in {".tsv", ".txt"}:
        name = path.name.lower()
        if "insulation" in name:
            return "insulation_tsv"
        if "boundar" in name or re.search(r"\.\d+b\.tsv$", name):
            return "tad_tsv"
        if "e1" in name or "eigenvector" in name or "compartment" in name:
            return "compartment_tsv"
        if "loop" in name:
            return "loop_tsv"
        return "tsv"
    if suffix == ".npy":
        return "oe_npy" if "oe" in path.name.lower() else "npy"
    return {".cool": "cool", ".mcool": "mcool", ".bw": "bigwig", ".bigwig": "bigwig", ".gtf": "gtf", ".gff": "gff", ".gff3": "gff", ".bed": "bed"}.get(suffix, "unknown")


def _classify_file(path: Path, data_type: str, inspection: InspectionResult) -> DatasetFile:
    name = path.name
    lowered = name.lower()
    evidence: list[str] = []
    allowed_roles: list[str] = []
    confidence = 1.0
    if data_type in {"cool", "mcool"}:
        role, assay = "hic", "Hi-C"
        allowed_roles = ["hic"]
        evidence.append("Cooler 容器结构")
    elif data_type in {"gtf", "gff"}:
        role, assay = "gene_annotation", "gene"
        allowed_roles = ["gene_annotation"]
        evidence.append("GTF/GFF 注释结构")
    elif data_type == "bigwig":
        role, assay = "signal", _assay(lowered)
        allowed_roles = ["signal"]
        evidence.append("BigWig 二进制信号格式")
    elif data_type in {"bed", "bedpe", "tsv", "loop_tsv", "compartment_tsv", "insulation_tsv", "tad_tsv"}:
        role, allowed_roles, confidence, evidence = _infer_text_role(data_type, inspection, lowered)
        assay = {
            "loops": "loops", "intervals": "intervals", "compartment": "E1",
            "insulation": "insulation", "boundaries": "TAD",
        }.get(role)
        data_type = _canonical_type_for_role(data_type, role)
    elif data_type == "oe_npy":
        role, assay = "oe", "O/E"
        allowed_roles = ["oe"]
        evidence.append("O/E NPY 文件名与数组格式")
    else:
        role, assay = "unknown", None
        allowed_roles = []
        confidence = 0.0
        evidence.append("未识别到兼容的 CFIZZ 数据结构")
    sample, group = _sample_and_group(path.stem, assay)
    label = _label(sample, group, assay, path.stem)
    resolutions = []
    if role == "hic":
        resolutions = sorted({int(value) for value in inspection.metadata.get("resolutions", []) if str(value).isdigit()})
    return DatasetFile(
        str(path.resolve()), name, data_type, role, assay, sample, group, label,
        inspection.usable, list(inspection.warnings), resolutions,
        role, allowed_roles, confidence, evidence, sample,
    )


def _infer_text_role(
    data_type: str,
    inspection: InspectionResult,
    lowered_name: str,
) -> tuple[str, list[str], float, list[str]]:
    """Infer a CFIZZ scientific role from sampled table structure.

    Insulation result tables and boundary-only tables can share their header.
    The full table is normally a contiguous genomic grid with relatively few
    boundary-positive rows, whereas a boundary export is sparse and consists
    mostly of called boundaries.  We therefore use both structure and name as
    evidence instead of treating the filename as the schema.
    """
    metadata = inspection.metadata or {}
    columns = {str(value).strip().lower() for value in metadata.get("columns", [])}
    coordinate_rows = int(metadata.get("coordinate_rows") or 0)
    bedpe_rows = int(metadata.get("bedpe_coordinate_rows") or 0)
    contiguous_ratio = metadata.get("contiguous_ratio")
    boundary_fraction = metadata.get("boundary_row_fraction")
    evidence: list[str] = []

    if bedpe_rows > 0 and not columns:
        evidence.append("检测到两端染色体坐标列（BEDPE/Loop）")
        return "loops", ["loops"], 0.99, evidence

    if {"chrom1", "start1", "end1", "chrom2", "start2", "end2"}.issubset(columns):
        evidence.append("检测到带表头的两端坐标结果；CFIZZ Loop 绘图接口要求无表头原始 BEDPE")
        return "unknown", [], 0.92, evidence

    if "e1" in columns or "eigenvector" in columns or data_type == "compartment_tsv" and not columns:
        evidence.append("检测到 E1/eigenvector 列")
        return "compartment", ["compartment"], 0.98 if columns else 0.72, evidence

    insulation_columns = any(name.startswith("log2_insulation_score") for name in columns)
    boundary_columns = any(name.startswith("is_boundary") for name in columns)
    boundary_named = "boundar" in lowered_name or bool(re.search(r"\.\d+b\.tsv$", lowered_name))
    insulation_named = "insulation" in lowered_name
    canonical_intervals = coordinate_rows > 0 and {"chrom", "start", "end"}.issubset(columns)
    boundary_context_columns = {
        "chrom", "start", "end", "dist_to_upstream", "dist_to_downstream", "adaptive_threshold",
    }.issubset(columns)
    if boundary_context_columns:
        evidence.append("检测到 CFIZZ TAD boundary context 坐标与距离列")
        return "boundaries", ["boundaries"], 0.98, evidence

    if canonical_intervals and boundary_named and not (insulation_columns or boundary_columns):
        evidence.append("检测到边界命名的单区间坐标表")
        # A boundary TSV is an analysis result, not a generic BED interval
        # track.  It must not be relabelled as ``intervals`` because the
        # official integrated renderer only accepts ``type=bed`` there.
        return "boundaries", ["boundaries"], 0.84, evidence

    if insulation_columns or boundary_columns:
        if insulation_columns:
            evidence.append("检测到 insulation score 列")
        if boundary_columns:
            evidence.append("检测到 boundary 标记列")
        if contiguous_ratio is not None:
            evidence.append(f"基因组区间连续率 {float(contiguous_ratio):.0%}")
        if boundary_fraction is not None:
            evidence.append(f"boundary 阳性行比例 {float(boundary_fraction):.0%}")

        # Boundary-only exports are usually sparse and every retained row is a
        # positive call.  That structure is authoritative even when users have
        # renamed the file to something uninformative.  The filename remains a
        # secondary hint for short/partially sampled tables, never a required
        # condition for recognising a boundary result.
        boundary_like = bool(boundary_columns) and (
            boundary_fraction is not None and float(boundary_fraction) >= 0.8
            or boundary_named and (
                boundary_fraction is not None and float(boundary_fraction) >= 0.5
                or contiguous_ratio is not None and float(contiguous_ratio) < 0.5
            )
        )
        insulation_like = (
            insulation_named
            or (contiguous_ratio is not None and float(contiguous_ratio) >= 0.8)
            or (boundary_fraction is not None and float(boundary_fraction) < 0.5)
        )
        if boundary_like:
            return "boundaries", ["boundaries"], 0.96, evidence
        if insulation_like:
            # A complete insulation table that contains is_boundary columns is
            # also a structurally valid source of boundary calls.  A sparse
            # boundary-only export is never offered as an insulation grid.
            allowed = ["insulation", "boundaries"] if boundary_columns else ["insulation"]
            return "insulation", allowed, 0.97 if insulation_named or contiguous_ratio else 0.84, evidence
        role = "boundaries" if boundary_named else "insulation"
        evidence.append("内容采样不足，使用文件名作为次级证据")
        return role, [role], 0.68, evidence

    if data_type == "loop_tsv" or "loop" in lowered_name:
        evidence.append("文件名含 Loop，但内容不是 CFIZZ 可直接读取的无表头 BEDPE")
        return "unknown", [], 0.22, evidence

    if coordinate_rows > 0 and not columns and (data_type == "tad_tsv" or boundary_named):
        evidence.append("检测到无表头的边界区间坐标")
        return "boundaries", ["boundaries"], 0.82, evidence

    if data_type == "bed":
        evidence.append("检测到单区间 BED 坐标结构")
        return "intervals", ["intervals"], 0.94, evidence

    if canonical_intervals:
        evidence.append("检测到 TSV/TXT 单区间坐标，但 CFIZZ intervals 图层只接受 BED")
        return "unknown", [], 0.9, evidence

    evidence.append("表格存在，但未检测到已登记的 CFIZZ 输入列")
    return "unknown", [], 0.2, evidence


def _canonical_type_for_role(data_type: str, role: str) -> str:
    return {
        "compartment": "compartment_tsv",
        "insulation": "insulation_tsv",
        "boundaries": "tad_tsv",
        "loops": "bedpe" if data_type == "bedpe" else "loop_tsv",
        "intervals": "bed" if data_type == "bed" else "tsv",
    }.get(role, data_type)


# These are the source types accepted by the corresponding FigureSpec roles.
# Keeping the rule next to dataset classification lets the picker, workflow
# validator and track-appending path enforce the same contract.  The broader
# roles intentionally include the legacy TSV variants used by CFIZZ analysis
# APIs; ``intervals`` is deliberately BED-only.
_ROLE_SOURCE_TYPES = {
    "hic": {"cool", "mcool"},
    "signal": {"bigwig"},
    "gene_annotation": {"gtf", "gff"},
    "intervals": {"bed"},
    "loops": {"bedpe", "loop_tsv", "tsv"},
    "compartment": {"compartment_tsv", "tsv"},
    "insulation": {"insulation_tsv", "tsv"},
    "boundaries": {"tad_tsv", "tsv"},
    "oe": {"oe_npy"},
}


def _source_type_supports_role(source_type: str, role: str) -> bool:
    allowed = _ROLE_SOURCE_TYPES.get(str(role))
    return not allowed or str(source_type) in allowed


def _normalise_path_key(value: str) -> str:
    return str(value).replace("\\", "/").rstrip("/").casefold()


def _apply_file_override(item: DatasetFile, overrides: Dict[str, Dict[str, str]]) -> DatasetFile:
    if not overrides:
        return item
    override = next(
        (
            value for key, value in overrides.items()
            if _normalise_path_key(key) in {_normalise_path_key(item.path), _normalise_path_key(item.name)}
        ),
        None,
    )
    if not override:
        return item
    requested_role = str(override.get("role") or item.role).strip()
    if requested_role != item.role and requested_role not in item.allowed_roles:
        allowed = "、".join(item.allowed_roles) or "无"
        raise ValueError(f"{item.name} 不能标记为 {requested_role}；根据文件结构只允许：{allowed}。")
    canonical_type = _canonical_type_for_role(item.type, requested_role)
    if not _source_type_supports_role(canonical_type, requested_role):
        if requested_role == "intervals":
            raise ValueError(f"{item.name} 不能标记为 intervals；CFIZZ 区间轨道只接受 BED 文件。")
        raise ValueError(f"{item.name} 不能作为 {requested_role} 输入（当前数据类型为 {canonical_type}）。")
    requested_sample = str(override.get("sample") or item.sample or "").strip()
    requested_label = str(override.get("label") or item.label or item.name).strip()
    if len(requested_sample) > 120 or len(requested_label) > 160:
        raise ValueError(f"{item.name} 的样本名或显示名过长。")
    assay = {
        "hic": "Hi-C", "signal": item.assay or "signal", "gene_annotation": "gene",
        "intervals": "intervals", "loops": "loops", "compartment": "E1",
        "insulation": "insulation", "boundaries": "TAD", "oe": "O/E",
    }.get(requested_role, item.assay)
    return replace(
        item,
        role=requested_role,
        type=canonical_type,
        assay=assay,
        sample=requested_sample or None,
        label=requested_label or item.name,
    )


def _assay(name: str) -> str:
    if "atac" in name:
        return "ATAC"
    if "h3k27ac" in name or "h3k27" in name or "h3k" in name:
        return "H3K"
    if "ctcf" in name:
        return "CTCF"
    if "rna" in name or "rnaseq" in name or ("chr" in name and "mean" in name):
        return "RNA"
    return "signal"


def _sample_and_group(stem: str, assay: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    lowered = stem.lower()
    has_normal = any(token in lowered for token in ("normal", "_nor", "-nor", "control", "_ctrl"))
    has_variant = any(token in lowered for token in ("variant", "_var", "-var", "case", "treated", "treatment"))
    if has_normal and has_variant:
        group = "comparison"
    elif has_normal:
        group = "normal"
    elif has_variant:
        group = "variant"
    else:
        group = None
    # CFIZZ analysis products commonly use names such as
    # ``1_0.51_5_1000.10000.insulation`` and
    # ``2_0.51_5_1000.10000.10b.boundaries``.  Recover the originating sample
    # before removing generic assay tokens so companion matching is based on a
    # canonical identity rather than substring luck.
    numeric_sample = re.search(r"(?<!\d)(\d+_\d+_\d+)(?!\d)", stem)
    if numeric_sample:
        sample = numeric_sample.group(1)
    else:
        # Some downstream CFIZZ tables omit the library/resolution suffix and
        # start directly with a two-token sample id (for example
        # ``51_5_boundary_with_context.tsv``).  Preserve that id so it can be
        # suggested as the companion of ``51_5_1000.mcool``.
        short_numeric_sample = re.match(
            r"^(\d+_\d+)(?=_(?:boundary|boundaries|insulation|loop|eigenvector|e1)(?:_|$))",
            stem,
            flags=re.IGNORECASE,
        )
        if short_numeric_sample:
            sample = short_numeric_sample.group(1)
        else:
            sample = re.sub(r"^\d+_\d+[._-]", "", stem)
        sample = re.sub(
            r"(?i)(?:[._-]\d+(?:kb|bp)?)*(?:[._-](?:\d+b))?[._-](?:insulation|boundaries|boundary|eigenvector|e1)$",
            "",
            sample,
        )
    for token in ("ATAC-Seq", "ATAC", "CUTTag-H3K27ac", "CUTTag-CTCF", "H3K27ac", "CTCF", "RNA-Seq", "RNA", "chr17_mean", "_mean"):
        sample = re.sub(re.escape(token), "", sample, flags=re.IGNORECASE)
    sample = re.sub(r"[_\-.]+$", "", sample).strip(" _-." )
    return (sample or None), group


def _label(sample: Optional[str], group: Optional[str], assay: Optional[str], stem: str) -> str:
    if assay in {"ATAC", "H3K27ac", "CTCF", "RNA"}:
        sample_label = "normal" if group == "normal" else "variant" if group == "variant" else (sample or "sample")
        return f"{sample_label} {assay}"
    if assay == "Hi-C":
        return "normal Hi-C" if group == "normal" else "variant Hi-C" if group == "variant" else stem
    return stem


def _common_chromosomes(inspections: Iterable[InspectionResult], hic: list[DatasetFile]) -> list[str]:
    values: list[str] = []
    hic_paths = {item.path for item in hic}
    for inspection in inspections:
        if inspection.path not in hic_paths:
            continue
        for chrom in inspection.metadata.get("chromosomes", []):
            if chrom not in values:
                values.append(chrom)
    return values


def _resolutions(inspections: Iterable[InspectionResult], hic: list[DatasetFile]) -> list[int]:
    # DatasetFile carries the metadata discovered for that exact file.  A
    # multi-sample figure can only use the intersection; a union would defer
    # an avoidable failure until the renderer opens the second matrix.
    if hic and all(item.resolutions for item in hic):
        return _common_file_resolutions(hic)
    # Defensive fallback for callers constructing DatasetFile objects from an
    # older payload without the new field.
    paths = {item.path for item in hic}
    values: list[set[int]] = []
    for inspection in inspections:
        if inspection.path in paths:
            values.append({int(value) for value in inspection.metadata.get("resolutions", []) if str(value).isdigit()})
    if not values or any(not item for item in values):
        return []
    return sorted(set.intersection(*values))


def _common_file_resolutions(files: list[DatasetFile]) -> list[int]:
    sets = [set(item.resolutions) for item in files]
    if not sets or any(not values for values in sets):
        return []
    return sorted(set.intersection(*sets))


def _capabilities(files: list[DatasetFile]) -> list[Dict[str, Any]]:
    roles = {item.role for item in files if item.usable}
    capabilities = []
    if "hic" in roles:
        capabilities.extend([
            {"id": "hic_triangle", "label": "Hi-C 三角热图", "ready": True},
            {"id": "hic_square", "label": "Hi-C 方形热图", "ready": True},
            {"id": "hic_oe", "label": "O/E 热图", "ready": True},
            {"id": "tad_insulation", "label": "TAD / Insulation", "ready": True},
        ])
    if "hic" in roles and "compartment" in roles:
        capabilities.append({"id": "compartment", "label": "A/B Compartment", "ready": True})
    if "hic" in roles and "loops" in roles:
        capabilities.extend([
            {"id": "loop_heatmap", "label": "Loop 标注热图", "ready": True},
            {"id": "loop_apa", "label": "Loop APA", "ready": True},
        ])
    if "hic" in roles and ({"signal", "gene_annotation", "intervals"} & roles):
        capabilities.append({"id": "integrated", "label": "多组学整合图", "ready": True, "figure_type": "hic_triangle"})
    if "insulation" in roles:
        capabilities.extend([
            {"id": "tad_insulation_track", "label": "Insulation score 轨道", "ready": True},
            {"id": "tad_multi", "label": "多样本 TAD 对比", "ready": "hic" in roles},
        ])
    if "boundaries" in roles and "hic" in roles:
        capabilities.append({"id": "tad_boundary_pileup", "label": "TAD Boundary Pileup", "ready": True})
    if "compartment" in roles:
        capabilities.append({"id": "compartment_eigenvector", "label": "E1 特征向量轨道", "ready": True})
    if "compartment" in roles and "oe" in roles and "hic" in roles:
        capabilities.append({"id": "compartment_multi", "label": "多样本 Compartment", "ready": True})
    if "loops" in roles and "hic" in roles:
        capabilities.extend([
            {"id": "loop_multi", "label": "多样本 Loop 区域对比", "ready": True},
            {"id": "loop_apa_multi", "label": "多样本 Loop APA", "ready": True},
        ])
    return capabilities


def _summary(scan: DatasetScan) -> str:
    counts = _role_counts(scan.files)
    parts = [f"发现 {count} 个{label}" for label, count in (("Hi-C", counts.get("hic", 0)), ("BigWig 信号", counts.get("signal", 0)), ("基因注释", counts.get("gene_annotation", 0)), ("区间/Loop", counts.get("intervals", 0) + counts.get("loops", 0)) ) if count]
    if scan.gene:
        parts.append(f"已定位 {scan.gene['gene']}（{scan.gene['chrom']}:{scan.gene['start']:,}-{scan.gene['end']:,}）")
    return "；".join(parts) if parts else "没有发现可用的实验数据文件。"


def _role_counts(files: Iterable[DatasetFile]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in files:
        if item.usable:
            counts[item.role] = counts.get(item.role, 0) + 1
    return counts


def _sort_hic(items: list[DatasetFile]) -> list[DatasetFile]:
    return sorted(items, key=lambda item: (0 if item.group == "normal" else 1 if item.group == "variant" else 2, item.name.lower()))


def _sort_signals(items: list[DatasetFile]) -> list[DatasetFile]:
    return sorted((item for item in items if item.role == "signal" and item.usable), key=lambda item: (_ASSAY_ORDER.get(item.assay or "signal", 9), 0 if item.group == "normal" else 1, item.name.lower()))


def _signal_color(item: DatasetFile) -> str:
    if item.group == "variant":
        return "#C0392B"
    if item.group == "normal":
        return "#666666"
    if item.group == "comparison":
        return "#7B4FA3"
    return "#2E7D5B"


def _choose_resolution(values: list[int], width: int) -> int:
    target = max(1, width // 300)
    return min(values, key=lambda value: (abs(value - target), value))


def _hic_chromosomes(scan: DatasetScan, item: DatasetFile) -> list[str]:
    # The scan stores the union; normal chromosome aliases are enough for the
    # generated spec and are checked again by the FigureSpec validator.
    return scan.chromosomes


def _chrom_size(scan: DatasetScan, chrom: str, item: DatasetFile) -> Optional[int]:
    # The scan's chromosome list is intentionally light-weight. Use the
    # reference fallback for the bundled human demo when no size is present.
    return None


def _select_annotation(items: list[DatasetFile], gene: Optional[str]) -> Optional[str]:
    if not items:
        return None
    if gene:
        wanted = gene.strip().lower()
        exact = next((item for item in items if Path(item.name).stem.lower() == wanted), None)
        if exact:
            return exact.path
    return items[0].path


def _parse_region_query(value: Optional[str]) -> Optional[tuple[str, int, int]]:
    """Parse a user genomic range, accepting ``chr1:1-2Mb`` and ``chr1``."""
    text = str(value or "").strip().replace("，", ",")
    if not text:
        return None
    match = re.fullmatch(
        r"(?i)(chr[0-9xywm]+|[0-9xywm]+)\s*(?::\s*([\d,.]+)\s*([kmg]?)(?:bp|b)?\s*[-–—到]\s*([\d,.]+)\s*([kmg]?)(?:bp|b)?)?",
        text,
    )
    if not match:
        return None
    chrom = match.group(1)
    if not chrom.lower().startswith("chr"):
        chrom = f"chr{chrom}"
    if match.group(2) is None:
        return chrom, 0, 2_000_000
    start = _scaled_query_number(match.group(2), match.group(3))
    end = _scaled_query_number(match.group(4), match.group(5))
    if end <= start:
        return None
    return chrom, start, end


def _scaled_query_number(raw: str, suffix: str) -> int:
    value = float(str(raw).replace(",", ""))
    factor = {"k": 1_000, "m": 1_000_000, "g": 1_000_000_000}.get(str(suffix or "").lower(), 1)
    return int(value * factor)


def _resolve_query(
    value: Optional[str],
    references: ReferenceRegistry,
    build: str,
    annotation_path: Optional[str],
    available_chromosomes: Iterable[str],
) -> Optional[GeneLocation]:
    region = _parse_region_query(value)
    if region:
        raw_chrom, start, end = region
        chrom = normalize_chromosome(raw_chrom, available_chromosomes)
        if chrom is None:
            return None
        return GeneLocation(str(value).strip(), build, chrom, start, end, "用户指定范围")
    return references.locate_gene(value or "", build, annotation_path) if value else None
