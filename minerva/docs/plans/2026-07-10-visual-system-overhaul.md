# Application Visual System Overhaul

**Date:** 2026-07-10  
**Status:** Implemented  
**Scope:** Application shell, navigation, shared widgets, all four primary pages, and global QSS

## Objective

Replace the page-by-page styling accumulated in the application with one coherent
desktop visual system. The redesign preserves the existing workflows and domain
behaviour while improving hierarchy, density, discoverability, and state feedback.

## Design principles

1. **Operational content first.** Page titles, primary actions, filters, and current
   state are visible without competing dashboard chrome.
2. **One action hierarchy.** One or two common actions remain visible; destructive,
   batch, and lower-frequency actions move into an overflow menu.
3. **Consistent surfaces.** Panels, inspectors, forms, tables, and empty states use
   the same border, radius, spacing, and elevation vocabulary.
4. **Semantic colour.** Blue identifies interaction and selection; green identifies
   healthy/completed state; amber warns; red is reserved for failures and destructive
   actions.
5. **Desktop density.** Controls remain comfortable without adopting oversized web
   dashboard spacing.
6. **Bounded states.** Empty, loading, and error content appears in a deliberate card
   instead of floating in a large blank page.

## Visual tokens

The central `ThemeTokens` object now defines the application palette, typography,
radii, and accent variants. The default dark palette uses:

- background: `#070C14`
- base surface: `#0D1522`
- raised surface: `#152033`
- hover surface: `#1B2940`
- border: `#223049`
- strong border: `#31435F`
- primary text: `#F3F6FB`
- default accent: blue
- body and heading family: `Segoe UI` with platform fallback

Accent selection remains supported for blue, purple, and green themes.

## Shared component changes

- `AppSidebar`: branded shell navigation, clear selected state, workspace grouping,
  and a compact environment/version footer.
- `PageHeader`: page eyebrow, title/subtitle hierarchy, visible primary actions, and
  a reusable overflow menu with compatibility proxies for existing page logic.
- `EmptyState` / `ContentState`: bounded loading, empty, and error cards with a
  consistent icon well and action placement.
- `SurfacePanel` / `PanelHeader`: normalized internal spacing and heading treatment.
- `MetricCard`: compact horizontal metric presentation instead of oversized tiles.
- Global controls: unified buttons, inputs, combo boxes, switches, menus, tooltips,
  scrollbars, tabs, and segmented controls.
- Global tables: shared headers, row selection, separators, padding, and scrollbars.

## Page-level changes

### Reports

- Keeps Add report and Import folder as visible primary operations.
- Moves matching, queueing, removal, and batch actions into overflow.
- Uses the shared bounded empty state when no reports are imported.

### Library

- Keeps Add to queue visible.
- Moves export/open actions into overflow.
- Uses the shared filter bar, compact metrics, table, and error/empty states.

### Downloads

- Retains the redesigned master/detail queue introduced in the downloads overhaul.
- Aligns its margins, header, status strip, panels, tables, inspector, and chart with
  the application-wide visual system.

### Settings

- Adds a stable category rail and centered maximum-width settings content.
- Uses consistent form groups and fields.
- Adds a persistent save footer with explicit saved/unsaved state.

## Behavioural constraints

- Existing page actions, shortcuts, signals, and test-facing button proxies are
  preserved.
- The overhaul does not alter report matching, indexing, download scheduling, or
  persistence semantics.
- Sidebar width remains 240 px for compatibility and predictable desktop layout.
- The shell minimum size is 1100×700; the default size is 1440×900.

## QSS architecture

The stylesheet loader now composes purpose-specific modules:

- `base.qss`
- `layout.qss`
- `controls.qss`
- `tables.qss`
- `navigation.qss`
- `cards.qss`
- page and component-specific stylesheets

This keeps generic interaction styling out of individual page styles and reduces
cross-page drift.
