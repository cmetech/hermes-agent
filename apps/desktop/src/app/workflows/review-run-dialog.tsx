import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { startWorkflowRun, WorkflowApiError } from '@/lib/hermes-api'
import { Play } from '@/lib/icons'
import type { WorkflowDefinition, WorkflowDefinitionInput, WorkflowDetail } from '@/types/hermes'

import {
  desktopWorkflowLanguageLabel,
  desktopWorkflowRunDisabledReason,
  workflowSupportsImmediateRun,
  workflowSupportsScheduledRun,
  workflowTrustAllowsRun
} from './catalog-run-policy'
import { workflowDetailQueryOptions } from './detail-query'

type FlatInputValue = boolean | number | string | undefined

interface AdmissionError {
  field?: string
  kind:
    | 'catalog_source'
    | 'compatibility'
    | 'conflict'
    | 'coordinator'
    | 'network'
    | 'profile'
    | 'schedule'
    | 'showcase_cli'
    | 'showcase_verification'
    | 'validation'
  message?: string
}

interface AdmittedRun {
  disposition: string
  runId: string
  scheduled: boolean
}

export interface ReviewRunDialogProps {
  onClose: () => void
  onRunLocated: (runId: string, disposition: string, scheduled: boolean) => Promise<void> | void
  profile: null | string
  returnFocusTo?: HTMLElement | null
  workflow: WorkflowDefinition
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function enumValues(detail: WorkflowDetail, name: string): string[] {
  const inputs = asRecord(detail.definition.inputs)
  const specification = asRecord(inputs?.[name])

  for (const key of ['values', 'enum', 'options', 'choices']) {
    const values = specification?.[key]

    if (Array.isArray(values) && values.every(value => ['boolean', 'number', 'string'].includes(typeof value))) {
      return values.map(String)
    }
  }

  return []
}

function admissionError(error: unknown): AdmissionError {
  if (!(error instanceof WorkflowApiError)) {
    return { kind: 'network' }
  }

  const rawDetail = asRecord(error.body)?.detail
  const validation = Array.isArray(rawDetail) ? asRecord(rawDetail[0]) : null
  const detail = asRecord(rawDetail) ?? asRecord(asRecord(error.body)?.error)
  const location = Array.isArray(validation?.loc) ? validation.loc : []
  const validationField = [...location].reverse().find(value => typeof value === 'string' && value !== 'body')
  const validationMessage = typeof validation?.msg === 'string' ? validation.msg : undefined
  const message = typeof detail?.message === 'string' ? detail.message : undefined
  const field = typeof detail?.field === 'string' ? detail.field : undefined

  if (error.status === 409 && error.code === 'idempotency_conflict') {
    return { kind: 'conflict' }
  }

  if (error.status === 503 || error.code === 'coordinator_unavailable') {
    return { kind: 'coordinator' }
  }

  if (error.code === 'workflow_compatibility_blocked') {
    return { kind: 'compatibility' }
  }

  if (error.code === 'workflow_schedule_invalid' || error.code === 'workflow_schedule_required') {
    return { kind: 'schedule' }
  }

  if (error.code === 'workflow_showcase_cli_required') {
    return { kind: 'showcase_cli' }
  }

  if (error.code === 'workflow_showcase_verification_failed') {
    return { kind: 'showcase_verification' }
  }

  if (error.code === 'workflow_catalog_source_invalid') {
    return { kind: 'catalog_source' }
  }

  return {
    field: typeof validationField === 'string' ? validationField : field,
    kind: 'validation',
    message: validationMessage ?? message
  }
}

function initialValues(inputs: readonly WorkflowDefinitionInput[]): Record<string, FlatInputValue> {
  return Object.fromEntries(
    inputs.map(input => [input.name, input.required ? (input.type === 'boolean' ? false : '') : undefined])
  )
}

export function canonicalWorkflowScheduleAt(value: string, now = new Date()): string | null {
  const matched =
    /^(?<year>\d{4})-(?<month>\d{2})-(?<day>\d{2})T(?<hour>\d{2}):(?<minute>\d{2})(?::(?<second>\d{2}))?$/.exec(value)

  if (!matched?.groups) {
    return null
  }

  const year = Number(matched.groups.year)
  const month = Number(matched.groups.month) - 1
  const day = Number(matched.groups.day)
  const hour = Number(matched.groups.hour)
  const minute = Number(matched.groups.minute)
  const second = Number(matched.groups.second ?? '0')
  const instant = new Date(year, month, day, hour, minute, second)

  if (
    !Number.isFinite(instant.getTime()) ||
    instant.getFullYear() !== year ||
    instant.getMonth() !== month ||
    instant.getDate() !== day ||
    instant.getHours() !== hour ||
    instant.getMinutes() !== minute ||
    instant.getSeconds() !== second ||
    instant.getTime() <= now.getTime()
  ) {
    return null
  }

  return instant.toISOString().replace('.000Z', 'Z')
}

function PreflightLoader({ label }: { label: string }) {
  return (
    <div aria-label={label} className="grid min-h-40 place-items-center" role="status">
      <Loader aria-hidden className="size-9 text-primary/70" role="presentation" type="lemniscate-bloom" />
    </div>
  )
}

function InputField({
  detail,
  error,
  input,
  onChange,
  value
}: {
  detail: WorkflowDetail
  error?: string
  input: WorkflowDefinitionInput
  onChange: (value: FlatInputValue) => void
  value: FlatInputValue
}) {
  const { t } = useI18n()
  const copy = t.operations
  const inputId = useId()
  const counterId = useId()
  const errorId = useId()
  const byteCount = input.type === 'text' ? new TextEncoder().encode(String(value ?? '')).byteLength : 0
  const byteLimit = input.type === 'text' && typeof input.max_bytes === 'number' ? input.max_bytes : null
  const overByteLimit = byteLimit !== null && byteCount > byteLimit
  const fieldError = error ?? (overByteLimit ? copy.workflowRunInputTooLarge(input.name, byteLimit) : undefined)

  const describedBy =
    [byteLimit !== null ? counterId : null, fieldError ? errorId : null].filter(Boolean).join(' ') || undefined

  if (input.type === 'file') {
    return (
      <div aria-labelledby={inputId} className="grid gap-1" role="group">
        <span className="text-xs font-medium text-(--ui-text-primary)" id={inputId}>
          {input.name}
        </span>
        <p className="rounded-[2.5px] border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-2 py-1 text-xs text-(--ui-text-secondary)">
          {copy.workflowRunBundledFixture}
        </p>
      </div>
    )
  }

  if (input.type === 'boolean' && input.required) {
    return (
      <div className="grid gap-1">
        <label className="flex items-center gap-2 text-xs font-medium text-(--ui-text-primary)" htmlFor={inputId}>
          <Checkbox
            aria-describedby={describedBy}
            aria-invalid={Boolean(fieldError)}
            checked={Boolean(value)}
            id={inputId}
            onCheckedChange={checked => onChange(checked === true)}
          />
          {input.name}
        </label>
        {fieldError ? (
          <p className="text-xs text-destructive" id={errorId} role="alert">
            {fieldError}
          </p>
        ) : null}
      </div>
    )
  }

  if (input.type === 'boolean') {
    return (
      <div className="grid gap-1">
        <label className="text-xs font-medium text-(--ui-text-primary)" htmlFor={inputId}>
          {input.name}
        </label>
        <div className="flex items-center gap-2">
          <Select
            onValueChange={next => onChange(next === '' ? undefined : next === 'true')}
            value={value === undefined ? '' : String(value)}
          >
            <SelectTrigger
              aria-describedby={describedBy}
              aria-invalid={Boolean(fieldError)}
              aria-label={input.name}
              id={inputId}
              size="sm"
            >
              <SelectValue placeholder={t.common.notSet} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true">{t.common.on}</SelectItem>
              <SelectItem value="false">{t.common.off}</SelectItem>
            </SelectContent>
          </Select>
          {value !== undefined ? (
            <Button
              aria-label={`${t.common.clear} ${input.name}`}
              onClick={() => onChange(undefined)}
              size="xs"
              type="button"
              variant="ghost"
            >
              {t.common.notSet}
            </Button>
          ) : null}
        </div>
        {fieldError ? (
          <p className="text-xs text-destructive" id={errorId} role="alert">
            {fieldError}
          </p>
        ) : null}
      </div>
    )
  }

  if (input.type === 'enum') {
    const options = enumValues(detail, input.name)

    return (
      <div className="grid gap-1">
        <label className="text-xs font-medium text-(--ui-text-primary)" htmlFor={inputId}>
          {input.name}
        </label>
        <div className="flex items-center gap-2">
          <Select
            onValueChange={next => onChange(next === '' ? undefined : next)}
            value={value === undefined ? '' : String(value)}
          >
            <SelectTrigger
              aria-describedby={describedBy}
              aria-invalid={Boolean(fieldError)}
              aria-label={input.name}
              id={inputId}
              size="sm"
            >
              <SelectValue placeholder={input.required ? undefined : t.common.notSet} />
            </SelectTrigger>
            <SelectContent>
              {options.map(option => (
                <SelectItem key={option} value={option}>
                  {option}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!input.required && value !== undefined ? (
            <Button
              aria-label={`${t.common.clear} ${input.name}`}
              onClick={() => onChange(undefined)}
              size="xs"
              type="button"
              variant="ghost"
            >
              {t.common.notSet}
            </Button>
          ) : null}
        </div>
        {fieldError ? (
          <p className="text-xs text-destructive" id={errorId} role="alert">
            {fieldError}
          </p>
        ) : null}
      </div>
    )
  }

  if (input.type === 'text') {
    return (
      <div className="grid gap-1">
        <label className="text-xs font-medium text-(--ui-text-primary)" htmlFor={inputId}>
          {input.name}
        </label>
        <Textarea
          aria-describedby={describedBy}
          aria-invalid={Boolean(fieldError)}
          id={inputId}
          onChange={event => onChange(event.target.value)}
          size="sm"
          value={String(value ?? '')}
        />
        {byteLimit !== null ? (
          <p
            aria-live="polite"
            className={overByteLimit ? 'text-xs text-destructive' : 'text-xs text-(--ui-text-tertiary)'}
            id={counterId}
          >
            {copy.workflowRunInputBytes(byteCount, byteLimit)}
          </p>
        ) : null}
        {fieldError ? (
          <p className="text-xs text-destructive" id={errorId} role="alert">
            {fieldError}
          </p>
        ) : null}
      </div>
    )
  }

  return (
    <div className="grid gap-1">
      <label className="text-xs font-medium text-(--ui-text-primary)" htmlFor={inputId}>
        {input.name}
      </label>
      <Input
        aria-describedby={describedBy}
        aria-invalid={Boolean(fieldError)}
        id={inputId}
        onChange={event => onChange(event.target.value)}
        size="sm"
        type={input.type === 'number' ? 'number' : 'text'}
        value={String(value ?? '')}
      />
      {fieldError ? (
        <p className="text-xs text-destructive" id={errorId} role="alert">
          {fieldError}
        </p>
      ) : null}
    </div>
  )
}

export function ReviewRunDialog({ onClose, onRunLocated, profile, returnFocusTo, workflow }: ReviewRunDialogProps) {
  const { t } = useI18n()
  const copy = t.operations
  const queryClient = useQueryClient()
  const [detail, setDetail] = useState<WorkflowDetail | null>(null)
  const [preflightError, setPreflightError] = useState(false)
  const [preflightAttempt, setPreflightAttempt] = useState(0)
  const [values, setValues] = useState<Record<string, FlatInputValue>>({})
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [scheduleAt, setScheduleAt] = useState('')
  const [scheduleError, setScheduleError] = useState(false)
  const [retryScheduleAt, setRetryScheduleAt] = useState<string | null>(null)
  const [error, setError] = useState<AdmissionError | null>(null)
  const [admittedRun, setAdmittedRun] = useState<AdmittedRun | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const active = useRef(true)

  const focusTarget = useRef(
    returnFocusTo ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
  )

  const submitInFlight = useRef(false)
  const [idempotencyKey] = useState(() => globalThis.crypto.randomUUID())

  useEffect(() => {
    active.current = true
    setDetail(null)
    setPreflightError(false)
    let current = true

    void queryClient.fetchQuery(workflowDetailQueryOptions(workflow.name, workflow.source, profile)).then(
      next => {
        if (!current || !active.current) {
          return
        }

        setDetail(next)
        setValues(initialValues(next.inputs))
      },
      () => {
        if (current && active.current) {
          setPreflightError(true)
        }
      }
    )

    return () => {
      current = false
      active.current = false
    }
  }, [preflightAttempt, profile, queryClient, workflow.name, workflow.source])

  const submit = async (scheduled = false, canonicalRetry?: string) => {
    if (!detail || submitInFlight.current) {
      return
    }

    const canonicalScheduleAt = scheduled ? (canonicalRetry ?? canonicalWorkflowScheduleAt(scheduleAt)) : undefined

    if (scheduled && !canonicalScheduleAt) {
      setScheduleError(true)

      return
    }

    const nextFieldErrors: Record<string, string> = {}
    const wireValues: Record<string, string> = {}

    if (!admittedRun) {
      for (const input of detail.inputs) {
        if (input.type === 'file') {
          continue
        }

        const value = values[input.name]

        if (
          input.type === 'text' &&
          typeof input.max_bytes === 'number' &&
          new TextEncoder().encode(String(value ?? '')).byteLength > input.max_bytes
        ) {
          nextFieldErrors[input.name] = copy.workflowRunInputTooLarge(input.name, input.max_bytes)

          continue
        }

        if (!input.required && value === undefined) {
          continue
        }

        if (input.required && input.type !== 'boolean' && String(value ?? '').trim() === '') {
          nextFieldErrors[input.name] = copy.workflowRunRequiredInput(input.name)

          continue
        }

        wireValues[input.name] = String(value ?? '')
      }
    }

    if (Object.keys(nextFieldErrors).length > 0) {
      setFieldErrors(nextFieldErrors)

      return
    }

    submitInFlight.current = true
    setSubmitting(true)
    setError(null)
    setFieldErrors({})
    setScheduleError(false)
    setRetryScheduleAt(canonicalScheduleAt ?? null)

    let run = admittedRun

    try {
      if (!run) {
        const response = await startWorkflowRun(
          {
            catalogSource: workflow.source,
            concurrencyPolicy: 'queue',
            idempotencyKey,
            ...(canonicalScheduleAt ? { scheduleAt: canonicalScheduleAt } : {}),
            values: wireValues,
            workflow: workflow.name
          },
          profile
        )

        run = {
          disposition: response.result.admission_disposition,
          runId: response.result.run_id,
          scheduled: canonicalScheduleAt !== undefined
        }

        if (active.current) {
          setAdmittedRun(run)
        }
      }

      if (active.current) {
        await onRunLocated(run.runId, run.disposition, run.scheduled)
      }
    } catch (caught) {
      if (!active.current) {
        return
      }

      const parsed = run ? { kind: 'profile' as const } : admissionError(caught)

      const next =
        parsed.field && !detail.inputs.some(input => input.name === parsed.field)
          ? { ...parsed, field: undefined }
          : parsed

      setError(next)

      if (next.field && next.message) {
        setFieldErrors({ [next.field]: next.message })
      }
    } finally {
      submitInFlight.current = false

      if (active.current) {
        setSubmitting(false)
      }
    }
  }

  const retryPreflight = () => setPreflightAttempt(attempt => attempt + 1)

  const close = () => {
    if (submitting) {
      return
    }

    const target = focusTarget.current
    onClose()
    queueMicrotask(() => target?.focus())
  }

  const runSupport = detail?.run_support

  const runDisabledReason = detail ? desktopWorkflowRunDisabledReason(detail, copy, 'detail') : null

  const runSupportMessage =
    runDisabledReason === copy.workflowRunSupportUnavailable ||
    runDisabledReason === copy.workflowRunUnsupportedInputs ||
    runDisabledReason === copy.workflowRunShowcaseFromCli
      ? runDisabledReason
      : null

  const commonBlocked =
    !detail ||
    Boolean(runDisabledReason) ||
    detail.inputs.some(
      input =>
        input.type === 'text' &&
        typeof input.max_bytes === 'number' &&
        new TextEncoder().encode(String(values[input.name] ?? '')).byteLength > input.max_bytes
    ) ||
    detail.inputs.some(input => input.type === 'enum' && enumValues(detail, input.name).length === 0)

  const immediateBlocked = commonBlocked || !workflowSupportsImmediateRun(runSupport)
  const scheduledBlocked = commonBlocked || !workflowSupportsScheduledRun(runSupport)

  const admissionErrorMessages: Partial<Record<AdmissionError['kind'], string>> = {
    catalog_source: copy.workflowRunCatalogSourceInvalid,
    compatibility: copy.workflowRunIncompatible,
    conflict: copy.workflowRunConflict,
    coordinator: copy.workflowRunCoordinatorUnavailable,
    network: copy.workflowRunNetworkError,
    profile: copy.workflowRunProfileUnavailable,
    schedule: copy.workflowRunScheduleInvalid,
    showcase_cli: copy.workflowRunShowcaseFromCli,
    showcase_verification: copy.workflowRunShowcaseVerificationFailed
  }

  const errorMessage =
    error && !(error.kind === 'validation' && error.field)
      ? admissionErrorMessages[error.kind] || error.message || copy.workflowRunValidationError
      : null

  return (
    <Dialog onOpenChange={open => !open && !submitting && close()} open>
      <DialogContent
        aria-busy={submitting}
        className="sm:max-w-xl"
        onEscapeKeyDown={event => submitting && event.preventDefault()}
        onInteractOutside={event => submitting && event.preventDefault()}
        showCloseButton={!submitting}
      >
        <DialogHeader>
          <DialogTitle icon={Play}>{copy.workflowReviewRunTitle(workflow.name)}</DialogTitle>
          <DialogDescription>{copy.workflowReviewRunDescription}</DialogDescription>
        </DialogHeader>
        {!detail && !preflightError ? <PreflightLoader label={copy.workflowRunPreflightLoading} /> : null}
        {preflightError ? (
          <ErrorState
            description={copy.workflowRunPreflightErrorDescription}
            title={copy.workflowRunPreflightErrorTitle}
          >
            <Button onClick={retryPreflight} size="sm" type="button" variant="secondary">
              {copy.workflowRunRetry}
            </Button>
          </ErrorState>
        ) : null}
        {detail ? (
          <>
            <p className="text-sm leading-relaxed text-(--ui-text-secondary)">{detail.description}</p>
            <section className="grid gap-2 border-t border-(--ui-stroke-tertiary) pt-3">
              <h2 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowRunTrust}</h2>
              <Badge variant={workflowTrustAllowsRun(detail.trust_state) ? 'default' : 'destructive'}>
                {detail.trust_state === 'verified_bundled'
                  ? copy.workflowVerifiedBundle
                  : detail.trust_state === 'trusted'
                    ? copy.workflowTrusted
                    : copy.workflowUntrusted}
              </Badge>
              {detail.language ? (
                <div className="flex flex-wrap items-center gap-2 text-xs text-(--ui-text-secondary)">
                  <Badge variant={detail.language.legacy ? 'muted' : 'default'}>
                    {desktopWorkflowLanguageLabel(detail.language, copy)}
                  </Badge>
                  {detail.language.normalizer_version !== undefined ? (
                    <span>
                      {copy.workflowLanguageNormalizer} {detail.language.normalizer_version}
                    </span>
                  ) : null}
                  {detail.language.normalized_definition_digest ? (
                    <span className="flex items-center gap-1">
                      <span>{copy.workflowLanguageDigest}</span>
                      <span className="font-mono" title={detail.language.normalized_definition_digest}>
                        {detail.language.normalized_definition_digest.slice(0, 12)}…
                      </span>
                    </span>
                  ) : null}
                </div>
              ) : null}
            </section>
            <section className="grid gap-2 border-t border-(--ui-stroke-tertiary) pt-3">
              <h2 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowRunRisk}</h2>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-(--ui-text-secondary)">
                {Object.entries(detail.risk_summary).map(([key, value]) => (
                  <div className="contents" key={key}>
                    <dt className="font-medium text-(--ui-text-primary)">{key.replaceAll('_', ' ')}</dt>
                    <dd>
                      {typeof value === 'string' || typeof value === 'number' ? String(value) : JSON.stringify(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
            {!admittedRun && detail.inputs.length > 0 && detail.supported_inputs.supported ? (
              <fieldset className="grid gap-3 border-t border-(--ui-stroke-tertiary) pt-3">
                <legend className="mb-2 text-xs font-medium text-(--ui-text-primary)">{copy.workflowRunInputs}</legend>
                {detail.inputs.map(input => (
                  <InputField
                    detail={detail}
                    error={fieldErrors[input.name]}
                    input={input}
                    key={input.name}
                    onChange={value => setValues(current => ({ ...current, [input.name]: value }))}
                    value={values[input.name]}
                  />
                ))}
              </fieldset>
            ) : null}
            {!admittedRun ? (
              <section className="grid gap-1 border-t border-(--ui-stroke-tertiary) pt-3">
                <label className="text-xs font-medium text-(--ui-text-primary)" htmlFor="workflow-run-at">
                  {copy.workflowRunAt}
                </label>
                <Input
                  aria-describedby={scheduleError ? 'workflow-run-at-error' : undefined}
                  aria-invalid={scheduleError}
                  id="workflow-run-at"
                  onChange={event => {
                    setScheduleAt(event.target.value)
                    setScheduleError(false)
                  }}
                  size="sm"
                  type="datetime-local"
                  value={scheduleAt}
                />
                <p className="text-xs text-(--ui-text-tertiary)">{copy.workflowRunLaterDescription}</p>
                {scheduleError ? (
                  <p className="text-xs text-destructive" id="workflow-run-at-error" role="alert">
                    {copy.workflowRunScheduleInvalid}
                  </p>
                ) : null}
              </section>
            ) : null}
            {runSupportMessage ? (
              <Alert variant="warning">
                <AlertDescription>{runSupportMessage}</AlertDescription>
              </Alert>
            ) : null}
            {detail.coordinator?.healthy !== true ? (
              <Alert variant="warning">
                <AlertDescription>{copy.workflowRunCoordinatorUnavailable}</AlertDescription>
              </Alert>
            ) : null}
            {detail.compatibility?.runnable !== true ? (
              <Alert variant="warning">
                <AlertDescription>
                  <p>{copy.workflowRunIncompatible}</p>
                  {(detail.compatibility?.findings ?? []).some(finding => finding.blocking) ? (
                    <ul className="list-disc pl-4">
                      {(detail.compatibility?.findings ?? [])
                        .filter(finding => finding.blocking)
                        .map(finding => (
                          <li key={`${finding.path}:${finding.code}`}>{finding.message}</li>
                        ))}
                    </ul>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}
            {errorMessage ? (
              <Alert variant={error?.kind === 'coordinator' ? 'warning' : 'destructive'}>
                <AlertDescription>
                  <p>{errorMessage}</p>
                  {error?.kind === 'coordinator' || error?.kind === 'network' || error?.kind === 'profile' ? (
                    <Button
                      disabled={submitting}
                      onClick={() => void submit(retryScheduleAt !== null, retryScheduleAt ?? undefined)}
                      size="xs"
                      type="button"
                      variant="secondary"
                    >
                      {copy.workflowRunRetry}
                    </Button>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}
          </>
        ) : null}
        {!admittedRun ? (
          <DialogFooter>
            <Button
              disabled={scheduledBlocked || submitting}
              onClick={() => void submit(true)}
              type="button"
              variant="secondary"
            >
              {copy.workflowRunLater}
            </Button>
            <Button disabled={immediateBlocked || submitting} onClick={() => void submit()} type="button">
              {copy.workflowRunStart}
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
