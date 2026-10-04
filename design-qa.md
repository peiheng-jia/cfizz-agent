# Design QA — workspace file selection

- Source visual truth path: conversation attachment supplied by the user (current persistent right-pane file browser, 894 × 1819 px); shell baseline at `agent_runtime/context-preview.png`.
- Implementation screenshot paths: `agent_runtime/context-selection-independent-preview.png`, `agent_runtime/context-folder-navigation-preview.png`, and `agent_runtime/context-pane-compact-png-preview.png`.
- Browser viewport: 1920 × 1080 CSS px, device scale factor 1.
- Implementation capture: 1894 × 980 px (Chrome content viewport).
- State: one imported source with 95 files; 30 recognized Hi-C/E1/Loop/Insulation/TAD files added to the analysis scope; visualization availability recalculated from that selection.
- Density normalization: both references were reviewed at their native browser density; fidelity was evaluated as a requested interaction redesign, not a pixel-identical clone.

## Full-view comparison evidence

The source keeps the complete 95-file inventory permanently in the narrow right pane, pushing the visualization tree below it and making selection difficult. The implementation keeps the workspace visible but moves the inventory into a centered 1040 px modal with a persistent header, toolbar, location bar, navigation rail, scrollable detail table, and footer actions. The right pane now reserves its file section for committed selections only.

## Focused-region comparison evidence

The file-selection region was inspected at full resolution. File name, type, sample and availability have stable columns; the vertical scrollbar remains visible with all 95 files; unsupported files are visually quieter; selected rows have both checkbox and tinted-row feedback. Search, category switching, mixed-role selection, cancel and apply states were exercised. Selection is now independent of the default figure, and graph-specific cardinality is applied only after a graph is chosen.

## Required fidelity surfaces

- Fonts and typography: native application stack retained; modal controls were increased to 10–12 px with stronger file-name weight and readable hierarchy. No blocking truncation was observed.
- Spacing and layout rhythm: fixed header/footer and 180 px navigation rail keep actions stable; 56 px file rows are dense without overlapping.
- Colors and visual tokens: existing CFIZZ red, warm neutral borders and green availability state are reused consistently.
- Image quality and asset fidelity: no new raster imagery or replacement brand assets were introduced; the existing CFIZZ mark remains unchanged.
- Copy and content: labels distinguish imported files, currently selectable files, unrecognized files, draft selection and committed selection.

## Findings

No actionable P0, P1 or P2 issue remains in the tested desktop state.

## Comparison history

### Iteration 1

- Earlier P1: the complete inventory occupied the persistent side pane and obscured the visualization tree.
- Earlier P1: file selection lacked a dedicated, reliably scrollable workspace.
- Earlier P2: every checkbox change immediately rebuilt the main workspace, making large inventories feel unstable.
- Fixes: moved inventory into a standalone picker, reduced the right pane to committed selections, added a fixed-height scroll region, and introduced draft selection with explicit “应用选择”.
- Post-fix evidence: `agent_runtime/context-file-picker-preview-v2.png`; 95 rows rendered with `scrollHeight 6555 > clientHeight 478`, and a second Hi-C selection applied back to the right pane successfully.

## Implementation checklist

- [x] Right pane shows only committed files.
- [x] Independent Windows-style file picker opens from the right pane.
- [x] Search, source tabs, categories, bulk actions and visible scrollbar work.
- [x] Draft changes do not affect the workspace until applied.
- [x] All recognized roles can be selected before choosing a graph.
- [x] Imported roots and nested folders can be navigated independently; breadcrumb navigation and selections persist across folders.
- [x] Choosing a graph derives a valid graph-specific subset without discarding the broader analysis scope.
- [x] The selected-file area is content-sized and capped, so the visualization tree remains immediately reachable.
- [x] Main-canvas preview prefers PNG while SVG remains available as a download artifact.
- [x] Browser console check completed with zero errors.

## Follow-up polish

- P3: a future iteration could persist the last picker category and scroll position between openings.

final result: passed
