# CFIZZ Agent visualization coverage

The Agent exposes the complete public CFIZZ visualization catalogue. It does
not contain a fallback plotting implementation: every render request is
resolved to an allow-listed `cfizz.api.*` entrypoint.

## Direct selector

The selector contains figures that can be built from the current Hi-C source
and one discoverable companion result: triangular/square/OE Hi-C, regional
compartment, three TAD views, loop annotations, and loop APA.

## Conversation workflows

The AI planner selects a typed workflow ID and real FigureSpec source IDs. The
server validates sample roles and scientific options before dispatching CFIZZ:

- multi-sample Hi-C, compartment, TAD, and loop comparisons;
- E1 and compartment saddle plots;
- TAD boundary pileup and multi-sample loop APA;
- integrated and standalone BigWig/GTF/BED tracks;
- compartment, TAD, and loop differential classification, regional views,
  boundary pileup, and differential APA.

Missing inputs produce a data requirement, not generated Python. Directory
scanning registers `.cool/.mcool`, BigWig, GTF/GFF, BED/BEDPE, E1,
insulation, TAD-boundary, loop and O/E products as selectable data sources.

All rendered figures are exported from the same CFIZZ figure to SVG, PNG, and
PDF where the underlying workflow produces a figure.
