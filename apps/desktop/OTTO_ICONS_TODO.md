# TODO(otto-icons)

The desktop build still ships the original Hermes icon assets under
`apps/desktop/assets/` (`icon.icns`, `icon.ico`, `icon.png`, etc.) referenced by
`package.json` → `build.icon` and `build.extraResources`.

The app **identity** is fully OTTO (product name, appId `io.cmetech.otto`, window
title, dock, About, installer/DMG/NSIS names), but the **icon artwork** is still
the Hermes mark. Replace the icon files in place (same filenames/paths, so no
`package.json` change is needed) with the OTTO teal "O" mark:

- macOS: `assets/icon.icns` (1024×1024 source)
- Windows: `assets/icon.ico` (multi-resolution) + the `extraResources` copy
- Linux/PNG: `assets/icon.png` and any size variants

Until then the built app is named OTTO but wears the Hermes icon. This does not
block the build.
