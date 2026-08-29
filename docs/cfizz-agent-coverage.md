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

## Visualization parameter registry

Visualization controls have one authoritative registry in
`cfizz.agent.parameters`. The planner catalogue, render-adapter allow-list and
HTTP API all read the same definitions, including applicability, target,
type, range, default value and whether a change affects scientific results.

- `GET /api/visualization-parameters?figure_type=compartment_diff_scatter`
  returns the parameter templates for a figure type.
- `GET /api/sessions/{session_id}/parameters` returns concrete current targets
  together with `current_value`, `default_value`, `is_overridden` and
  `is_modified` for comparison.
- `POST /api/sessions/{session_id}/parameters` applies one typed, allow-listed
  edit. Scientific changes return HTTP 409 until
  `confirm_scientific_change: true` is supplied.

For example, changing a differential-compartment category color without
rendering immediately:

```json
{
  "target_kind": "figure",
  "parameter": "workflow_options.a_to_b_color",
  "value": "#E69F00",
  "render": false
}
```

The registry covers all ready figure types and includes heatmap palettes and
scales, plot dimensions, differential-category palettes, point/bar styling,
track rendering controls, typography, visibility and PNG DPI where the
official CFIZZ entrypoint supports them. Unsupported renderer options are not
advertised.
