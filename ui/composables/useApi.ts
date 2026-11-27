import type {
  CocoEvent, Environment, EventPage, Health, Note, Page, Run, ValidateResult, Version, Workflow,
} from '~/types/api'

export const API_BASE = '/api/v1'

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message)
  }
}

function token(): string | null {
  try {
    return localStorage.getItem('coco.token')
  } catch {
    return null
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

function qs(query?: Query): string {
  if (!query) return ''
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['content-type'] = 'application/json'
  const t = token()
  if (t) headers.authorization = `Bearer ${t}`
  let res: Response
  try {
    res = await fetch(API_BASE + path + qs(query), {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    throw new ApiError(0, 'offline', 'Cannot reach the Coco engine. Is coco-server running?')
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const err = data?.error ?? {}
    throw new ApiError(res.status, err.code ?? 'http_error', err.message ?? res.statusText, err.details)
  }
  return data as T
}

export function streamUrl(runId: string, afterSeq: number): string {
  const t = token()
  return `${API_BASE}/runs/${runId}/stream${qs({ after_seq: afterSeq, token: t })}`
}

export const api = {
  health: () => request<Health>('GET', '/health'),

  listWorkflows: (q?: string, page = 1, size = 500) =>
    request<Page<Workflow>>('GET', '/workflows', undefined, { q, page, size }),
  getWorkflow: (id: string) => request<Workflow>('GET', `/workflows/${encodeURIComponent(id)}`),
  createWorkflow: (name: string, source: string, description = '') =>
    request<Workflow>('POST', '/workflows', { name, source, description }),
  saveWorkflow: (id: string, body: { source?: string; description?: string; name?: string }) =>
    request<Workflow>('PUT', `/workflows/${encodeURIComponent(id)}`, body),
  deleteWorkflow: (id: string) => request<void>('DELETE', `/workflows/${encodeURIComponent(id)}`),
  versions: (id: string) => request<{ items: Version[] }>('GET', `/workflows/${encodeURIComponent(id)}/versions`),
  version: (id: string, v: number) => request<Version>('GET', `/workflows/${encodeURIComponent(id)}/versions/${v}`),
  validate: (source: string, name?: string | null) =>
    request<ValidateResult>('POST', '/validate', { source, name: name ?? null }),

  startRun: (wf: string, flow: string, input: Record<string, unknown>,
    opts: { version?: number; wait?: boolean; env?: string | null } = {}) =>
    request<Run>('POST', `/workflows/${encodeURIComponent(wf)}/flows/${encodeURIComponent(flow)}/runs`,
      { input, ...opts }),
  listRuns: (query: Query) => request<Page<Run>>('GET', '/runs', undefined, query),
  getRun: (id: string) => request<Run>('GET', `/runs/${id}`),
  events: (id: string, afterSeq = 0, limit = 1000) =>
    request<EventPage>('GET', `/runs/${id}/events`, undefined, { after_seq: afterSeq, limit }),
  cancelRun: (id: string) => request<{ id: string }>('POST', `/runs/${id}/cancel`),
  rerun: (id: string) => request<Run>('POST', `/runs/${id}/rerun`),
  deleteRun: (id: string) => request<void>('DELETE', `/runs/${id}`),

  listEnvs: () => request<{ items: Environment[] }>('GET', '/environments'),
  createEnv: (body: { name: string; data: Record<string, unknown>; description?: string; is_default?: boolean }) =>
    request<Environment>('POST', '/environments', body),
  updateEnv: (id: string, body: { name?: string; data?: Record<string, unknown>; description?: string; is_default?: boolean }) =>
    request<Environment>('PUT', `/environments/${encodeURIComponent(id)}`, body),
  deleteEnv: (id: string) => request<void>('DELETE', `/environments/${encodeURIComponent(id)}`),

  listNotes: (q?: string) => request<{ items: Note[] }>('GET', '/notes', undefined, { q }),
  getNote: (id: string) => request<Note>('GET', `/notes/${id}`),
  createNote: (body: { title?: string; body?: string } = {}) => request<Note>('POST', '/notes', body),
  updateNote: (id: string, body: { title?: string; body?: string }) => request<Note>('PUT', `/notes/${id}`, body),
  deleteNote: (id: string) => request<void>('DELETE', `/notes/${id}`),
}

export type { CocoEvent }

export function useApi() {
  return api
}
