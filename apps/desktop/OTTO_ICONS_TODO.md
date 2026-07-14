# OTTO icons — DONE

The desktop icon artwork is now the **OTTO/Ericsson** mark (was the Hermes mark).
The icon is the Ericsson three-stripe mark (white) on a rounded OTTO-dark
(`#0C0C0C`) square. Files replaced **in place** (same paths, so no
`package.json` change):

- `assets/icon.icns` — macOS app icon (Dock, Finder, ⌘-Tab, DMG, About)
- `assets/icon.ico` — Windows app icon (taskbar, title bar, Start menu, nsis
  installer, Add/Remove Programs) + the `extraResources` copy
- `assets/icon.png` — Linux app icon + 1024² master
- `public/apple-touch-icon.png` — the runtime icon (`app.dock.setIcon()` on macOS)

## Source + how to regenerate

Vector source: **`assets/otto-icon-source.svg`** (the Ericsson three stripes on
the rounded dark square).

Regenerate the full set from the SVG (needs `librsvg`, `imagemagick`; macOS
`iconutil`/`sips`):

```bash
cd apps/desktop/assets
rsvg-convert -w 1024 -h 1024 otto-icon-source.svg -o /tmp/m.png
# .icns
rm -rf /tmp/otto.iconset && mkdir /tmp/otto.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s /tmp/m.png --out /tmp/otto.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) /tmp/m.png --out /tmp/otto.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns /tmp/otto.iconset -o icon.icns
# .ico + pngs
magick /tmp/m.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico
cp /tmp/m.png icon.png && cp /tmp/m.png ../public/apple-touch-icon.png
```

## loop24 branch — brand overlay (this branch)

On the `loop24` branch the icon + in-app mark artwork is the **loop24** blue
paint-stroke "24" mark, replaced **in place** at the same paths (no
`package.json` / code change — `brand-mark.tsx` still points at the
`ericsson-logo-*.png` filenames, which here hold loop24 art):

- `assets/icon.{icns,ico,png}` + `public/apple-touch-icon.png` — the app/OS/
  installer icon (loop24 mark on the rounded `#0C0C0C` tile).
- `public/ericsson-logo-{light,dark}.png` — the in-app `BrandMark` (About,
  updates overlay, install overlay). Two tuned variants: `-dark` uses the
  bright gradient (`#8EC2F7 → #4D97ED → #0F5FBF`) for dark surfaces; `-light`
  uses a deeper gradient (`#3F86DB → #1466C4 → #0A3D77`) for stronger contrast
  on the light surface (`#F4F5FA`).

Vector source: **`assets/loop24-icon-source.svg`** (the loop24 mark on the
rounded dark square). Bare mark (no tile) lives in the workspace as
`loop24.svg`; the `-light` in-app variant is the same mark with the deeper
gradient above. Regenerate the full icon set exactly as above, substituting
`loop24-icon-source.svg` for `otto-icon-source.svg`.

## Still Hermes/default artwork (low-visibility, optional)

- The nsis installer sidebar/header bitmaps and the DMG background use
  electron-builder defaults — not the app icon. Revisit only if branded
  installer chrome is wanted.
