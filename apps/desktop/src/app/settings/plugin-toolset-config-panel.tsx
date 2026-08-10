import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  cancelPluginSetupAction,
  clearPluginConfigurationSecret,
  getPluginConfigurations,
  getPluginSetupAction,
  refreshPluginReadiness,
  setPluginConfigurationEnabled,
  startPluginSetupAction,
  updatePluginConfiguration
} from '@/hermes'
import { useI18n } from '@/i18n'
import { Package, RefreshCw } from '@/lib/icons'
import { notifyError } from '@/store/notifications'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import type {
  PluginConfigurationDetail,
  PluginConfigurationField,
  PluginSetupAction,
  PluginSetupActionRun
} from '@/types/hermes'

import { ListRow, Pill, SectionHeading } from './primitives'

function safeDocumentationUrl(value?: string): string | null {
  if (!value) {
    return null
  }

  try {
    const url = new URL(value)

    return (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

function replaceDetail(
  current: PluginConfigurationDetail[],
  next: PluginConfigurationDetail
): PluginConfigurationDetail[] {
  const index = current.findIndex(item => item.plugin_id === next.plugin_id)

  if (index < 0) {
    return [...current, next]
  }

  if (current[index] === next) {
    return current
  }

  const copy = current.slice()
  copy[index] = next

  return copy
}

interface DocumentationLinkProps {
  url?: string
}

function DocumentationLink({ url }: DocumentationLinkProps) {
  const { t } = useI18n()
  const safeUrl = safeDocumentationUrl(url)

  if (!safeUrl) {
    return null
  }

  return (
    <Button asChild size="inline" variant="textStrong">
      <a href={safeUrl} rel="noreferrer" target="_blank">
        {t.common.docs}
      </a>
    </Button>
  )
}

interface FieldEditorProps {
  beginMutation: () => number
  field: PluginConfigurationField
  isLatestMutation: (generation: number) => boolean
  pluginId: string
  onDetail: (detail: PluginConfigurationDetail, generation: number) => void
}

function FieldEditor({ beginMutation, field, isLatestMutation, pluginId, onDetail }: FieldEditorProps) {
  const { t } = useI18n()
  const copy = t.settings.connectorPlugins
  const initial = field.storage === 'setting' && field.value !== undefined ? String(field.value) : ''
  const [value, setValue] = useState(initial)
  const [busy, setBusy] = useState(false)
  const mountedRef = useRef(false)
  const requestRef = useRef(0)
  const docs = <DocumentationLink url={field.documentation_url} />

  // eslint-disable-next-line no-restricted-syntax -- legitimate async-unmount guard; reset in setup for StrictMode
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      requestRef.current += 1
    }
  }, [])

  async function save() {
    const localGeneration = ++requestRef.current
    const pluginGeneration = beginMutation()
    setBusy(true)

    try {
      const parsed =
        field.type === 'boolean'
          ? value === 'true'
          : field.type === 'integer'
            ? Number.parseInt(value, 10)
            : field.type === 'number'
              ? Number(value)
              : value

      const detail = await updatePluginConfiguration(
        pluginId,
        field.storage === 'secret' ? { secrets: { [field.id]: value } } : { settings: { [field.id]: parsed } }
      )

      if (mountedRef.current && requestRef.current === localGeneration && isLatestMutation(pluginGeneration)) {
        onDetail(detail, pluginGeneration)
      }
    } catch (error) {
      if (mountedRef.current && requestRef.current === localGeneration && isLatestMutation(pluginGeneration)) {
        notifyError(error, copy.saveFailed(field.label))
      }
    } finally {
      if (mountedRef.current && requestRef.current === localGeneration && field.storage === 'secret') {
        setValue('')
      }

      if (mountedRef.current && requestRef.current === localGeneration) {
        setBusy(false)
      }
    }
  }

  async function clearSecret() {
    const localGeneration = ++requestRef.current
    const pluginGeneration = beginMutation()
    setBusy(true)

    try {
      const detail = await clearPluginConfigurationSecret(pluginId, field.id)

      if (mountedRef.current && requestRef.current === localGeneration && isLatestMutation(pluginGeneration)) {
        onDetail(detail, pluginGeneration)
      }
    } catch (error) {
      if (mountedRef.current && requestRef.current === localGeneration && isLatestMutation(pluginGeneration)) {
        notifyError(error, copy.clearFailed(field.label))
      }
    } finally {
      if (mountedRef.current && requestRef.current === localGeneration) {
        setValue('')
        setBusy(false)
      }
    }
  }

  async function saveBoolean(checked: boolean) {
    const localGeneration = ++requestRef.current
    const pluginGeneration = beginMutation()
    setValue(String(checked))
    setBusy(true)

    try {
      const detail = await updatePluginConfiguration(pluginId, { settings: { [field.id]: checked } })

      if (mountedRef.current && requestRef.current === localGeneration && isLatestMutation(pluginGeneration)) {
        onDetail(detail, pluginGeneration)
      }
    } catch (error) {
      if (mountedRef.current && requestRef.current === localGeneration && isLatestMutation(pluginGeneration)) {
        setValue(String(!checked))
        notifyError(error, copy.saveFailed(field.label))
      }
    } finally {
      if (mountedRef.current && requestRef.current === localGeneration) {
        setBusy(false)
      }
    }
  }

  if (!['boolean', 'integer', 'number', 'string'].includes(field.type)) {
    return <ListRow description={copy.unsupportedField(field.label)} hint={field.help} title={field.label} />
  }

  const enumValues = field.validation?.enum?.filter(item => ['boolean', 'number', 'string'].includes(typeof item)) ?? []
  const label = field.label
  let control

  if (field.type === 'boolean') {
    control = (
      <Switch
        aria-label={label}
        checked={value === 'true'}
        disabled={busy}
        onCheckedChange={checked => void saveBoolean(checked)}
      />
    )
  } else if (enumValues.length > 0 && field.storage === 'setting') {
    control = (
      <div className="flex items-center gap-2">
        <Select onValueChange={setValue} value={value}>
          <SelectTrigger aria-label={label} size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {enumValues.map(option => (
              <SelectItem key={String(option)} value={String(option)}>
                {String(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button aria-label={copy.saveField(label)} disabled={busy} onClick={() => void save()} size="sm">
          {t.common.save}
        </Button>
      </div>
    )
  } else {
    control = (
      <div className="flex items-center gap-2">
        <Input
          aria-label={label}
          autoComplete={field.storage === 'secret' ? 'new-password' : 'off'}
          disabled={busy}
          maxLength={field.validation?.max_length}
          minLength={field.validation?.min_length}
          onChange={event => setValue(event.target.value)}
          placeholder={field.storage === 'secret' && field.is_set ? copy.secretSet : undefined}
          type={field.storage === 'secret' ? 'password' : field.type === 'string' ? 'text' : 'number'}
          value={value}
        />
        <Button
          aria-label={field.storage === 'secret' ? copy.setField(label) : copy.saveField(label)}
          disabled={busy || value.length === 0}
          onClick={() => void save()}
          size="sm"
        >
          {field.storage === 'secret' ? t.common.set : t.common.save}
        </Button>
        {field.storage === 'secret' && field.is_set && (
          <Button
            aria-label={copy.clearField(label)}
            disabled={busy}
            onClick={() => void clearSecret()}
            size="sm"
            variant="text"
          >
            {t.common.clear}
          </Button>
        )}
      </div>
    )
  }

  return (
    <ListRow
      action={control}
      description={
        <span className="flex flex-wrap items-center gap-2">
          {field.help && <span>{field.help}</span>}
          {docs}
        </span>
      }
      title={
        <span className="flex items-center gap-2">
          {label}
          {field.required && <Pill tone="warn">{copy.required}</Pill>}
          {field.storage === 'secret' && <Pill>{field.is_set ? copy.secretSet : copy.secretNotSet}</Pill>}
        </span>
      }
    />
  )
}

function actionStatusLabel(
  run: PluginSetupActionRun | undefined,
  copy: ReturnType<typeof useI18n>['t']['settings']['connectorPlugins']
) {
  if (!run) {
    return null
  }

  return copy.actionStatuses[run.status]
}

interface SetupActionRowProps {
  action: PluginSetupAction
  pluginId: string
}

function SetupActionRow({ action, pluginId }: SetupActionRowProps) {
  const { t } = useI18n()
  const copy = t.settings.connectorPlugins
  const [run, setRun] = useState<PluginSetupActionRun>()
  const [busy, setBusy] = useState(false)
  const mountedRef = useRef(false)
  const requestRef = useRef(0)

  const applyLatest = useCallback((generation: number, next: PluginSetupActionRun) => {
    if (mountedRef.current && requestRef.current === generation) {
      setRun(current => (current === next ? current : next))
    }
  }, [])

  // eslint-disable-next-line no-restricted-syntax -- legitimate async-unmount guard; reset in setup for StrictMode
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      requestRef.current += 1
    }
  }, [])

  async function start() {
    const generation = ++requestRef.current
    setBusy(true)

    try {
      applyLatest(generation, await startPluginSetupAction(pluginId, action.id))
    } catch (error) {
      if (mountedRef.current && requestRef.current === generation) {
        notifyError(error, copy.actionFailed(action.label))
      }
    } finally {
      if (mountedRef.current && requestRef.current === generation) {
        setBusy(false)
      }
    }
  }

  async function refresh() {
    if (!run) {
      return
    }

    const generation = ++requestRef.current
    setBusy(true)

    try {
      applyLatest(generation, await getPluginSetupAction(run.run_id))
    } catch (error) {
      if (mountedRef.current && requestRef.current === generation) {
        notifyError(error, copy.actionStatusFailed)
      }
    } finally {
      if (mountedRef.current && requestRef.current === generation) {
        setBusy(false)
      }
    }
  }

  async function cancel() {
    if (!run) {
      return
    }

    const generation = ++requestRef.current
    setBusy(true)

    try {
      applyLatest(generation, await cancelPluginSetupAction(run.run_id))
    } catch (error) {
      if (mountedRef.current && requestRef.current === generation) {
        notifyError(error, copy.actionCancelFailed)
      }
    } finally {
      if (mountedRef.current && requestRef.current === generation) {
        setBusy(false)
      }
    }
  }

  const active = run?.status === 'queued' || run?.status === 'running'

  return (
    <ListRow
      action={
        <div className="flex flex-wrap items-center justify-end gap-2">
          {!run && (
            <Button disabled={!action.available || busy} onClick={() => void start()} size="sm">
              {action.label}
            </Button>
          )}
          {run && (
            <Button
              aria-label={copy.checkActionStatus}
              disabled={busy || !active}
              onClick={() => void refresh()}
              size="sm"
              variant="outline"
            >
              {t.common.refresh}
            </Button>
          )}
          {active && (
            <Button
              aria-label={copy.cancelAction}
              disabled={busy}
              onClick={() => void cancel()}
              size="sm"
              variant="text"
            >
              {t.common.cancel}
            </Button>
          )}
        </div>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          {action.help && <span>{action.help}</span>}
          <DocumentationLink url={action.documentation_url} />
          {!action.available && <span>{copy.availableNextSession}</span>}
        </span>
      }
      title={
        <span className="flex items-center gap-2">
          {action.label}
          {run && (
            <Pill
              tone={
                run.status === 'succeeded'
                  ? 'primary'
                  : run.status === 'failed' || run.status === 'timed_out'
                    ? 'warn'
                    : 'muted'
              }
            >
              {actionStatusLabel(run, copy)}
            </Pill>
          )}
        </span>
      }
    />
  )
}

interface ProfileScopedPluginToolsetConfigPanelProps {
  activeProfile: string
}

function ProfileScopedPluginToolsetConfigPanel({ activeProfile }: ProfileScopedPluginToolsetConfigPanelProps) {
  const { t } = useI18n()
  const copy = t.settings.connectorPlugins
  const [details, setDetails] = useState<PluginConfigurationDetail[]>([])
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const mountedRef = useRef(false)
  const catalogGenerationRef = useRef(0)
  const mutationGenerationsRef = useRef(new Map<string, number>())
  const mutationGenerations = mutationGenerationsRef.current

  // eslint-disable-next-line no-restricted-syntax -- legitimate async-unmount guard; reset in setup for StrictMode
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      catalogGenerationRef.current += 1
      mutationGenerations.clear()
    }
  }, [mutationGenerations])

  const load = useCallback(async () => {
    const generation = ++catalogGenerationRef.current
    setLoading(true)
    setLoadFailed(false)

    try {
      const next = await getPluginConfigurations()

      if (mountedRef.current && catalogGenerationRef.current === generation) {
        setDetails(current => {
          if (current.length === next.length && current.every((item, index) => item === next[index])) {
            return current
          }

          return next
        })
      }
    } catch (error) {
      if (mountedRef.current && catalogGenerationRef.current === generation) {
        setLoadFailed(true)
        notifyError(error, copy.loadFailed)
      }
    } finally {
      if (mountedRef.current && catalogGenerationRef.current === generation) {
        setLoading(false)
      }
    }
  }, [copy.loadFailed])

  useEffect(() => {
    void load()
  }, [load])

  const mutationKey = useCallback((pluginId: string) => `${activeProfile}\u0000${pluginId}`, [activeProfile])

  const beginMutation = useCallback(
    (pluginId: string) => {
      const key = mutationKey(pluginId)
      const generation = (mutationGenerationsRef.current.get(key) ?? 0) + 1
      mutationGenerationsRef.current.set(key, generation)

      return generation
    },
    [mutationKey]
  )

  const isLatestMutation = useCallback(
    (pluginId: string, generation: number) =>
      mountedRef.current && mutationGenerationsRef.current.get(mutationKey(pluginId)) === generation,
    [mutationKey]
  )

  const applyDetail = useCallback(
    (next: PluginConfigurationDetail, generation: number) => {
      if (isLatestMutation(next.plugin_id, generation)) {
        setDetails(current => replaceDetail(current, next))
      }
    },
    [isLatestMutation]
  )

  async function toggle(detail: PluginConfigurationDetail, enabled: boolean) {
    const generation = beginMutation(detail.plugin_id)
    setDetails(current => replaceDetail(current, { ...detail, enabled }))

    try {
      const next = await setPluginConfigurationEnabled(detail.plugin_id, enabled)

      applyDetail(next, generation)
    } catch (error) {
      if (isLatestMutation(detail.plugin_id, generation)) {
        applyDetail(detail, generation)
        notifyError(error, copy.toggleFailed(detail.plugin_id))
      }
    }
  }

  async function refreshReadiness(detail: PluginConfigurationDetail) {
    const generation = beginMutation(detail.plugin_id)

    try {
      const readiness = await refreshPluginReadiness(detail.plugin_id)

      if (!isLatestMutation(detail.plugin_id, generation)) {
        return
      }

      setDetails(current =>
        current.map(item => {
          if (item.plugin_id !== detail.plugin_id || item.readiness === readiness) {
            return item
          }

          return { ...item, readiness }
        })
      )
    } catch (error) {
      if (isLatestMutation(detail.plugin_id, generation)) {
        notifyError(error, copy.readinessFailed(detail.plugin_id))
      }
    }
  }

  if (loading) {
    return (
      <section className="mt-8 border-t border-(--ui-stroke-tertiary) pt-6">
        <Loader label={copy.loading} type="lemniscate-bloom" />
      </section>
    )
  }

  return (
    <section className="mt-8 border-t border-(--ui-stroke-tertiary) pt-6">
      <SectionHeading icon={Package} meta={copy.count(details.length)} title={copy.title} />
      <p className="mb-4 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{copy.blurb}</p>
      {loadFailed && (
        <Button onClick={() => void load()} size="sm" variant="outline">
          {t.common.retry}
        </Button>
      )}
      {!loadFailed && details.length === 0 && (
        <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{copy.empty}</p>
      )}
      {details.map(detail => {
        const values = Object.fromEntries(
          detail.fields.filter(field => field.storage === 'setting').map(field => [field.id, field.value])
        )

        const visibleFields = detail.fields.filter(field => {
          if (field.platforms && field.platforms.length > 0 && !field.platforms.includes('desktop')) {
            return false
          }

          if (!field.visible_when) {
            return true
          }

          return values[field.visible_when.field] === field.visible_when.equals
        })

        const reason = detail.readiness.reasons[0]

        const reasonLabel = reason
          ? reason.startsWith('authentication_required:')
            ? copy.authenticationRequired(reason.split(':', 2)[1])
            : reason.startsWith('configuration_required:')
              ? copy.configurationRequired(reason.split(':', 2)[1])
              : reason.startsWith('invalid_configuration:')
                ? copy.invalidConfiguration(reason.split(':', 2)[1])
                : reason.startsWith('setup_required:')
                  ? copy.setupRequired(reason.split(':', 2)[1])
                  : reason === 'plugin_not_enabled'
                    ? copy.disabled
                    : copy.notReady
          : detail.readiness.ready
            ? copy.ready
            : copy.notReady

        return (
          <section className="border-t border-(--ui-stroke-tertiary) py-2 first:border-t-0" key={detail.plugin_id}>
            <ListRow
              action={
                <div className="flex items-center gap-2">
                  <Button
                    aria-label={copy.refreshReadiness(detail.plugin_id)}
                    onClick={() => void refreshReadiness(detail)}
                    size="icon-xs"
                    variant="ghost"
                  >
                    <RefreshCw />
                  </Button>
                  <Switch
                    aria-label={
                      detail.enabled ? copy.disablePlugin(detail.plugin_id) : copy.enablePlugin(detail.plugin_id)
                    }
                    checked={detail.enabled}
                    onCheckedChange={enabled => void toggle(detail, enabled)}
                  />
                </div>
              }
              description={reason === 'plugin_not_enabled' ? undefined : reasonLabel}
              title={
                <span className="flex items-center gap-2">
                  {detail.plugin_id}
                  <Pill tone={detail.enabled ? 'primary' : 'muted'}>
                    {detail.enabled ? copy.enabled : copy.disabled}
                  </Pill>
                  {detail.enabled && (
                    <Pill tone={detail.readiness.ready ? 'primary' : 'warn'}>
                      {detail.readiness.ready ? copy.ready : copy.notReady}
                    </Pill>
                  )}
                </span>
              }
            />
            {visibleFields.map(field => (
              <FieldEditor
                beginMutation={() => beginMutation(detail.plugin_id)}
                field={field}
                isLatestMutation={generation => isLatestMutation(detail.plugin_id, generation)}
                key={`${activeProfile}:${field.id}`}
                onDetail={applyDetail}
                pluginId={detail.plugin_id}
              />
            ))}
            {detail.setup_actions?.map(action => (
              <SetupActionRow action={action} key={`${activeProfile}:${action.id}`} pluginId={detail.plugin_id} />
            ))}
          </section>
        )
      })}
    </section>
  )
}

export function PluginToolsetConfigPanel() {
  const activeProfile = normalizeProfileKey(useStore($activeGatewayProfile))

  return <ProfileScopedPluginToolsetConfigPanel activeProfile={activeProfile} key={activeProfile} />
}
