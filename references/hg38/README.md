# Human GRCh38 gene annotation

This top-level directory is an optional local override for the CFIZZ Agent.
Version 0.2.0 and later already ship a complete Ensembl 110 / GRCh38.p14
annotation under `src/cfizz/agent/resources/references/hg38/`, so a fresh
GitHub or wheel installation supports full gene-name lookup immediately.
Place a compatible replacement here only when a deployment needs a different
annotation release. Experimental datasets should use one shared reference
instead of carrying a duplicate GTF in every case directory.

Local annotation file:

- `Homo_sapiens.GRCh38.110.add_chr.gtf.gz`
- `Homo_sapiens.GRCh38.110.add_chr.sorted.gtf.gz` and `.tbi` are the
  coordinate-sorted, BGZF/Tabix-indexed runtime copy.
- `gene_index.json.gz` maps case-insensitive gene symbols and Ensembl gene
  IDs to coordinates for fast Agent lookup.
- Assembly: GRCh38.p14 (`GCA_000001405.29`)
- Annotation: Ensembl release 110
- Chromosome convention: `chr1`, `chr2`, ...
- Gene records: 62,754

Rebuild the runtime indexes after replacing the source annotation:

```bash
python examples/agent/build_reference_index.py \
  --source references/hg38/Homo_sapiens.GRCh38.110.add_chr.gtf.gz \
  --sorted-gtf /path/to/coordinate-sorted.gtf \
  --output-dir references/hg38
```

The packaged reference contains the coordinate-sorted BGZF GTF, its Tabix
index and the matching compact gene index. The unsorted source GTF is not
duplicated in the package. Keep release and assembly metadata with any custom
replacement.
