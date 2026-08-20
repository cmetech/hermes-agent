const UPSTREAM_PRODUCT_NAME = new RegExp('\\bhermes\\b', 'gi')

export function nativeNotificationTitle(value: unknown, appName: string): string {
  const fallback = appName.trim() || 'Hermes'
  const title = typeof value === 'string' ? value.trim() : ''

  if (!title) {
    return fallback
  }

  if (fallback.toLowerCase() === 'hermes') {
    return title
  }

  return title.replace(UPSTREAM_PRODUCT_NAME, fallback)
}
