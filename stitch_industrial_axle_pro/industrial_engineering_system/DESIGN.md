---
name: Industrial Engineering System
colors:
  surface: '#f6faff'
  surface-dim: '#cedce8'
  surface-bright: '#f6faff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eaf5ff'
  surface-container: '#e2f0fc'
  surface-container-high: '#dceaf6'
  surface-container-highest: '#d6e4f0'
  on-surface: '#101d26'
  on-surface-variant: '#43474d'
  inverse-surface: '#25323b'
  inverse-on-surface: '#e5f3ff'
  outline: '#74777e'
  outline-variant: '#c3c6ce'
  surface-tint: '#49607c'
  primary: '#00152a'
  on-primary: '#ffffff'
  primary-container: '#102a43'
  on-primary-container: '#7a92b0'
  inverse-primary: '#b0c9e8'
  secondary: '#366383'
  on-secondary: '#ffffff'
  secondary-container: '#aed9ff'
  on-secondary-container: '#33607f'
  tertiary: '#250d00'
  on-tertiary: '#ffffff'
  tertiary-container: '#441e00'
  on-tertiary-container: '#df7108'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d1e4ff'
  primary-fixed-dim: '#b0c9e8'
  on-primary-fixed: '#011d35'
  on-primary-fixed-variant: '#314863'
  secondary-fixed: '#cae6ff'
  secondary-fixed-dim: '#a0cbf0'
  on-secondary-fixed: '#001e30'
  on-secondary-fixed-variant: '#1b4b69'
  tertiary-fixed: '#ffdcc6'
  tertiary-fixed-dim: '#ffb786'
  on-tertiary-fixed: '#311300'
  on-tertiary-fixed-variant: '#723600'
  background: '#f6faff'
  on-background: '#101d26'
  surface-variant: '#d6e4f0'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  technical-data:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-caps:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 8px
  container-max: 1280px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 40px
  stack-sm: 4px
  stack-md: 12px
  stack-lg: 24px
---

## Brand & Style
This design system is engineered for the B2B industrial manufacturing sector, specifically focusing on reliability, precision, and utility. The brand personality is authoritative and technical, eschewing the "lifestyle" aesthetics of consumer automotive brands in favor of a "blueprint-first" philosophy. 

The design style utilizes **Corporate Modern** principles with a heavy emphasis on **Information Density** and **Clarity**. The interface should feel like a high-end engineering tool: structured, dependable, and efficient. Visual flourish is reserved strictly for functional signaling (e.g., status changes or primary actions) to ensure that technical specifications remain the focal point of the user experience.

## Colors
The palette is rooted in "Industrial Navy" and "Charcoal" to establish immediate professional trust. 
- **Primary Navy (#102A43):** Used for global navigation, headers, and primary brand surfaces.
- **Engineering Orange (#F58220):** Reserved exclusively for high-priority Call-to-Actions (CTAs) and RFQ (Request for Quote) triggers.
- **Warning Yellow (#F4B400):** Used for inventory alerts, technical warnings, or "limited availability" status.
- **Grayscale:** The "Light Gray Background" (#F4F6F8) provides a low-strain canvas, while "Medium Gray" (#667582) is used for secondary metadata and technical units (e.g., "kg", "mm").

## Typography
The system uses **Inter** for its exceptional legibility in data-heavy environments. To lean into the industrial aesthetic, **JetBrains Mono** is introduced for specific technical values, part numbers, and dimensions within specification tables to differentiate data from descriptive text.

Headings should be compact with slight negative letter spacing to feel "engineered" and sturdy. Body text maintains a generous line height to ensure readability of long-form technical documentation.

## Layout & Spacing
This system employs a **Fixed Grid** model for desktop to maintain structural integrity of technical tables, transitioning to a fluid model for mobile.

- **Desktop (1280px+):** 12-column grid, 24px gutters, 40px external margins.
- **Tablet (768px - 1279px):** 8-column grid, 16px gutters, 24px external margins.
- **Mobile (<767px):** 4-column grid, 16px gutters, 16px external margins.

The spacing rhythm is strictly based on an **8px base unit**. Technical tables should use a "Condensed" vertical rhythm (stack-sm) to maximize information density without sacrificing horizontal alignment.

## Elevation & Depth
The design system avoids soft shadows and "floating" elements. Depth is communicated via **Tonal Layering** and **Low-Contrast Outlines**.

- **Level 0 (Background):** #F4F6F8.
- **Level 1 (Cards/Content Blocks):** White (#FFFFFF) with a 1px solid border in #DDE1E6. No shadow.
- **Level 2 (Active/Hover):** White (#FFFFFF) with a 1px solid border in #1F4E6D and a subtle 4px blur, 0% offset shadow to indicate interactivity.
- **Separators:** Use 1px solid lines in #667582 at 20% opacity for table row demarcation.

## Shapes
The system uses **Soft (0.25rem)** roundedness. This provides a subtle modern touch while maintaining a rigid, industrial feel. Sharp corners (0px) are used for technical status tags and table headers to reinforce the "grid" nature of engineering documents. Larger components like Product Cards use the standard 0.25rem radius.

## Components
- **Technical Spec Tables:** Header rows use Primary Navy (#102A43) with white text. Alternate rows use a subtle #F4F6F8 tint. Values are set in JetBrains Mono.
- **RFQ Forms:** Input fields use a 1px border. Focus states use a 2px Engineering Orange (#F58220) border. Labels are always positioned above the field for clarity in complex forms.
- **Product Cards:** Must include a "Quick Spec" area (e.g., Load Capacity, Material, Part #). Use Badge components for "In Stock" or "OEM Certified" status.
- **Buttons:** 
  - *Primary:* Engineering Orange (#F58220), white text, bold weight.
  - *Secondary:* Outline Primary Navy (#102A43), 1px border.
- **Navigation:** A multi-level sidebar for desktop. Level 1 uses Primary Navy; Level 2 uses Industrial Charcoal (#24313A). Active states are indicated by a 4px Engineering Orange left-border accent.
- **Industrial Hero Banners:** Use high-contrast architectural/mechanical photography with a 60% Industrial Charcoal overlay to ensure text legibility.