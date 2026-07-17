interface AttentionInboxProps {
  items: Array<Record<string, unknown>>
}

export function AttentionInbox({ items }: AttentionInboxProps) {
  if (items.length === 0) return null
  return (
    <section aria-label="Workflow attention" className="py-3">
      <h2 className="text-sm font-medium">Needs attention</h2>
      <p className="text-sm text-(--ui-text-secondary)">{items.length}</p>
    </section>
  )
}
