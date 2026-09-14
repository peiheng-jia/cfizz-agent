# Bundled CFIZZ Agent resources

This directory is installed with the Python package so a fresh installation
can be checked and demonstrated without relying on the source checkout.

- `demo/` contains a small chr17/FOXJ1 fixture used by the “载入 FOXJ1 示例”
  action and installation smoke tests. It is demonstration data, not a
  biological reference dataset for downstream conclusions.
- `references/hg38/` contains the Ensembl release 110 GRCh38 annotation,
  a Tabix index and the matching CFIZZ gene lookup index. The annotation is
  derived from Ensembl data and chromosome names use the `chr` prefix.

User experiment data, generated figures, sessions and caches are never stored
here. They remain in explicitly authorised data roots or the runtime folder.
