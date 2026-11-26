// Shapes returned by the Coco engine API (/api/v1). Kept in step with server/coco_server/store.py.

export type Severity = 'error' | 'warning'
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Diagnostic {
  line: number
  col: number
  message: string
  severity: Severity
  code: string
  end_col?: number | null
}

export interface Param {
  name: string
  type: string
}

export interface Signature {
  name: string
  line: number
  inputs: Param[]
  outputs: Param[]
}

export interface ClassSymbol {
  name: string
  line: number
  fields: Param[]
}

export interface Symbols {
  package: string | null
  classes: ClassSymbol[]
  steps: Signature[]
  flows: Signature[]
  imports: { module: string; alias: string | null; names: string[]; kind: 'coco' | 'python' }[]
  self: string[]
}

export interface Version {
  id: string
  version: number
  checksum: string
  is_valid: boolean
  diagnostics: Diagnostic[]
  flows: Signature[]
  created_at: string
  source?: string
  symbols?: Symbols
  is_current?: boolean
}

export interface RunSummary {
  id: string
  status: RunStatus
  flow_name: string
  queued_at: string
  finished_at: string | null
}

export interface Workflow {
  id: string
  name: string
  description: string
  created_at: string
  updated_at: string
  current_version: Version | null
  last_run: RunSummary | null
}

export interface RunError {
  code: string
  message: string
  traceback?: string
  [k: string]: unknown
}

export interface Run {
  id: string
  workflow_id: string
  workflow_name: string
  version_id: string
  version: number
  flow_name: string
  status: RunStatus
  trigger: string
  schedule_id: string | null
  input?: Record<string, unknown>
  output?: Record<string, unknown> | null
  error?: RunError | null
  queued_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  event_count: number
  env_id?: string | null
  env_name?: string | null
  stream_url?: string
}

export interface Environment {
  id: string
  name: string
  description: string
  data: Record<string, unknown>
  is_default: boolean
  keys: string[]
  created_at: string
  updated_at: string
}

export interface CocoEvent {
  run_id: string
  seq: number
  type: string
  ts: string
  path: string | null
  step: string | null
  payload: Record<string, any>
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  size: number
}

export interface EventPage {
  items: CocoEvent[]
  has_more: boolean
  next_after_seq: number
}

export interface ValidateResult {
  package: string | null
  is_valid: boolean
  diagnostics: Diagnostic[]
  flows: Signature[]
  symbols: Symbols
}

export interface Health {
  ok: boolean
  version: string
  coco_core: string
  active_runs: number
  max_runs: number
}

export const TERMINAL: RunStatus[] = ['succeeded', 'failed', 'cancelled']

export interface Note {
  id: string
  title: string
  body?: string
  preview: string
  chars: number
  created_at: string
  updated_at: string
}
