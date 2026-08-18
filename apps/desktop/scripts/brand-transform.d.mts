import type { Plugin } from 'vite'

export function applyBrand(code: string): string
export function brandName(): string
export function brandVitePlugin(options?: { exclude?: RegExp; include?: RegExp }): Plugin
