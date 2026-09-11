# Tokenograph — Context Loop

**Selected by the owner, 2026-09-05.** The open ring suggests a bounded context window;
three rows suggest token accounting; the separated token suggests movement and reuse.
This is an identity for the personal tool, not a launch announcement. The panel and
existing wordmark have not been changed.

## Assets

| Asset | Use |
|---|---|
| `tokenograph.svg` | Master color mark, transparent, 64×64 viewBox; intended for dark backgrounds |
| `tokenograph-{32,64,256,512}.png` | Raster versions of the transparent color mark |
| `tokenograph-mono.svg` | Single ink using `currentColor` inline; defaults to black as an external image |
| `tokenograph-mono-512.png` | Black mark on transparent background |
| `tokenograph-white.svg`, `tokenograph-white-512.png` | White mark for dark backgrounds |
| `tokenograph-app.svg` | Padded mark on the panel's dark rounded-square tile |
| `tokenograph-app-{256,512,1024}.png` | App/avatar tile at common raster sizes |
| `favicon.svg` | Small-size version: pixel-aligned rows, slightly stronger small-scale contrast, dark tile |
| `favicon-{16,32,48}.png`, `favicon.ico` | Native-size favicons; ICO contains all three resolutions |

The main geometry is preserved from the selected concept. The favicon aligns the ledger
rows to the pixel grid; the app tile adds breathing room rather than changing the symbol.
SVGs contain only vector geometry and accessibility text: no embedded fonts, scripts,
external resources, or application data.

## Usage

- Blue: `#2f8ff5`; mint: `#7ee3b8`; dark tile: `#0b0b0d`.
- Use the black monochrome version on light backgrounds. The mint color is intentionally
  suited to the dark panel, not white paper.
- Keep proportions intact. Use the dedicated favicon at very small sizes.
- The master viewBox includes breathing room; leave at least another stroke-width of
  clear space around the mark in a wordmark lockup.
- `currentColor` inherits from surrounding CSS only when the SVG is inline. For an
  external white image, use the explicit white asset.
- If inserting several inline copies, give their accessibility IDs unique prefixes, or
  use decorative copies with `aria-hidden="true"` beside a visible name.
- Original studies remain in `docs/branding/concepts/`. No trademark clearance has been
  performed and these files do not choose a license for the project.

## Reproduce PNG/ICO exports

The assets are already generated and need no tooling to use. For development-only
regeneration, install librsvg's `rsvg-convert` and run:

```sh
python3 assets/brand/render.py
```

The script uses the Python standard library to invoke the renderer, verify PNG dimensions,
and assemble the multi-size ICO. It is not imported by Tokenograph and adds no runtime
dependency. The SVG files are the editable source of truth.
