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

## Still Hermes/default artwork (low-visibility, optional)

- The nsis installer sidebar/header bitmaps and the DMG background use
  electron-builder defaults — not the app icon. Revisit only if branded
  installer chrome is wanted.
