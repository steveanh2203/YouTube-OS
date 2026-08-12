# Design — MasterOS Web

A locked design system for the MasterOS self-hosted web app. Every screen reads
this system before visual changes are made. Product routes, features, data flow,
and copy intent stay unchanged unless separately specified.

## Genre

Atmospheric, restrained for a technical production tool.

## Macrostructure family

- Marketing pages: not in the current product scope.
- App pages: Workbench — fixed navigation rail, compact command bar, dense tool canvas, optional inspector or split workspace.
- Content pages: Long Document — reserved for documentation and open-source project information.

## Theme

MasterOS supports two user-selectable themes. Dark remains the default; the
selection persists locally between sessions. Both themes use the same teal
semantic accent and the same component hierarchy.

### Dark

- `--color-paper`: `oklch(14.5% 0.012 245)`
- `--color-paper-2`: `oklch(18% 0.014 245)`
- `--color-paper-3`: `oklch(22% 0.016 245)`
- `--color-ink`: `oklch(95% 0.008 220)`
- `--color-ink-2`: `oklch(73% 0.014 230)`
- `--color-rule`: `oklch(29% 0.016 242)`
- `--color-accent`: `oklch(72% 0.14 182)`
- `--color-accent-ink`: `oklch(15% 0.025 185)`
- `--color-focus`: `oklch(82% 0.13 190)`

### Light

- `--color-paper`: `oklch(97.5% 0.006 235)`
- `--color-paper-2`: `oklch(100% 0 0)`
- `--color-paper-3`: `oklch(94.5% 0.008 235)`
- `--color-ink`: `oklch(20% 0.018 245)`
- `--color-ink-2`: `oklch(42% 0.018 240)`
- `--color-rule`: `oklch(86% 0.012 238)`
- `--color-accent`: `oklch(56% 0.14 182)`
- `--color-accent-ink`: `oklch(98% 0.006 185)`
- `--color-focus`: `oklch(58% 0.14 190)`

Accent teal is reserved for active navigation, progress, healthy states, and
primary actions. Tool-specific accents may appear only in their icon container.

## Typography

- Display: Space Grotesk, weight 600, normal.
- Body: Inter, weight 400.
- Mono: JetBrains Mono or ui-monospace, weight 400.
- Display tracking: `-0.025em`.
- App headings stay compact; no marketing-scale display text inside tools.

## Spacing

Four-point named scale. Values live in `frontend/tokens.css`. App screens use a
compact 8–24px rhythm and keep controls at a minimum 40px visual height, with
44px touch targets where space allows.

## Motion

- Easings: `--ease-out`, `--ease-in`, `--ease-in-out` from `frontend/tokens.css`.
- Reveal pattern: opacity only.
- State transitions: background, border colour, opacity, and transform only.
- Reduced motion: no spatial movement and transitions at or below 150ms.

## Microinteractions stance

- Silent success for routine operations; toasts only when the result needs persistence.
- Visible `:focus-visible` ring on every interactive element.
- Tooltips: delayed for hover, immediate for keyboard focus.
- Reversible list actions should offer Undo where the existing data flow supports it.

## CTA voice

- Primary: teal fill, compact rounded rectangle, verb-first label.
- Secondary: graphite surface with a one-pixel border.
- Destructive: dark red surface and explicit verb.

## Per-page allowances

- App pages do not use decorative enrichment; function carries the screen.
- Charts may use semantic data colours but do not expand the global accent palette.
- AI Audio may use violet only for its tool icon; the interface remains teal-led.

## What pages MUST share

- MasterOS wordmark, graphite canvas, teal accent placement, typography, control heights, focus treatment, separators, and status semantics.
- Workbench hierarchy: navigation, command context, primary tool surface, optional inspector.
- shadcn/ui open-code primitives under `frontend/src/components/ui`.

## What pages MAY differ on

- Density and column split according to the tool workflow.
- Tool icon accent and specialized data visualizations.
- Table, board, timeline, editor, or inspector composition when required by the existing feature.

## Exports

The canonical implementation tokens are in `frontend/tokens.css`. Tailwind and
shadcn aliases map to the same variables; no screen may introduce a private
palette in raw hex, RGB, or OKLCH values.
