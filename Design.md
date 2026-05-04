# Cerno Design

This document captures the current Cerno design direction so future UI work can
improve the product without drifting away from the established shape.

## Product Shape

Cerno is a desktop-style data workspace for turning spreadsheet uploads into a
reviewed data map, generated schema guidance, dashboard views, and grounded chat.

The current flow is:

1. Landing page
2. Workspaces page with saved workspaces and stats
3. Session workspace with persistent chat on the left
4. Dashboard and schema tabs in the main pane
5. Schema review and guidance before dashboard and chat become useful

The product should feel like a focused operating surface, not a marketing site
once the user enters the workspace.

## Visual Thesis

Light data-room interface with a reactive orange ASCII signal layer, sharp
hairline structure, compact monospace labels, and mostly white working surfaces.

The interface should feel precise, analytical, and calm. The orange accent gives
energy and identity, but it should not turn the whole app into a one-color theme.

## Layout Principles

- Landing page is the brand entrance.
- Workspaces is the workspace control room.
- Session workspace keeps chat persistent on the left.
- Main work happens through top tabs, currently Dashboard and Schema.
- Schema guidance is merged into the Schema surface.
- Do not add a separate Notebook or Docs page.
- Avoid nested cards. Use panels, dividers, rows, and full-width surfaces.
- Keep detail behind clicks or drawers when it would overload non-technical users.

## Current Surfaces

### Landing Page

The landing page uses a full-viewport light background with pointer-reactive
orange ASCII texture. The first impression is brand-first: Cerno, a short value
statement, and an action into the workspace.

### Workspaces

Workspaces is the home for creating and resuming analysis spaces. It contains
aggregate workspace stats, a large start-workspace CTA, and richer saved
workspace rows with file, row, and activity metadata.

### Session Workspace

The session workspace uses a persistent left chat sidebar and a main pane with
top tabs. The current tabs are Dashboard and Schema.

Chat is disabled until the data map is approved. This is intentional because the
approved schema guide is the shared understanding for downstream analysis.

### Dashboard

Dashboard views contain generated widgets. Current widgets can be repositioned,
resized, and lightly formatted in the inspector.

The dashboard should prioritize useful business views, but it should not hide
the core Cerno idea: files become a reviewed map before they become analysis.

### Schema

Schema is the review and guidance surface. It contains:

- Upload shape and review summary
- File tiles
- Relationship map
- File detail drawer
- Column review and editing
- Preview rows
- Generated schema guidance, caveats, glossary, and starter questions

This surface should stay summary-first. Detailed schema belongs in the drawer,
not as one giant inline table.

## Typography

- Body copy should never render below 12px.
- Normal body text should mostly use 14px.
- Dense metadata can use 12px.
- Display headings use the existing monospace treatment.
- Small-caps labels are allowed, but they should remain readable.
- Do not scale font size with viewport width.
- Letter spacing should stay non-negative.

## Color

Primary colors:

- Ink: `#0E0E0E`
- Paper: `#FAFAFA`
- Hairline: `#E5E5E5`
- Ember: `#E85D23`

Use orange as an action, focus, and signal color. Avoid making every section an
orange variant. Keep most surfaces white, paper, neutral gray, and ink.

## Interaction

- Pointer-reactive ASCII is part of the home identity.
- Buttons and tabs should be simple, sharp, and predictable.
- Dashboard widgets can be dragged and resized.
- Schema detail opens through a drawer.
- Destructive actions need explicit confirmation.

## Redesign Notes

The current redesign target is the Workspaces page.

Goals for that pass:

- Make workspace status easier to scan.
- Separate "start new work" from "resume old work."
- Make stats useful, not decorative.
- Improve hierarchy without adding clutter.
- Keep the light mode and orange signal identity.
