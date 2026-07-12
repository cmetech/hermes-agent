// scripts/brand/skin-palette.mjs
//
// Shared CLI skin palette, copied verbatim from the "otto" skin block in
// hermes_cli/skin_engine.py (_BUILTIN_SKINS["otto"]). This module is the
// single source of truth the (later) skin emitter composes with per-brand
// labels and banner art — do not hand-edit hex values here without also
// updating skin_engine.py, or the two will drift.

export const OTTO_PALETTE = {
  banner_border: '#8A6D1A',
  banner_title: '#FAD22D',
  banner_accent: '#FFDE5C',
  banner_dim: '#5C4E14',
  banner_text: '#FAFAFA',
  ui_accent: '#FAD22D',
  ui_label: '#FFDE5C',
  ui_ok: '#0FC373',
  ui_error: '#FF3232',
  ui_warn: '#FF8C0A',
  prompt: '#FAFAFA',
  input_rule: '#8A6D1A',
  response_border: '#C9A227',
  status_bar_bg: '#1A1500',
  status_bar_text: '#FAFAFA',
  status_bar_strong: '#FAD22D',
  status_bar_dim: '#6E6144',
  status_bar_good: '#0FC373',
  status_bar_warn: '#FAD22D',
  status_bar_bad: '#FF3232',
  status_bar_critical: '#FF3232',
  session_label: '#FAD22D',
  session_border: '#6E6144',
}

export const OTTO_SPINNER = {
  waiting_faces: ['(◎)', '(◉)', '(⊙)', '(◈)', '(◇)'],
  thinking_faces: ['(◉)', '(◎)', '(⊙)', '(◈)', '(◇)'],
  thinking_verbs: [
    'routing', 'spinning up', 'warming the gateway', 'reaching kilo',
    'orchestrating', 'wiring it up', 'queuing', 'syncing',
  ],
  wings: [
    ['⟪◈', '◈⟫'],
    ['⟪◉', '◉⟫'],
    ['⟪⊙', '⊙⟫'],
    ['⟪◇', '◇⟫'],
  ],
}
