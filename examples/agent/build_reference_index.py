#!/usr/bin/env python3
"""Build the compact lookup and Tabix files used by the Agent reference registry."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re

import pysam


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--sorted-gtf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    indexed = args.output_dir / "Homo_sapiens.GRCh38.110.add_chr.sorted.gtf.gz"
    pysam.tabix_compress(str(args.sorted_gtf), str(indexed), force=True)
    pysam.tabix_index(str(indexed), preset="gff", force=True)

    aliases: dict[str, dict[str, object]] = {}
    gene_records = 0
    with gzip.open(args.source, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 9 or fields[2] != "gene":
                continue
            attributes = dict(re.findall(r'(\w+)\s+"([^"]+)"', fields[8]))
            gene_id = attributes.get("gene_id")
            gene_name = attributes.get("gene_name") or gene_id
            if not gene_id or not gene_name:
                continue
            gene_records += 1
            record: dict[str, object] = {
                "gene": gene_name,
                "gene_id": gene_id,
                "chrom": fields[0],
                "start": int(fields[3]) - 1,
                "end": int(fields[4]),
            }
            aliases.setdefault(gene_name.upper(), record)
            aliases.setdefault(gene_id.upper(), record)

    payload = {
        "schema_version": 1,
        "assembly": "GRCh38.p14",
        "annotation": "Ensembl 110",
        "source_file": args.source.name,
        "indexed_file": indexed.name,
        "gene_records": gene_records,
        "aliases": aliases,
    }
    index_path = args.output_dir / "gene_index.json.gz"
    with gzip.open(index_path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"indexed_annotation={indexed}")
    print(f"tabix_index={indexed}.tbi")
    print(f"gene_index={index_path}")
    print(f"gene_records={gene_records}; aliases={len(aliases)}")


if __name__ == "__main__":
    main()
