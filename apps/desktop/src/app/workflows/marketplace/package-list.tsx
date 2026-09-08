import { useRef } from 'react'

import { Badge } from '@/components/ui/badge'
import { RowButton } from '@/components/ui/row-button'
import { useI18n } from '@/i18n'
import type { WorkflowMarketplaceCatalogPackage, WorkflowMarketplaceInstalledPackage } from '@/types/hermes'

export interface MarketplacePackageSelection {
  packageId: string
  sourceName: string
}

export interface MarketplacePackageListProps {
  installedPackages: WorkflowMarketplaceInstalledPackage[]
  items: WorkflowMarketplaceCatalogPackage[]
  onSelect: (selection: MarketplacePackageSelection, origin: HTMLButtonElement) => void
  selectedIdentifier: null | string
}

function installedFor(
  item: WorkflowMarketplaceCatalogPackage,
  installedPackages: WorkflowMarketplaceInstalledPackage[]
): WorkflowMarketplaceInstalledPackage | undefined {
  return installedPackages.find(
    installed => installed.identity.source_key === item.source_name && installed.identity.package_id === item.id
  )
}

export function MarketplacePackageList({
  installedPackages,
  items,
  onSelect,
  selectedIdentifier
}: MarketplacePackageListProps) {
  const { t } = useI18n()
  const copy = t.operations
  const itemRefs = useRef(new Map<string, HTMLButtonElement>())

  const moveSelection = (index: number, key: 'ArrowDown' | 'ArrowUp' | 'End' | 'Home') => {
    const targetIndex =
      key === 'Home'
        ? 0
        : key === 'End'
          ? items.length - 1
          : key === 'ArrowDown'
            ? Math.min(items.length - 1, index + 1)
            : Math.max(0, index - 1)

    const target = items[targetIndex]
    const button = target ? itemRefs.current.get(target.identifier) : undefined

    if (target && button) {
      button.focus()
      onSelect({ packageId: target.id, sourceName: target.source_name }, button)
    }
  }

  return (
    <div aria-label={copy.workflowMarketplacePackages} className="space-y-1" role="listbox">
      {items.map((item, index) => {
        const selected = item.identifier === selectedIdentifier
        const installed = installedFor(item, installedPackages)

        return (
          <RowButton
            aria-label={`${item.display_name} ${item.identifier} ${item.publisher}`}
            aria-selected={selected}
            className="w-full rounded-md px-2.5 py-2 text-start outline-none transition-colors hover:bg-(--chrome-action-hover) focus-visible:ring-2 focus-visible:ring-(--ui-accent) data-[selected=true]:bg-(--ui-bg-quaternary)"
            data-marketplace-package={item.identifier}
            data-selected={selected}
            key={item.identifier}
            onClick={event => onSelect({ packageId: item.id, sourceName: item.source_name }, event.currentTarget)}
            onKeyDown={event => {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Home' || event.key === 'End') {
                event.preventDefault()
                moveSelection(index, event.key)
              } else if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onSelect({ packageId: item.id, sourceName: item.source_name }, event.currentTarget)
              }
            }}
            ref={element => {
              if (element) {
                itemRefs.current.set(item.identifier, element)
              } else {
                itemRefs.current.delete(item.identifier)
              }
            }}
            role="option"
            tabIndex={selected || (!selectedIdentifier && index === 0) ? 0 : -1}
          >
            <span className="flex min-w-0 items-baseline justify-between gap-2">
              <span className="min-w-0 break-all text-sm font-medium text-(--ui-text-primary)">
                {item.display_name}
              </span>
              <span className="shrink-0 font-mono text-[0.65rem] text-(--ui-text-tertiary)">v{item.version}</span>
            </span>
            <span className="mt-0.5 block truncate font-mono text-[0.6875rem] text-(--ui-text-secondary)">
              {item.identifier}
            </span>
            <span className="mt-1 block line-clamp-2 text-xs text-(--ui-text-secondary)">{item.description}</span>
            <span className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1">
              <Badge className="max-w-full break-all whitespace-normal" variant="muted">
                {item.publisher}
              </Badge>
              {item.tags.map(tag => (
                <Badge key={tag} size="xs" variant="outline">
                  {tag}
                </Badge>
              ))}
              {item.state === 'stale' ? <Badge variant="warn">{copy.workflowMarketplaceStale}</Badge> : null}
              {installed ? (
                <Badge variant="muted">{copy.workflowMarketplaceInstalledVersion(installed.version)}</Badge>
              ) : null}
            </span>
          </RowButton>
        )
      })}
    </div>
  )
}
