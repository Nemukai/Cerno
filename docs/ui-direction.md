# UI direction

Locked for v1. Update this doc before changing tokens or layout.

## Layout

```
┌───────────────┬──────────────────────────────────────────────┐
│               │  [01 DASHBOARD]  02 NOTEBOOK   03 SCHEMA     │  ← top tabs
│    CHAT       │ ──────────────────────────────────────────── │
│   (sidebar)   │                                              │
│               │   main content (active tab)                  │
│               │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

- **Left sidebar = Chat.** Always visible. Not a tab.
- **Main area has three top tabs:** Dashboard / Notebook / Schema. One active at a time. Default: Dashboard.
- **Dashboard tab** shows widgets for the currently selected page.
- **Notebook tab** shows the read-only cells the model wrote for the current page (model-writes / user-reads).
- **Schema tab** shows every ingested file plus the link graph between them.

## View-object pattern

- Every chat prompt that produces visuals spawns a **page** (a view object).
- The chat renders a **view chip** in the turn, e.g. `↗ 04 CUSTOMER OVERLAP`.
- Clicking the chip switches the main area to the Dashboard tab focused on that page.
- Chat-only responses (plain text, numbers) never spawn a page.

## Aesthetic — "spec sheet meets control panel"

### Typography

- Body sans: **IBM Plex Sans**
- Mono / numerals: **IBM Plex Mono**
- Numbers in KPIs, tables, and deltas use mono. Body copy uses sans.

### Color

- Accent: **Ember orange** (`#E85D23`). Used sparingly — deltas, active tab underline, primary buttons, view-chip arrow.
- Base palette: near-black on near-white with neutral greys. No gradients.

### Shape & spacing

- Sharp corners. Border radius 0-2px max. No rounded bubbles.
- Hairline dividers (1px, neutral grey). Use dividers instead of cards wherever possible.
- Section numbering: `01 OVERVIEW`, `02 ANOMALIES`, `03 LINKS`. Always zero-padded two digits.

### KPI pattern

```
01 TOTAL ROWS
1,23,456          ↗ 30%
```

- Small-caps label above.
- Huge mono number.
- Delta pill to the right: sharp rectangle, accent background, `↗` or `↘` glyph.

### Progress & status

- Barcode / tick bars for progress, not rounded bars.
- Status dots: solid square, not circle.

### Tabs

- Top tabs are **underline-active**, not pill tabs. 2px accent underline on active tab.
- Tab label format: `01 DASHBOARD` — leading number, small-caps.

### Chat styling

- No chat bubbles.
- Role labels in small-caps: `USER` / `CERNO`. Hairline divider between turns.
- Assistant reply text is plain body sans.
- Code/tool-call snippets use mono with a thin left border.
- View chips are sharp rectangles with a leading `↗` glyph in accent orange.

## What not to do

- No rounded corners beyond 2px.
- No drop shadows.
- No gradient fills.
- No emoji in UI chrome. (Glyphs like `↗ ↘` are allowed.)
- No card-on-card layering. Dividers over nesting.
