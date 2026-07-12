/**
 * OTTO brand themes — a family of gold/accent-on-dark skins for the OTTO
 * (branded Hermes) desktop. Lives in its own file so it's purely ADDITIVE:
 * presets.ts spreads OTTO_THEMES into BUILTIN_THEMES, so upstream never
 * conflicts here. Brand colors come from the cmetech/oscar-adminui palette.
 *
 * Six themes keep the Settings grid aligned (12 total = even rows in both the
 * 2-col and 3-col layouts). Each defines an explicit light (`colors`) and dark
 * (`darkColors`) variant; only the accent hue changes between family members.
 */
import type { DesktopTheme, DesktopThemeColors } from './types'

// Shared OTTO neutrals — accent-and-backgrounds only, so the brand reads clearly
// without fighting the app's layout. Per-theme code overlays primary/ring/etc.
const DARK: DesktopThemeColors = {
  background: '#0C0C0C',
  foreground: '#FAFAFA',
  card: '#1A1A1A',
  cardForeground: '#FAFAFA',
  muted: '#242424',
  mutedForeground: '#A0A0A0',
  popover: '#242424',
  popoverForeground: '#FAFAFA',
  primary: '#FAD22D',
  primaryForeground: '#0C0C0C',
  secondary: '#242424',
  secondaryForeground: '#FAFAFA',
  accent: '#2E2E2E',
  accentForeground: '#FAFAFA',
  border: '#3A3A3A',
  input: '#3A3A3A',
  ring: '#FAD22D',
  destructive: '#FF3232',
  destructiveForeground: '#FFFFFF',
  sidebarBackground: '#0C0C0C',
  sidebarBorder: '#242424',
  userBubble: '#1A1A1A',
  userBubbleBorder: '#3A3A3A'
}

const LIGHT: DesktopThemeColors = {
  background: '#F4F5FA',
  foreground: '#242424',
  card: '#FFFFFF',
  cardForeground: '#242424',
  muted: '#F2F2F2',
  mutedForeground: '#767676',
  popover: '#FFFFFF',
  popoverForeground: '#242424',
  primary: '#FAD22D',
  primaryForeground: '#0C0C0C',
  secondary: '#F0EFF0',
  secondaryForeground: '#242424',
  accent: '#F0F2F8',
  accentForeground: '#242424',
  border: '#E0E0E0',
  input: '#E0E0E0',
  ring: '#FAD22D',
  destructive: '#FF3232',
  destructiveForeground: '#FFFFFF',
  sidebarBackground: '#FFFFFF',
  sidebarBorder: '#E0E0E0',
  userBubble: '#F2F2F2',
  userBubbleBorder: '#E0E0E0'
}

/** Build one OTTO family member: shared neutrals + a brand accent hue. */
function ottoTheme(
  name: string,
  label: string,
  description: string,
  accent: string,
  onAccent: string
): DesktopTheme {
  const accentTokens = (base: DesktopThemeColors): DesktopThemeColors => ({
    ...base,
    primary: accent,
    primaryForeground: onAccent,
    ring: accent,
    midground: accent,
    composerRing: accent
  })
  return {
    name,
    label,
    description,
    colors: accentTokens(LIGHT),
    darkColors: accentTokens(DARK)
  }
}

// Brand accents from oscar-adminui customColors. onAccent chosen for contrast:
// dark text on bright hues (gold/teal/purple/green/orange), white on blue.
export const ottoGoldTheme = ottoTheme('otto', 'OTTO', 'Gold on black — the OTTO brand', '#FAD22D', '#0C0C0C')
export const ottoTealTheme = ottoTheme('otto-teal', 'OTTO Teal', 'Teal accent on OTTO dark', '#1FA6A6', '#0C0C0C')
export const ottoPurpleTheme = ottoTheme('otto-purple', 'OTTO Purple', 'Purple accent on OTTO dark', '#AF78D2', '#0C0C0C')
export const ottoBlueTheme = ottoTheme('otto-blue', 'OTTO Blue', 'Blue accent on OTTO dark', '#1174E6', '#FFFFFF')
export const ottoGreenTheme = ottoTheme('otto-green', 'OTTO Green', 'Green accent on OTTO dark', '#0FC373', '#0C0C0C')
export const ottoOrangeTheme = ottoTheme('otto-orange', 'OTTO Orange', 'Orange accent on OTTO dark', '#FF8C0A', '#0C0C0C')

/** OTTO family, keyed by name. Spread into BUILTIN_THEMES (OTTO first). */
export const OTTO_THEMES: Record<string, DesktopTheme> = {
  otto: ottoGoldTheme,
  'otto-teal': ottoTealTheme,
  'otto-purple': ottoPurpleTheme,
  'otto-blue': ottoBlueTheme,
  'otto-green': ottoGreenTheme,
  'otto-orange': ottoOrangeTheme
}
