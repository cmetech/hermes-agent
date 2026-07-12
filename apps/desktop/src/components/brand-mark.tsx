import { cn } from '@/lib/utils'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

// OTTO brand badge: the Ericsson mark, theme-aware. The app toggles a `.dark`
// class on <html> (see src/themes/context.tsx), so we render both variants and
// let CSS pick: the black mark on light surfaces, the white mark on dark ones —
// no white tile, so it sits cleanly on the themed panel. Size via className
// (default size-14). See the OTTO customization surface in CLAUDE.md.
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center', className)} {...props}>
      <img alt="" className="size-full object-contain dark:hidden" src={assetPath('ericsson-logo-light.png')} />
      <img alt="" className="hidden size-full object-contain dark:block" src={assetPath('ericsson-logo-dark.png')} />
    </span>
  )
}
