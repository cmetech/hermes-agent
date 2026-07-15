# Data Contract

The inspect, analyze, prepare, and render helpers treat source files as
read-only data. They are local and deterministic; none calls a model,
`image_generate`, web search, the network, or a remote renderer. The coworker
that invokes them is model-backed: helper stdout selected for the interview,
including source metadata and minimal stage labels, may enter chat.

## Command-line interface

Use these forms exactly:

```text
python3 scripts/prepare_opportunities.py inspect SOURCE [--sheet SHEET] [--json-key KEY]
python3 scripts/prepare_opportunities.py analyze SOURCE --view VIEW \
  --semantics SEMANTICS.json [--mapping MAPPING.json] \
  [--sheet SHEET] [--json-key KEY] [--months LABEL] [--filters FILTERS.json]
python3 scripts/prepare_opportunities.py prepare SOURCE --view VIEW \
  --semantics SEMANTICS.json [--mapping MAPPING.json] \
  --output-dir DIRECTORY [--sheet SHEET] [--json-key KEY] \
  [--months LABEL] [--filters FILTERS.json]
python3 scripts/render_opportunity_visual.py --preflight --output-dir DIRECTORY
python3 scripts/render_opportunity_visual.py normalized-data.json \
  --output-dir DIRECTORY [--width 1920] [--height 1080] \
  [--png auto|never|required]
```

Canonical `VIEW` argument values and normalized `view` identifiers are exactly `wins`, `losses`, `all-progression`, and `positive-progression`.
Valid CLI forms are:

- `--view wins`
- `--view losses`
- `--view all-progression`
- `--view positive-progression`

The normalized `view` key stores the bare identifier without the `--view` option name.
Human-facing “all-stage progression” maps to `all-progression`, and human-facing
“positive progression” maps to `positive-progression`. Inspection reports
candidate sheets, JSON arrays, mappings, months, and ambiguities without
creating render artifacts. Analysis applies the proposed mapping, selected
months, semantics, and filters without an output directory. Preparation writes
to a new output directory and never overwrites an existing run by default.

## Read-only semantics analysis

Run `analyze` after initial inspection and proposed semantics, and before
playback or preparation. It returns one structured JSON object containing safe
source metadata, the selected mapping/months, filter key names, counts,
`unresolved_terminal_stages`, `unresolved_transitions`, and
`mixed_transitions`. It never creates an output directory or artifact and
never exposes opportunity names, IDs, or raw rows.

Each grouped `unresolved_terminal_stages` item contains the exact display
`stage`, stable code `unknown_terminal_status`, `occurrences`, and
`affects_output: true`. Terminal confirmation can change view inclusion,
terminal metadata, and first-terminal cutoff. Resolve one stage at a time:
add an alias to `positive_terminals` or `negative_terminals`, or add a
confirmed non-terminal to `non_terminal_stages`, then rerun analyze.

The terminal-status scan includes relevant pre-range stage history before the
first selected month as well as selected normalized records. Movement-direction
groups remain scoped to the selected range. Because terminal-before-range
exclusion is evaluated before filters, analysis may conservatively report an
unknown pre-range stage from a row that a later filter would exclude; confirming
it can still change the exclusion audit. Blank and known stages are ignored.
The scan stops immediately after each row's first confirmed terminal and never
groups or discloses a later stage. An uncached stage formula before that cutoff
returns `formula_cache_missing`; formulas after that cutoff are ignored.
After recording safe cell metadata for a pre-range formula, the scan continues
only to detect a later confirmed terminal; it does not group later labels or
record later formulas. Because the confirmed terminal excludes the row before
normalization, fixed mapped and selected formulas on that range-excluded row
are also ignored. Formulas on other rows keep the existing safety rules.
Formulas are never executed.

Each grouped transition contains the exact display `from_stage` and
`to_stage`, stable `code`, `occurrences`, `terminal_status_resolved`,
`affects_inclusion`, and `affects_truncation`. Blank months remain blank; the
transition uses the previous populated stage. An unresolved destination
terminal status conservatively sets both impact fields true in every view.
After terminal status is resolved as non-terminal, an unknown direction
affects `positive-progression` inclusion only when its filtered record has no
other `positive` or `won` transition; it does not affect selection for other
views. Ask one semantics question at a time, update the semantics JSON, and
rerun analyze before preparing artifacts.

Only the local helpers are guaranteed not to call a model or network service.
Do not paste confidential rows into chat unless the configured model and the
user's privacy policy permit it. Minimal stage labels and diagnostics shared
for the semantics interview may enter the model-backed chat.

## Accepted sources

- UTF-8 CSV with a header row.
- JSON as an array of row objects or an object containing the array selected by
  `--json-key`.
- XLSX, using the uniquely detected or explicitly selected worksheet. Formula
  cells use cached values; formulas are never executed.
- Pasted tabular data saved with user approval as a temporary UTF-8 CSV before
  inspection.

Required logical fields are Area, Sub-area, Opportunity Name, TCV,
Probability, and chronological monthly stages. At least two populated stage
cells are required for an included row. Monthly probability columns are
optional and must pair with their stage month. A single current Probability
field is for display and sorting only, never inferred history. Preserve all
display labels exactly and leave blank month cells blank.

## Mapping

Use an explicit mapping when aliases or month order are ambiguous. This is the
exact optional shape:

```json
{
  "area": "Area",
  "sub_area": "Sub-area",
  "opportunity_name": "Opportunity Name",
  "tcv": "TCV",
  "probability": "Probability",
  "months": [
    {"key": "2026-03", "label": "Mar '26", "stage": "Mar '26", "probability": "Mar '26 Probability"}
  ]
}
```

When the mapping is absent, unambiguous aliases and months are auto-detected.
Ambiguous required columns or month order require user confirmation; the
preparer must not guess.

## Stage semantics

The required semantics document has this exact shape:

```json
{
  "positive_terminals": ["Won"],
  "negative_terminals": ["Lost", "Cancelled"],
  "non_terminal_stages": ["Discovery"],
  "stage_paths": [["Ideation", "Solution", "Proposal", "SDP2", "Won"]],
  "positive_transitions": [["Proposal", "Workshop"]],
  "tcv_order": ["X-Small", "Small", "Medium", "Large", "X-Large"],
  "probability_order": ["Low", "Medium", "High", "Certain"]
}
```

Terminal aliases and comparisons may be case-insensitive, but display values
are never rewritten. The allowed transition values are `initial`, `positive`,
`negative`, `neutral`, `mixed`, `unknown`, `won`, and `lost`.

Terminal destinations take precedence. Otherwise classification combines a
confirmed stage order or explicit positive edge with paired monthly
probability movement when available. Opposing directional signals are
`mixed`; unchanged signals are `neutral`; an unrecognized transition without
a usable probability signal is `unknown` and produces a warning. Never infer
forward or backward movement from an unknown stage order.

`non_terminal_stages` is optional and defaults to `[]`. Its values must be
unique, nonblank strings and must not overlap positive or negative terminals
case-insensitively. Stages named in confirmed stage paths or positive
transitions also have confirmed non-terminal status unless a terminal list
names them; terminal lists take precedence. Listing a stage as known does not
change its display label or invent a direction.

Every `mixed` transition emits a `mixed_signals` warning.
Every `unknown` transition emits an `unknown_transition` warning.
Both warnings are retained in `normalized-data.json`
and carried into `render-manifest.json`; neither classification may be silently omitted.

## Normalized output

`normalized-data.json` contains these top-level keys:

- `schema_version`
- `view`
- `source`
- `mapping`
- `semantics`
- `selected_months`
- `filters`
- `records`
- `exclusions`
- `warnings`
- `counts`

Records retain a stable generated row ID, source row number, exact display
values, normalized sort ranks, ordered month entries, terminal indices,
transition classifications, and inclusion decisions. The normalized JSON is
the renderer's only data input.

The stable exclusion codes are:

- `missing_required_value`
- `invalid_tcv`
- `invalid_probability`
- `insufficient_stages`
- `filter_not_matched`
- `view_not_matched`
- `terminal_before_range`

## View behavior

- **Wins (`wins`):** include rows reaching a positive terminal in range and
  truncate at the first positive terminal.
- **Losses (`losses`):** include rows reaching a negative terminal in range and
  truncate at the first negative terminal.
- **All-stage progression (`all-progression`):** include rows with at least two
  populated stages and truncate at the first terminal of either kind.
- **Positive progression (`positive-progression`):** include rows with at least
  one `positive` transition and truncate at the first terminal of either kind.

Empty months remain empty. Transitions compare consecutive populated stages
without carrying values across gaps. Group by Area then Sub-area. Within each
Sub-area, sort by normalized TCV descending, normalized Probability descending,
Opportunity Name ascending, then source row number.

## Output and recovery

Preparation writes `source-summary.json`, `normalized-data.json`, and
`exclusions.json`. Rendering writes `render-manifest.json` plus numbered SVG
and HTML pages and, when locally available, PNG pages. Invalid or ambiguous
source values are reported by row and stable code. No matching rows still
produce normalization and exclusion artifacts, but not a blank visual.
Missing Playwright or Chromium is a PNG fallback, not an SVG/HTML failure.
