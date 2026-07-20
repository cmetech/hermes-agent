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
import { useI18n } from '@/i18n'
import { startWorkflowRun, WorkflowApiError } from '@/lib/hermes-api'
import { Play } from '@/lib/icons'
import type { WorkflowDefinition, WorkflowDefinitionInput, WorkflowDetail } from '@/types/hermes'

import { workflowTrustAllowsRun } from './catalog-run-policy'
import { workflowDetailQueryOptions } from './detail-query'

type FlatInputValue = boolean | number | string | undefined

interface AdmissionError {
  field?: string
  kind: 'compatibility' | 'conflict' | 'coordinator' | 'network' | 'profile' | 'validation'
  message?: string
}

interface AdmittedRun {
  disposition: string
  runId: string
}

export interface ReviewRunDialogProps {
  onClose: () => void
  onRunLocated: (runId: string, disposition: string) => Promise<void> | void
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
  const inputId = useId()
  const errorId = useId()
  const describedBy = error ? errorId : undefined

  if (input.type === 'boolean' && input.required) {
    return (
      <div className="grid gap-1">
        <label className="flex items-center gap-2 text-xs font-medium text-(--ui-text-primary)" htmlFor={inputId}>
          <Checkbox
            aria-describedby={describedBy}
            aria-invalid={Boolean(error)}
            checked={Boolean(value)}
            id={inputId}
            onCheckedChange={checked => onChange(checked === true)}
          />
          {input.name}
        </label>
        {error ? (
          <p className="text-xs text-destructive" id={errorId} role="alert">
            {error}
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
              aria-invalid={Boolean(error)}
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
        {error ? (
          <p className="text-xs text-destructive" id={errorId} role="alert">
            {error}
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
              aria-invalid={Boolean(error)}
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
        {error ? (
          <p className="text-xs text-destructive" id={errorId} role="alert">
            {error}
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
        aria-invalid={Boolean(error)}
        id={inputId}
        onChange={event => onChange(event.target.value)}
        size="sm"
        type={input.type === 'number' ? 'number' : 'text'}
        value={String(value ?? '')}
      />
      {error ? (
        <p className="text-xs text-destructive" id={errorId} role="alert">
          {error}
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

  const submit = async () => {
    if (!detail || submitInFlight.current) {
      return
    }

    const nextFieldErrors: Record<string, string> = {}
    const wireValues: Record<string, string> = {}

    if (!admittedRun) {
      for (const input of detail.inputs) {
        const value = values[input.name]

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

    let run = admittedRun

    try {
      if (!run) {
        const response = await startWorkflowRun(
          {
            catalogSource: workflow.source,
            concurrencyPolicy: 'queue',
            idempotencyKey,
            values: wireValues,
            workflow: workflow.name
          },
          profile
        )

        run = {
          disposition: response.result.admission_disposition,
          runId: response.result.run_id
        }

        if (active.current) {
          setAdmittedRun(run)
        }
      }

      if (active.current) {
        await onRunLocated(run.runId, run.disposition)
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

  const blocked =
    !detail ||
    !detail.coordinator.healthy ||
    detail.compatibility.runnable !== true ||
    !detail.run_support.supported ||
    !workflowTrustAllowsRun(detail.trust_state) ||
    detail.inputs.some(input => input.type === 'enum' && enumValues(detail, input.name).length === 0)

  const runSupportMessage =
    detail && !detail.run_support.supported
      ? detail.source === 'showcase'
        ? copy.workflowRunShowcaseFromCli
        : copy.workflowRunUnsupportedCommand(workflow.name)
      : null

  const errorMessage =
    error && !(error.kind === 'validation' && error.field)
      ? error.kind === 'conflict'
        ? copy.workflowRunConflict
        : error.kind === 'compatibility'
          ? copy.workflowRunIncompatible
          : error.kind === 'coordinator'
            ? copy.workflowRunCoordinatorUnavailable
            : error.kind === 'profile'
              ? copy.workflowRunProfileUnavailable
              : error.kind === 'network'
                ? copy.workflowRunNetworkError
                : error.message || copy.workflowRunValidationError
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
            {runSupportMessage ? (
              <Alert variant="warning">
                <AlertDescription>{runSupportMessage}</AlertDescription>
              </Alert>
            ) : null}
            {!detail.coordinator.healthy ? (
              <Alert variant="warning">
                <AlertDescription>{copy.workflowRunCoordinatorUnavailable}</AlertDescription>
              </Alert>
            ) : null}
            {detail.compatibility.runnable !== true ? (
              <Alert variant="warning">
                <AlertDescription>
                  <p>{copy.workflowRunIncompatible}</p>
                  {(detail.compatibility.findings ?? []).some(finding => finding.blocking) ? (
                    <ul className="list-disc pl-4">
                      {(detail.compatibility.findings ?? [])
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
                      onClick={() => void submit()}
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
            <Button disabled={blocked || submitting} onClick={() => void submit()} type="button">
              {copy.workflowRunStart}
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
