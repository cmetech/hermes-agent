# Visual Rules

Apply these rules to every generated opportunity visual. SVG is canonical;
HTML is a self-contained preview; PNG is an optional local rasterization.

## Global constraints

- Default page size is 1920×1080 in a wide, slide-ready format.
- Background is white or `#F2F2F2`.
- Text is black. Use a bundled or installed Ericsson Hilda font only when its
  license and local availability permit; otherwise use a documented humanist
  sans-serif fallback stack.
- Positive stage fill is Teal `#23969A`.
- Negative stage fill is Coral `#E65D6A`.
- Neutral, mixed, unknown, and first-stage fill is Gray `#A6A6A6`.
- Structural accent is Ericsson Blue `#1174E6`.
- Probability uses its exact display label with a small approved-color bullet.
- No gradients, shadows, decorative icons, photos, arbitrary user styling, or
  external assets.
- At 1920×1080, body and stage text must be at least 16px. Never shrink below
  that minimum to fit more rows or month columns; paginate instead.

## Table and pagination

- Use this fixed column order: Area, Sub-area, Opportunity Name, TCV,
  Probability, then chronological months.
- Give all rows on a page equal height and all month columns equal width.
- Wrap text within bounded lines or ellipsize it. Retain every complete value in
  `normalized-data.json` and HTML/SVG `data-full-value` metadata. The
  privacy-safe render manifest records IDs, page mappings, warnings, and hashes,
  not the complete display values.
- Create deterministic numbered pages when rows or month columns cannot fit
  legibly.
- Every page repeats the title, fixed column headers, month headers, and
  applicable group context.
- Preserve Area and Sub-area group boundaries where practical; when a group
  continues, make the repeated context and page continuity explicit.
- Preserve all user-supplied labels exactly.

## Stage presentation

- The first populated stage is neutral gray unless it is a terminal, in which
  case it keeps its terminal color.
- Later stages use their transition classification: positive is teal, negative
  is coral, and neutral, mixed, and unknown are gray.
- Empty month cells have no pill and stay visibly empty. Do not imply a stage
  across a gap.
- Wins truncate after the first positive terminal. Losses truncate after the
  first negative terminal. All-stage and positive progression truncate after
  the first terminal of either kind.
- Stage labels remain exactly as supplied.

## Local-only output

HTML and SVG contain only locally generated, escaped data and embedded styles.
They contain no JavaScript, remote URLs, stylesheets, fonts, images, iframes,
network resources, or other network-capable elements. PNG capture uses a local
file URL, denies all external requests, uses the exact page viewport, and
captures each numbered page independently.

## Manual checks

Review every page and confirm:

- labels match the normalized data exactly;
- no text or table content is clipped;
- text, pills, headers, and group context do not overlap;
- positive, negative, neutral, mixed, unknown, and terminal colors are correct;
- empty months remain empty;
- Area and Sub-area group boundaries are clear;
- repeated headers and group context preserve page continuity;
- terminal cutoff leaves no stages after the applicable terminal;
- every PNG has the requested dimensions, including the default 1920×1080.

Also compare page-to-row mappings, warning and exclusion counts, and artifact
hashes against `render-manifest.json`. A PNG fallback does not invalidate
successful SVG and HTML artifacts.
