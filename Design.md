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

## Brand Assets

The canonical app mark lives at `frontend/public/brand/cerno-mark.svg`. Browser
favicon metadata uses the same mark through `frontend/public/favicon.svg`.

The Cerno wordmark uses Inter Bold with `-0.02em` letter spacing. Use the shared
frontend brand components instead of retyping the wordmark in product surfaces.

## Layout Principles

- Landing page is the brand entrance.
- Workspaces is the workspace control room.
- Session workspace is chat-first.
- Main work happens through top tabs: Ask, Insights, and Files.
- Files and chats live in the left sidebar for workspace navigation.
- Generated visuals live in the right inspector, not as the primary surface.
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

The session workspace uses a three-column chat-first layout:

- Left: files and chats in the current workspace.
- Center: Ask, Insights, and Files tabs.
- Right: selected visuals expanded for inspection.

The main product feeling should be "Here's what Cerno found. Ask follow-ups or
save insights." It should not feel like a draggable-widget dashboard builder.

Chat is disabled until the data map is approved. This is intentional because the
approved schema guide is the shared understanding for downstream analysis.

### Ask

Ask is the primary session surface. It contains Cerno chat and an inline summary
of generated insights from the processing step. Responses can spawn visuals,
which appear in the right inspector.

### Insights

Insights is the generated understanding layer: overview, usage notes,
relationships, glossary, starter questions, file descriptions, and processing
log. Approval happens here.

### Files

Files is the data management surface. It lists uploaded files, row counts,
schema versions, header status, and upload dates. Adding or deleting a file
starts a fresh processing pass while the backend keeps content-hash dedup and
reuses unchanged uploaded data where possible.

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
- Ask should be the default active session tab.
- Visuals are selected from the right inspector.
- File add/delete actions should make the user aware that Cerno is refreshing
  workspace understanding.
- Destructive actions need explicit confirmation.

## Redesign Notes

The current redesign target is the session workspace.

Goals for that pass:

- Shift from dashboard-first to chat-first.
- Make generated insights visible inline with chat.
- Make Files the place for upload, delete, and refresh lifecycle.
- Keep visuals useful but secondary.
- Preserve light mode, sharp structure, and orange signal identity.
