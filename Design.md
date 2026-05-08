# Cerno Design

This document is the current design source of truth for Cerno. The landing page
uses oceanic topography as the visual artifact, while the product copy stays
plain and functional.

## Product Shape

Cerno is a desktop-style data workspace for turning spreadsheet uploads into
reviewed schema guidance, dashboard views, and grounded chat.

The current flow is:

1. Landing page
2. Workspaces page with saved workspaces and stats
3. Session workspace with persistent chat on the left
4. Ask, Insights, and Files tabs in the main pane
5. Schema review and guidance before dashboard and chat become useful

The product should feel like a focused operating surface once the user enters
the workspace. The landing page can be more atmospheric, but it should still
lead directly into work.

## Visual Thesis

Oceanic topography on warm paper: dense contour lines, bathymetry-like depth,
Kai ocean tones, and orange Nemukai accents on a textured sand surface.

The visual language can reference topography, but the written interface should
not use cartography puns to explain product behavior. Say what Cerno does:
upload files, review schema, ask questions, generate grounded answers.

## Landing Content Plan

- Hero: full-viewport oceanic topography background, Cerno as the clearest first
  signal, one short explanation, and a single sign-in action.
- Support: three plain workflow stages: profile files, review schema, ask
  questions.
- Detail: show what context Cerno stores without using a "legend" metaphor.
- Final CTA: invite the user to start with a workbook.

## Interaction Thesis

- Contour lines respond subtly to pointer movement.
- Orange accent strokes draw in on page load, then pulse lightly.
- Workflow rows and context rows move on hover with restrained color shifts.

## Brand Assets

The canonical app mark lives at `frontend/public/brand/cerno-mark.svg`. Browser
favicon metadata uses the same mark through `frontend/public/favicon.svg`.

The Cerno wordmark uses the shared frontend brand components. Do not retype the
wordmark on product surfaces when `CernoLockup` can be used.

## Layout Principles

- Landing page is the brand entrance.
- Workspaces is the workspace control room.
- Session workspace is chat-first.
- Main work happens through top tabs: Ask, Insights, and Files.
- Files and chats live in the left sidebar for workspace navigation.
- Generated visuals live near the answer that produced them, with inspection
  affordances when needed.
- Do not add a separate Notebook or Docs page.
- Avoid nested cards. Use panels, dividers, rows, and full-width surfaces.
- Keep detail behind clicks or drawers when it would overload non-technical
  users.

## Current Surfaces

### Landing Page

The landing page uses a restrained oceanic topography composition: smooth nested
contours, Kai ocean lines, orange highlighted levels, and paper grain. Contour
families should stay closed and non-intersecting; avoid crossing route lines,
sharp polygon cuts, and map clutter behind the hero copy.

The brand hierarchy is:

1. Cerno
2. "Turn spreadsheets into trusted analysis."
3. Sign in to continue

### Workspaces

Workspaces is the home for creating and resuming analysis spaces. It contains
aggregate workspace stats, a large start-workspace CTA, and saved workspace rows
with file, row, and activity metadata.

The surface should share the landing page's paper and ocean-contour system, but
stay more operational: one large low-contrast contour artifact behind the right
side, orange used only for action and selected contour levels, and Kai tones for
secondary linework. Keep the start control clearly framed as a workspace-name
form, not a search bar.

### Session Workspace

The session workspace uses a three-column chat-first layout:

- Left: files and chats in the current workspace.
- Center: Ask and Insights tabs.
- Right: selected visuals expanded for inspection when the flow needs it.

The left sidebar can be hidden from the top header. It should stay narrow and
scannable: one Files link with an upload count, then compact chat rows. Chat row
context actions are rename and delete.

Chat is disabled until the schema guide is approved. This is intentional because
the approved guide is the shared understanding for downstream analysis.

### Ask

Ask is the primary session surface. It contains Cerno chat and generated
insights from the processing step. Responses can spawn visuals grounded in the
approved guide.

### Insights

Insights is the generated understanding layer, but it should read as a simple
review surface for non-technical users. Show the current state, a short summary,
key totals, compact file rows, compact connection groups, and small context
counts. Detailed fields, row previews, relationship evidence, usage notes,
glossary terms, and edit controls belong in the right-side detail drawer after a
user clicks a file or relationship.

### Files

Files should not be a top-level tab after upload. Source-file management lives
inside the Insights detail drawer and session header: upload from the top bar,
review file summaries in Insights, and delete a file from its detail drawer.
Legacy `/files` routes should redirect to Insights.

## Typography

- Body copy should never render below 12px.
- Normal body text should mostly use 14px.
- Dense metadata can use 12px.
- Display headings use the existing serif treatment.
- Small-caps labels are allowed, but they should remain readable.
- Do not scale font size with viewport width.
- Letter spacing should stay non-negative.

## Color

Primary colors:

- Sand: `#F2EBDD`
- Warm ink: `#141210`
- Kai 900: `#07242E`
- Kai 600: `#176B7D`
- Kai 500: `#1F869A`
- Yuhi 500: `#D17B2E`
- Yuhi 400: `#E89A48`
- Sango 500: `#C06A54`

Use orange as the primary action and visual accent. Use Kai as the secondary
oceanic palette. Do not use pure white or pure black on the landing page.

## Interaction

- The landing topography may move subtly, but the product workspace should stay quiet.
- Buttons and tabs should be simple, sharp, and predictable.
- Ask should be the default active session tab.
- File add/delete actions should make the user aware that Cerno is refreshing
  workspace understanding.
- Destructive actions need explicit confirmation.
