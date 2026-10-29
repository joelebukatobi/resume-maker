---
version: 1
name: Resume Maker
description: >
  The design system for the Resume Maker workbench. Neutral near-black chrome
  (Cursor's #181818) around a paper-bright document, one blue that only ever means act, and two status
  colours that mean exactly two things: your notes back this, or this is still
  open. Geist alone, at three weights. Structure follows Linear's DESIGN.md
  (4px base, surface steps and hairlines, no shadows) with our own palette,
  type and a 4px radius throughout.

colors:
  dark:
    ground: "#181818"
    surface: "#1E1E1E"
    raised: "#242424"
    paper: "#F7F7F7"
    text: "#D6D6DD"
    text-secondary: "#9B9BA4"
    text-muted: "#6E6E77"
    hairline: "#2A2A2A"
    hairline-strong: "#383838"
    action-text: "#7C93D6"
    action-fill: "#3B59A8"
    action-fill-hover: "#4A68B8"
    action-fill-pressed: "#2F4B8F"
    on-action: "#FFFFFF"
    action-tint: "#1E2A4A"
    backed: "#3FA36B"
    backed-text: "#5CC48A"
    backed-tint: "#16261C"
    open: "#F79009"
    open-text: "#FDB022"
    open-tint: "#1E1E1E"
    open-tint-text: "#FEDF89"
    hover-overlay: "rgba(214, 214, 221, 0.06)"
    pressed-overlay: "rgba(214, 214, 221, 0.10)"
  light:
    ground: "#F4F4F5"
    surface: "#FAFAFA"
    raised: "#FFFFFF"
    paper: "#FFFFFF"
    text: "#18181B"
    text-secondary: "#5F5F67"
    text-muted: "#8E8E96"
    hairline: "#E4E4E7"
    hairline-strong: "#D4D4D8"
    action-text: "#3B59A8"
    action-fill: "#3B59A8"
    action-fill-hover: "#324E96"
    action-fill-pressed: "#2A4282"
    on-action: "#FFFFFF"
    action-tint: "#E7EAF8"
    backed: "#1F7A4D"
    backed-text: "#1F7A4D"
    backed-tint: "#E3F4EA"
    open: "#DC6803"
    open-text: "#B54708"
    open-tint: "#FEF0E0"
    open-tint-text: "#B54708"
    hover-overlay: "rgba(24, 24, 27, 0.04)"
    pressed-overlay: "rgba(24, 24, 27, 0.08)"
  paper-ink:
    ink: "#111111"
    ink-secondary: "#555555"
    covered-highlight: "#D3F2DF"

typography:
  family: "'Geist', ui-sans-serif, system-ui, sans-serif"
  family-mono: "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
  load: "https://fonts.googleapis.com/css2?family=Geist:wght@300..500&family=Geist+Mono:wght@400&display=swap"
  display:
    fontSize: 40px
    lineHeight: 48px
    fontWeight: 300
    letterSpacing: -0.02em
  title:
    fontSize: 28px
    lineHeight: 32px
    fontWeight: 500
    letterSpacing: -0.015em
  heading:
    fontSize: 22px
    lineHeight: 32px
    fontWeight: 500
    letterSpacing: -0.01em
  card-title:
    fontSize: 18px
    lineHeight: 24px
    fontWeight: 500
    letterSpacing: -0.005em
  body:
    fontSize: 16px
    lineHeight: 24px
    fontWeight: 400
    letterSpacing: 0
  ui:
    fontSize: 14px
    lineHeight: 24px
    fontWeight: 400
    letterSpacing: 0
  ui-strong:
    fontSize: 14px
    lineHeight: 24px
    fontWeight: 500
    letterSpacing: 0
  meta:
    fontSize: 13px
    lineHeight: 16px
    fontWeight: 400
    letterSpacing: 0.01em
  eyebrow:
    fontSize: 12px
    lineHeight: 16px
    fontWeight: 500
    letterSpacing: 0.06em
    textTransform: uppercase
  mono:
    fontFamily: "{typography.family-mono}"
    fontSize: 13px
    lineHeight: 24px
    fontWeight: 400

rounded:
  none: 0px
  paper: 2px
  control: 4px
  full: 9999px

spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  xxl: 48px
  xxxl: 64px
  gutter: 32px
  inset: 24px

layout:
  nav-height: 56px
  step-strip-height: 48px
  rail-width: 340px
  rail-min: 260px
  measure-prose: 720px
  measure-list: 800px
  page-max: none

layers:
  base: 0
  rail: 10
  nav: 20
  menu: 30
  dialog-overlay: 40
  dialog: 50
  toast: 60

components:
  button-primary:
    backgroundColor: "{colors.action-fill}"
    textColor: "{colors.on-action}"
    typography: "{typography.ui-strong}"
    rounded: "{rounded.control}"
    height: 40px
    padding: 8px 14px
  button-secondary:
    backgroundColor: transparent
    textColor: "{colors.text}"
    border: "1px solid {colors.hairline-strong}"
    typography: "{typography.ui}"
    rounded: "{rounded.control}"
    height: 40px
    padding: 8px 14px
  button-compact:
    note: "Same as primary or secondary, for bars, rows and toolbars."
    typography: "{typography.meta}"
    height: 32px
    padding: 6px 12px
  icon-button:
    backgroundColor: transparent
    textColor: "{colors.text-secondary}"
    border: none
    size: 28px
    iconSize: 18px
  input:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.text}"
    border: "1px solid {colors.hairline-strong}"
    typography: "{typography.ui}"
    rounded: "{rounded.control}"
    height: 40px
    padding: 8px 12px
  input-compact:
    height: 32px
    padding: 6px 12px
    typography: "{typography.meta}"
  chip:
    typography: "{typography.meta}"
    rounded: "{rounded.control}"
    height: 28px
    padding: 4px 10px
  chip-on:
    backgroundColor: "{colors.text}"
    textColor: "{colors.ground}"
  chip-off:
    backgroundColor: transparent
    textColor: "{colors.text-muted}"
    border: "1px solid {colors.hairline}"
  chip-suggested:
    backgroundColor: transparent
    textColor: "{colors.text}"
    border: "1px dashed {colors.hairline-strong}"
  step-pill:
    typography: "{typography.meta}"
    rounded: "{rounded.control}"
    padding: 4px 10px
  status-dot:
    size: 6px
    rounded: "{rounded.full}"
  nav-bar:
    backgroundColor: "{colors.ground}"
    height: "{layout.nav-height}"
    padding: "0 {spacing.gutter}"
    borderBottom: "1px solid {colors.hairline}"
  rail:
    backgroundColor: "{colors.ground}"
    width: "{layout.rail-width}"
    padding: "40px {spacing.inset}"
    borderLeft: "1px solid {colors.hairline}"
  row:
    padding: "14px 0"
    borderBottom: "1px solid {colors.hairline}"
  card:
    backgroundColor: "{colors.surface}"
    border: "1px solid {colors.hairline}"
    rounded: "{rounded.control}"
    padding: 16px
  card-selected:
    border: "1px solid {colors.action-text}"
    backgroundColor: "{colors.action-tint}"
  paper-frame:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.paper}"
    border: "1px solid {colors.hairline}"
---

# Resume Maker

## Overview

The product makes one thing, a page you send. Every screen is built so that page
is the brightest object on it. Chrome is neutral near-black in dark mode, taken from
Cursor's editor (`#181818`), and a neutral zinc grey in light mode. No warm or
brown tones anywhere. The CV is off-white on dark and pure white on light. Nothing else on
screen is allowed to be that bright.

Everything the chrome needs comes from three sources. Linear's DESIGN.md sets the
structure: a 4px base, spacing in steps of 4 and 8, depth from surface steps and
hairlines, no shadows. Geist carries every word, at three weights. The palette has
six roles and nothing else.

The current pages in `static/` are the previous design language. They are a
record of what the app does, not of how it should look. New work follows this
file.

## Colors

Six roles. Each has one job, and a new colour has to name its job before it
gets added.

- **Ground** is the app frame: the nav bar, gutters, rails. Nothing sits on
  ground directly except text and hairlines.
- **Surface** and **raised** are the two steps above it. Depth comes from moving
  one step lighter, never from a shadow.
- **Paper** is the document, and only the document. On dark it is off-white
  (`#F7F7F7`) so it reads as paper rather than as a white hole; on light it is
  pure white so it stays brighter than the chrome around it.
- **Action** is blue, and it only ever means *you can do this*: buttons, links,
  the focus ring, the selected row. Two values: `action-text` for text, borders
  and rings, `action-fill` for filled buttons. The fill is the same in both modes.
- **Backed** is green, and it only ever means *your notes support this*. Never
  clickable. In dark mode the base green is too dark for text; use `backed-text`.
- **Open** is amber in dark mode and orange in light, and it only ever means
  *not answered yet*: a posting term nothing covers, a question waiting for
  you, a page over length. Never clickable either. In dark mode it has no
  tinted fill, because dark amber is brown; an open card is surface with an
  amber border and amber text.

Three things that look like status are not, and stay neutral: progress (a step
reached, an application sent), destructive actions such as Delete, and anything
the voice changed. None of them says "your notes back this" or "this is
unanswered".

Status never uses blue, so a status never looks clickable. Backed and open sit
close in hue, so they are always paired with a shape: a filled dot for backed, a
hollow dot for open. They have to read in a greyscale print.

`text-muted` passes 3:1 and no more. It is for placeholders, disabled labels and
counts in passing, never for 12–13px text a person needs to read.

### Contrast, checked

Every text pairing above was measured against WCAG on 2026-10-29.

| Pair | Dark | Light |
|---|---|---|
| text on surface | 11.5 | 17.0 |
| text-secondary on surface | 6.1 | 6.1 |
| text-muted on surface | 3.3 | 3.1 |
| action-text on surface | 5.6 | 6.3 |
| on-action on action-fill | 6.6 | 6.6 |
| backed-text on surface | 7.7 | 5.1 |
| open-text on surface | 9.1 | 5.2 |
| open-tint-text on open-tint | 12.8 | 4.9 |
| ink on paper | 17.6 | 18.9 |
| ink on covered-highlight | 15.8 | 15.8 |

A new pairing gets measured before it ships.

## Typography

**Geist alone.** Hierarchy comes from size, weight and tracking, not from a
second family. Three weights: 400 for body and UI, 500 for headings, labels and
buttons, 300 for display sizes above 32px only. Nothing above 500.

The size scale moves in steps of 2px at small sizes. The rhythm is set by line
height: every line height is a multiple of 8, so any stack of text is a multiple
of 8 tall regardless of which sizes it mixes.

Tracking tightens as type grows and loosens as it shrinks: −0.02em at 40px,
0 at 16px, +0.01em at 13px, +0.06em on uppercase eyebrows. Small grotesk type
closes up without the loosening.

**Tabular figures are mandatory for numbers that line up.** Geist ships `tnum` but
leaves it off. Any score, count, date, point size or page count in a column or
a row sets `font-feature-settings: "tnum"`. Prose numbers do not need it.

**Geist Mono** is for exactly two things: file paths, and terms taken word for
word from a posting. A mono term reads as a literal string from somewhere else,
which is what a frozen keyword is.

## Layout

- **Gutter 32px** from the frame edge to the first element, on every screen.
  **Inset 24px** inside anything: rails, cards, panels. The two differ on purpose,
  so the page edge reads as an edge.
- **Nav bar** is three parts: destinations on the left, search centred when the
  page has one, actions on the right. 56px.
- **Main column plus rail** is the default screen: the main column takes the rest,
  the rail is 340px (260px minimum) behind a hairline. Lists that need width, such
  as Applications, drop the rail and run full-bleed.
- **Measure**: 720px for prose and forms, 800px for list-heavy screens like
  Bullets, where a line, its meta and a control share a row.
- Below 1200px the chat leaves the main view and becomes a mode; below 900px the
  rail collapses under the main column.

## Elevation and depth

| Level | Treatment | Use |
|---|---|---|
| 0 | ground | Frame, nav, rails |
| 1 | surface + 1px hairline | Panels, cards, rows on hover |
| 2 | raised + 1px hairline-strong | Inputs, menus, the open bullet |
| Focus | 2px `action-text` at 50%, 2px offset | Any focused control |

No shadows, in either mode. A menu is raised plus a strong hairline. The paper
gets a hairline, not a drop shadow.

## Shapes

One radius for everything interactive and every container: **4px**. The paper is
2px. Status dots are full circles. Linear uses 8px on buttons; we don't, because
4px sits better against the compact controls and nothing should look softer than
the page it surrounds.

## Components

Atoms, molecules, organisms, in that order. Build up, never sideways.

**Atoms**: button (primary, secondary), compact button, icon button, input,
compact input, bespoke select (trigger + menu, never a native `<select>`),
chip (on, off, suggested), step pill, status dot, eyebrow label.

**Molecules**: labelled field, bullet row (dot, text, meta, chevron), alternative
row (text, meta, check), term row (term, state), nav group, tab bar.

**Organisms**: nav bar, step strip, rail, the paper frame, the change pane, the
applications list.

Rules that hold across all of them:

- **Two control heights, each with a job.** 40px for actions and fields on the
  page; 32px in bars, rows, toolbars and modals. On a page, every field in a
  row (input, select, chip) shares the same height. Nothing else.
- **Reuse the atoms.** Same button, input, select and chip everywhere. Do not
  invent a parallel control for one screen.
- **Every action is a button.** Links are only for going somewhere: nav, step
  pills, *see all*, *open*. A primary save on a settings-style page is `.btn`
  / `.btn-primary`, not a text link.
- **Icon-only controls have no border and no fill.** A chevron opens, a check
  accepts, a kebab holds the rest. Each carries an `aria-label`.
- **Icons** are the Iconoir set already in the repo, 24px grid, stroke 1.5:
  microphone, send-diagonal, paperclip, xmark, plus, minus, nav arrows, sidebar,
  and the check and more-vert added for the redesign.
- **Cards only when the boundary means something**: a choice you are making, or a
  proposal you can accept. Fields and lists are never wrapped in a card.
- **Labels are Title Case.** Every word in a field label or section title starts
  with a capital: Paper Size, Open Models, not Paper size.
- **No long hyphens.** Never an em dash (—) or en dash (–) in UI copy. Use a
  comma, a period, or a short hyphen-minus (-) only for compounds.

## States

Derived, never picked per component.

- **Hover**: lay `hover-overlay` over whatever the control sits on. Filled
  buttons switch to `action-fill-hover`.
- **Pressed**: `pressed-overlay`; filled buttons to `action-fill-pressed`.
- **Focus**: the ring above, on `:focus-visible` only.
- **Selected**: `action-tint` fill with an `action-text` border.
- **Disabled**: avoid it. Keep the control enabled and explain on use. When it
  must happen: `text-muted` label, no fill, `hairline` border.

## The paper

The CV and letter are not governed by this file. They are measured in points
and inches, set in a font chosen for parsers, and configured on the Settings
screen. Both the DOCX and the PDF read the same `Theme`. Nothing in this file's
type scale, spacing or palette applies inside the page, apart from the covered
highlight shown in the editor and never exported.

## Do's and don'ts

**Do**
- Keep the paper the brightest thing on screen.
- Name a colour's job before adding it.
- Pair every status colour with a shape.
- Put numbers that line up in `tnum`.
- Check contrast before shipping a new pairing.

**Don't**
- Use blue for status, or green or amber for anything clickable.
- Add a shadow, a gradient, or a second typeface.
- Use weight 600 or above.
- Wrap a field or a list in a card.
- Put explanations in rails. A rail holds state, not instructions.
- Use a native `<select>`, or a long hyphen (— / –) in labels or leads.
- Mix control heights in one row of fields.

## Implementation

Native, no build step, no npm. The backend stays exactly as it is.

- **Tokens** become CSS custom properties on `:root`, named after the keys above:
  `--ground`, `--action-fill`, `--space-lg`, `--radius-control`, `--layer-menu`.
  Dark is the default; light mode redefines the colour variables under
  `[data-theme="light"]` and nothing else. Components read variables only,
  never a raw hex.
- **Reset** is our own and short: box-sizing, zeroed margins, block media,
  form controls inheriting the font. No Pico.
- **Atoms** are plain CSS classes (`.btn`, `.btn-compact`, `.chip`, `.input`)
  in one stylesheet.
- **Molecules and organisms** are custom elements (`<bullet-row>`,
  `<step-strip>`, `<change-pane>`), one file each, **in the light DOM**. CSS
  variables would reach into a shadow root but classes would not, and every
  component needs the shared atoms. No shadow DOM.
- **Scripts** are native ES modules, `<script type="module">`, one per
  component, importing what they need.
- **Z-index** comes only from the `layers` tokens. No literal z-index values.

## Iteration

Change a value here first, then in code. The wireframes for this system are the
**Workbench wireframes** page of the Resume Maker canvas.
