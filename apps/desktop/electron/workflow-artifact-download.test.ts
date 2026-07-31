import { EventEmitter } from 'node:events'

import { describe, expect, it, vi } from 'vitest'

import {
  collectWorkflowArtifactResponse,
  downloadWorkflowArtifactWithDeps,
  WORKFLOW_ARTIFACT_MAX_BYTES,
  workflowArtifactAuthHeaders,
  type WorkflowArtifactDownloadDeps
} from './workflow-artifact-download'

function deps(overrides: Partial<WorkflowArtifactDownloadDeps> = {}): WorkflowArtifactDownloadDeps {
  return {
    chooseSavePath: vi.fn().mockResolvedValue('/tmp/diagnostic.json'),
    ensureBackend: vi.fn().mockResolvedValue({
      authMode: 'token',
      baseUrl: 'http://127.0.0.1:8899',
      token: 'local-session-token'
    }),
    fetchResource: vi.fn().mockResolvedValue({
      bytes: new TextEncoder().encode('{"ok":true}'),
      headers: {
        'content-disposition': 'attachment; filename="diagnostic.json"',
        'content-type': 'application/json'
      }
    }),
    resolveOauthAuth: vi.fn(),
    routePath: vi.fn(path => path),
    writeFile: vi.fn().mockResolvedValue(undefined),
    ...overrides
  }
}

const request = {
  path: '/api/plugins/workflow/runs/run%20%2F%20one/artifacts/publication%20%2F%20opaque/download',
  profile: 'remote-profile'
}

describe('downloadWorkflowArtifactWithDeps', () => {
  it('maps static and native OAuth credentials to the established request headers', () => {
    expect(workflowArtifactAuthHeaders({ kind: 'token', token: 'local-session-token' })).toEqual({
      'X-Hermes-Session-Token': 'local-session-token'
    })
    expect(workflowArtifactAuthHeaders({ kind: 'bearer', token: 'native-access-token' })).toEqual({
      Authorization: 'Bearer native-access-token'
    })
  })

  it('aborts before buffering a response whose recorded size exceeds the producer limit', async () => {
    const response = new EventEmitter() as EventEmitter & {
      headers: Record<string, string>
      statusCode: number
    }

    response.headers = { 'content-length': String(WORKFLOW_ARTIFACT_MAX_BYTES + 1) }
    response.statusCode = 200
    const abort = vi.fn()

    await expect(collectWorkflowArtifactResponse(response, WORKFLOW_ARTIFACT_MAX_BYTES, abort)).rejects.toThrow(
      'Workflow artifact exceeds the download limit'
    )
    expect(abort).toHaveBeenCalledOnce()
  })

  it('downloads through the resolved backend instead of the renderer file:// origin and saves recorded metadata', async () => {
    const testDeps = deps()

    await expect(downloadWorkflowArtifactWithDeps(request, testDeps)).resolves.toEqual({
      filename: 'diagnostic.json',
      mediaType: 'application/json',
      sizeBytes: 11,
      status: 'saved'
    })

    expect(testDeps.ensureBackend).toHaveBeenCalledWith('remote-profile')
    expect(testDeps.routePath).toHaveBeenCalledWith(request.path, 'remote-profile')
    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      'http://127.0.0.1:8899/api/plugins/workflow/runs/run%20%2F%20one/artifacts/publication%20%2F%20opaque/download',
      { kind: 'token', token: 'local-session-token' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
    expect(testDeps.chooseSavePath).toHaveBeenCalledWith({
      filename: 'diagnostic.json',
      mediaType: 'application/json'
    })
    expect(testDeps.writeFile).toHaveBeenCalledWith('/tmp/diagnostic.json', expect.any(Uint8Array))
  })

  it('keeps the captured profile while applying global-remote/profile-aware routing', async () => {
    const testDeps = deps({
      ensureBackend: vi.fn().mockResolvedValue({
        authMode: 'token',
        baseUrl: 'https://gateway.example',
        token: 'remote-static-token'
      }),
      routePath: vi.fn((path, profile) => `${path}?profile=${encodeURIComponent(profile ?? '')}`)
    })

    await downloadWorkflowArtifactWithDeps(request, testDeps)

    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      'https://gateway.example/api/plugins/workflow/runs/run%20%2F%20one/artifacts/publication%20%2F%20opaque/download?profile=remote-profile',
      { kind: 'token', token: 'remote-static-token' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
  })

  it('uses a refreshed native OAuth bearer when available', async () => {
    const testDeps = deps({
      ensureBackend: vi.fn().mockResolvedValue({ authMode: 'oauth', baseUrl: 'https://gateway.example', token: '' }),
      resolveOauthAuth: vi.fn().mockResolvedValue({ kind: 'bearer', token: 'native-access-token' })
    })

    await downloadWorkflowArtifactWithDeps(request, testDeps)

    expect(testDeps.resolveOauthAuth).toHaveBeenCalledWith('https://gateway.example')
    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      expect.stringContaining('https://gateway.example/api/plugins/workflow/'),
      { kind: 'bearer', token: 'native-access-token' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
  })

  it('uses the OAuth session-cookie transport when no native bearer exists', async () => {
    const testDeps = deps({
      ensureBackend: vi.fn().mockResolvedValue({ authMode: 'oauth', baseUrl: 'https://gateway.example', token: '' }),
      resolveOauthAuth: vi.fn().mockResolvedValue({ kind: 'cookie' })
    })

    await downloadWorkflowArtifactWithDeps(request, testDeps)

    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      expect.stringContaining('https://gateway.example/api/plugins/workflow/'),
      { kind: 'cookie' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
  })

  it('returns typed cancellation without writing a file', async () => {
    const testDeps = deps({ chooseSavePath: vi.fn().mockResolvedValue(null) })

    await expect(downloadWorkflowArtifactWithDeps(request, testDeps)).resolves.toEqual({ status: 'cancelled' })
    expect(testDeps.writeFile).not.toHaveBeenCalled()
  })

  it('rejects unscoped paths before transport and bounds responses at the producer limit', async () => {
    const invalidDeps = deps()

    await expect(
      downloadWorkflowArtifactWithDeps({ path: 'https://attacker.example/artifact', profile: 'default' }, invalidDeps)
    ).rejects.toThrow('Invalid workflow artifact download path')
    expect(invalidDeps.ensureBackend).not.toHaveBeenCalled()

    const oversizedDeps = deps({
      fetchResource: vi.fn().mockResolvedValue({
        bytes: new Uint8Array(WORKFLOW_ARTIFACT_MAX_BYTES + 1),
        headers: {
          'content-disposition': 'attachment; filename="too-large.json"',
          'content-type': 'application/json'
        }
      })
    })

    await expect(downloadWorkflowArtifactWithDeps(request, oversizedDeps)).rejects.toThrow(
      'Workflow artifact exceeds the download limit'
    )
    expect(oversizedDeps.chooseSavePath).not.toHaveBeenCalled()
    expect(oversizedDeps.writeFile).not.toHaveBeenCalled()
  })

  it('leaves transport failures retryable and never opens the save dialog after failure', async () => {
    const testDeps = deps({ fetchResource: vi.fn().mockRejectedValue(new Error('gateway unavailable')) })

    await expect(downloadWorkflowArtifactWithDeps(request, testDeps)).rejects.toThrow('gateway unavailable')
    expect(testDeps.chooseSavePath).not.toHaveBeenCalled()
    expect(testDeps.writeFile).not.toHaveBeenCalled()
  })
})
