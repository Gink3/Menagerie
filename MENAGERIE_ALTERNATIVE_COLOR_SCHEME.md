# Menagerie Design Document Update: Alternative Application Color Scheme

## Global Application Color Scheme (Alternative)

This alternative theme provides a more subdued, museum-inspired appearance. Compared to the primary purple/pink theme, this palette emphasizes neutrality and professionalism while retaining a distinctive purple brand color.

### Primary Colors

| Token | Name | Hex | Intended Use |
|---|---|---:|---|
| `--color-primary` | Velvet Orchid | `#6B3080` | Primary navigation, branding, major headings, selected states |
| `--color-secondary` | Gunmetal | `#444241` | Top bars, side navigation, footer, dark backgrounds, body text in light theme |

Velvet Orchid provides the primary brand identity while Gunmetal grounds the interface with a dark neutral.

### Accent Colors

| Token | Name | Hex | Intended Use |
|---|---|---:|---|
| `--color-accent-grey` | Dust Grey | `#D4D2D5` | Cards, tables, hover backgrounds, subtle highlights |
| `--color-accent-silver` | Silver | `#BFAFA6` | Secondary panels, borders, muted badges, inactive controls |
| `--color-accent-taupe` | Dusty Taupe | `#AA968A` | Warm accents, metadata panels, timeline backgrounds, decorative surfaces |

The overall palette should feel calm, archival, and museum-like, making it especially suitable for historical collections, heraldry, genealogy, and research-oriented interfaces.

### Suggested Semantic Mapping

```css
:root {
  --menagerie-gunmetal: #444241;
  --menagerie-velvet-orchid: #6B3080;
  --menagerie-dust-grey: #D4D2D5;
  --menagerie-silver: #BFAFA6;
  --menagerie-dusty-taupe: #AA968A;

  --color-primary: var(--menagerie-velvet-orchid);
  --color-secondary: var(--menagerie-gunmetal);
  --color-surface: var(--menagerie-dust-grey);
  --color-muted: var(--menagerie-silver);
  --color-accent: var(--menagerie-dusty-taupe);
}
```

### Recommended Interface Usage

- **Application header and navigation:** Gunmetal
- **Branding, active navigation, and primary actions:** Velvet Orchid
- **Cards and content surfaces:** Dust Grey
- **Borders, muted controls, and inactive states:** Silver
- **Metadata panels, timeline elements, and secondary accents:** Dusty Taupe
- **AI status indicators:** Keep a dedicated semantic success color rather than reusing Taupe.

### Accessibility Requirements

- Use white text on Gunmetal and Velvet Orchid after contrast verification.
- Use dark text on Dust Grey, Silver, and Dusty Taupe.
- Do not rely on color alone for state changes or AI status.
- Verify all combinations against WCAG 2.2 AA requirements.

### Relationship to Heraldry Colors

Like the primary Menagerie theme, this application palette is independent of Heraldry Studio colors.

It must remain separate from:

1. Traditional heraldic tinctures.
2. Collection palettes.
3. Recently used heraldry colors.
4. Named custom heraldry colors.

Changing the application theme must never modify stored heraldry designs.
