import type { Locale } from './types'

// OTTO customization (brand-neutral — lives on `base`, flows to every brand):
// which UI languages the language picker offers. This is an ADDITIVE allowlist
// layer so `languages.ts` / `types.ts` stay byte-identical to upstream Hermes —
// upstream can add, remove, or reorder locales without ever conflicting here.
// Mirrors the messaging-channel allowlist pattern (`curation.channels.allow`).
//
// Fail-OPEN: an EMPTY allowlist means "show every locale" (stock Hermes
// behavior). Populate it to restrict the picker.
//
// Currently English-only. To offer another language (e.g. Spanish), add its
// `Locale` id here AND ship its translation file + `LOCALE_OPTIONS` entry
// upstream/in a follow-up — the picker reads the endonym from LOCALE_META
// automatically. Ids not present in the app's `Locale` union are ignored.
export const LOCALE_ALLOWLIST: readonly Locale[] = ['en']

export function isLocaleAllowed(locale: Locale): boolean {
  return LOCALE_ALLOWLIST.length === 0 || LOCALE_ALLOWLIST.includes(locale)
}

// Filters a list of [Locale, meta] picker entries down to the allowlist,
// preserving order. The active locale is always kept so a user whose saved
// language is no longer allowlisted can still see and change it.
export function filterAllowedLocales<T>(
  entries: Array<[Locale, T]>,
  activeLocale?: Locale
): Array<[Locale, T]> {
  if (LOCALE_ALLOWLIST.length === 0) return entries
  return entries.filter(([code]) => isLocaleAllowed(code) || code === activeLocale)
}
