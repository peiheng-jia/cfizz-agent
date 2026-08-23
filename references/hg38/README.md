# Human GRCh38 gene annotation

This directory is an optional, project-wide human gene annotation for the
CFIZZ Agent. It is intentionally excluded from the GitHub source package
because the compressed GTF and Tabix files are large. Keep a local copy here,
or point a checkout at an equivalent reference directory, when full gene-name
lookup and gene tracks are required. Experimental datasets should reference
this shared copy instead of carrying a duplicate GTF in every case directory.

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

The annotation file is large reference data and is intentionally not treated
as source code. Keep its release and assembly metadata with any deployment.
The published package still includes a FOXJ1 coordinate fallback for the demo;
all other gene symbols require a user-provided GTF/GFF or this optional
reference directory.
