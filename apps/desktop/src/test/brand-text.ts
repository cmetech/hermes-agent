import { applyBrand } from '../../scripts/brand-transform.mjs'

/** Apply the active desktop build's display-brand rules to an expected UI string. */
export const brandText = (text: string): string => applyBrand(text)
