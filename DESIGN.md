---
name: SiMa Site Watch
description: Construction safety monitoring demo
colors:
  background: "#101313"
  surface: "#1a1e1e"
  border: "#343b39"
  text: "#eceeef"
  muted: "#adb8b3"
  accent: "#d2ee99"
  accent-text: "#182114"
  accent-hover: "#e0f5b9"
  danger: "#ff969b"
  warning: "#efc47b"
  focus: "#d7e8df"
typography:
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    lineHeight: 1.5
  heading:
    fontSize: "1.5rem"
    fontWeight: 600
    letterSpacing: "-.025em"
  label:
    fontSize: ".875rem"
rounded:
  control: "5px"
  thumbnail: "8px"
  settings: "12px"
  surface: "16px"
  navigation: "24px"
spacing:
  compact: "8px"
  control: "12px"
  inset: "16px"
  camera-gap: "20px"
  workspace-gap: "24px"
  page-inset: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-text}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  navigation-selected:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-text}"
    rounded: "{rounded.navigation}"
    padding: "8px 18px"
  camera-card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.surface}"
---

# Design System: SiMa Site Watch

## Overview

**Creative North Star: "Camera-first monitoring"**

Dark smart-camera monitoring with rounded charcoal surfaces, lime selection, and restrained interface chrome. Camera imagery carries the main visual weight; a compact alert feed provides adjacent evidence access. This direction follows the user-supplied monitoring references.

**Key Characteristics:**

- Video-first monitoring
- Rounded charcoal surfaces
- Lime selected navigation
- Compact alerts and short labels
- Diagnostics behind disclosures

## Colors

Charcoal neutrals frame the footage. Lime marks selected navigation, selected camera views, and primary settings actions. Pale pink and amber communicate danger and warning. Muted gray-green supports secondary text. The frontmatter records the values in `construction_safety/web/dashboard.css`, including the final overriding monitoring rules.

## Typography

Use the existing Inter/system sans-serif stack; the page does not load a separate font asset. Main monitor headings are 1.5rem, reducing to 1.25rem on narrow screens. Camera titles are .9375rem and navigation and alert titles are .875rem. Compact alert metadata is .6875rem. Use tabular numbers for times and metrics. Keep headings and labels brief; preserve readable text in detailed evidence and settings.

## Layout

The page has a 1920px maximum width and 32px horizontal padding, reduced to 16px at 760px. Desktop monitoring pairs a flexible two-column camera grid with a 340px alert rail and a 24px gap. From 901px to 1250px, cameras stack alongside a 310px rail. At 900px and below the rail follows the camera wall. Camera video retains a 16:9 frame with `object-fit: contain`. The alert rail is sticky on desktop, with a bounded scrolling feed; it returns to normal document flow on narrow screens.

Settings remain a separate navigation destination, with routing in a disclosure and zone editing beside a still frame. Source-dependent names, counts, and states must remain data-driven.

## Elevation & Depth

Use tonal layers and restrained borders for resting surfaces. Cards and the Actions popover have no decorative shadow; borders and surface color establish separation. Video messages use a dark translucent background for legibility. Buttons use a 120ms background transition; reduced-motion preferences disable transitions.

## Shapes

Camera cards, the alert rail, and evidence dialogs have 16px corners. Settings containers use 12px. Navigation and camera-view selectors use 24px corners. Evidence thumbnails and the fullscreen button use 8px corners. Standard fields and buttons retain 5px corners.

## Components

- Navigation: Monitor and Camera settings; lime fill indicates the current destination. Camera-view selectors use the same selection treatment.
- Camera card: short camera title, video status, settings access, prominent video, fullscreen control, compact live indicators, and collapsed diagnostics.
- Alert feed: clickable rows combine finding type, camera/location metadata, time, and evidence thumbnail. Open evidence directly from a row. Keep filters, Actions, and review explanation in disclosures.
- Evidence dialog: annotated image and incident facts, with raw JSON behind a disclosure. Provide an explicit close button and meaningful image alternative text.
- Settings: labeled fields, source routing feedback, still-frame zone editing, coordinate inputs, and explicit save/apply actions. Preserve the keyboard alternative to dragging corners.
- Focus and states: use a 2px pale focus outline with 3px offset; alert rows place it inside the row. Disabled buttons use .45 opacity. Selected state and source health need readable labels alongside color.

## Do's and Don'ts

- Use actual camera names, statuses, and evidence.
- Keep video prominent and alerts compact.
- Pair warning colors with readable status text.
- Preserve visible keyboard focus and reduced-motion behavior.
- Do not add a pause control or redundant review link to the monitor.
- Do not promote diagnostic values or long explanations into the primary monitoring view.
- Do not imply verified compliance or cross-camera identity.
- Do not invent site names, benchmarks, or brand promises.
