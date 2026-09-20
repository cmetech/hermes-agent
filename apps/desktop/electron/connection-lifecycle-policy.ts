import { DEFAULT_BACKEND_READY_TIMEOUT_MS } from './backend-health'
import { DEFAULT_PROBE_TIMEOUT_MS } from './backend-probes'
import { resolvePortAnnounceTimeoutMs } from './backend-ready'
import {
  DEFAULT_CONNECT_TIMEOUT_MS as DEFAULT_GATEWAY_WS_CONNECT_TIMEOUT_MS,
  DEFAULT_READY_GRACE_MS as DEFAULT_GATEWAY_WS_READY_GRACE_MS
} from './gateway-ws-probe'
import {
  DEFAULT_CONNECT_TIMEOUT_MS as DEFAULT_SSH_CONNECT_TIMEOUT_MS,
  DEFAULT_EXEC_TIMEOUT_MS as DEFAULT_SSH_EXEC_TIMEOUT_MS,
  DEFAULT_FORWARD_TIMEOUT_MS as DEFAULT_SSH_FORWARD_TIMEOUT_MS
} from './ssh-connection'

const RUNTIME_PROBE_ATTEMPTS = 2
const DEADLINE_SETTLEMENT_MARGIN_MS = 5_000
const IPC_DELIVERY_MARGIN_MS = 15_000

export interface ConnectionLifecyclePolicy {
  attemptTimeoutMs: number
  ipcDeliveryMarginMs: number
  localAttemptTimeoutMs: number
  portAnnounceTimeoutMs: number
  preloadWatchdogMs: number
  remoteAttemptTimeoutMs: number
}

export function resolveConnectionLifecyclePolicy(
  env: NodeJS.ProcessEnv = process.env,
  _platform: NodeJS.Platform = process.platform
): ConnectionLifecyclePolicy {
  const portAnnounceTimeoutMs = resolvePortAnnounceTimeoutMs(env)
  const runtimeProbeTimeoutMs = DEFAULT_PROBE_TIMEOUT_MS * RUNTIME_PROBE_ATTEMPTS
  const gatewayProofTimeoutMs = DEFAULT_GATEWAY_WS_CONNECT_TIMEOUT_MS + DEFAULT_GATEWAY_WS_READY_GRACE_MS

  const localAttemptTimeoutMs =
    runtimeProbeTimeoutMs +
    portAnnounceTimeoutMs +
    DEFAULT_BACKEND_READY_TIMEOUT_MS +
    gatewayProofTimeoutMs +
    DEADLINE_SETTLEMENT_MARGIN_MS

  const remoteAttemptTimeoutMs =
    runtimeProbeTimeoutMs +
    DEFAULT_SSH_CONNECT_TIMEOUT_MS +
    DEFAULT_SSH_EXEC_TIMEOUT_MS +
    DEFAULT_SSH_FORWARD_TIMEOUT_MS +
    DEFAULT_BACKEND_READY_TIMEOUT_MS +
    gatewayProofTimeoutMs +
    DEADLINE_SETTLEMENT_MARGIN_MS

  const attemptTimeoutMs = Math.max(localAttemptTimeoutMs, remoteAttemptTimeoutMs)

  return {
    attemptTimeoutMs,
    ipcDeliveryMarginMs: IPC_DELIVERY_MARGIN_MS,
    localAttemptTimeoutMs,
    portAnnounceTimeoutMs,
    preloadWatchdogMs: attemptTimeoutMs + IPC_DELIVERY_MARGIN_MS,
    remoteAttemptTimeoutMs
  }
}
