import type { SessionState, AgentState } from "./schema"

/** Kernel response shapes — matches actual server.py endpoints */
export interface KernelSession {
  id: string
  agent: string
  status: string
  cwd?: string
  command?: string
  started_at?: string
  last_activity?: string
  pid?: number
}

export interface KernelAgent {
  id: string
  name: string
  status: string
  label?: string | null
  directory?: string
  command?: string
  tags?: string[]
  auto_start?: boolean
  pinned?: boolean
  uptime?: number
  restart_count?: number
}

export interface KernelPerformance {
  cpu_percent: number
  memory_mb: number
  disk_usage_percent?: number
  uptime_seconds: number
  active_sessions: number
  active_agents: number
}

export interface KernelHealth {
  status: string
  uptime?: number
  version?: string
  last_check?: string
}

export interface SourceRef {
  url: string
  fetched_at: string
  status: "ok" | "stale" | "error"
}

const API_BASE = "/api"
const TIMEOUT_MS = 5000

async function fetchJson<T>(path: string): Promise<{ data: T | null; ref: SourceRef }> {
  const url = `${API_BASE}${path}`
  const fetched_at = new Date().toISOString()
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
    const res = await fetch(url, { signal: controller.signal })
    clearTimeout(timer)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: T = await res.json()
    return { data, ref: { url, fetched_at, status: "ok" } }
  } catch {
    return { data: null, ref: { url, fetched_at, status: "error" } }
  }
}

/** Normalize kernel /aterm/status → KernelSession[] */
export async function fetchSessions() {
  const raw = await fetchJson<{ ok: boolean; sessions: KernelSession[]; total: number }>("/aterm/status")
  if (raw.data && Array.isArray(raw.data.sessions)) {
    return { data: raw.data.sessions, ref: raw.ref }
  }
  return { data: [] as KernelSession[], ref: raw.ref }
}

export function fetchAgents() {
  return fetchJson<KernelAgent[]>("/agents")
}

/** Normalize kernel /performance → KernelPerformance */
export async function fetchPerformance() {
  const raw = await fetchJson<{
    cpu_user_s: number
    cpu_system_s: number
    max_rss_kb: number
    running_agents: number
    timestamp: number
  }>("/performance")
  if (raw.data) {
    const d = raw.data
    return {
      data: {
        cpu_percent: d.cpu_user_s + d.cpu_system_s,
        memory_mb: d.max_rss_kb / 1024,
        uptime_seconds: 0,
        active_sessions: 0,
        active_agents: d.running_agents,
      } satisfies KernelPerformance,
      ref: raw.ref,
    }
  }
  return { data: null, ref: raw.ref }
}

/** Normalize kernel /health → KernelHealth */
export async function fetchHealth() {
  const raw = await fetchJson<{ status: string }>("/health")
  if (raw.data) {
    return {
      data: {
        status: raw.data.status,
        uptime: 0,
        version: undefined,
        last_check: new Date().toISOString(),
      } satisfies KernelHealth,
      ref: raw.ref,
    }
  }
  return { data: null, ref: raw.ref }
}

/** SSE connection for live events with automatic reconnection */
export function connectEventStream(
  onEvent: (event: MessageEvent) => void,
  onError?: () => void,
): EventSource | null {
  const url = `${API_BASE}/events/stream`
  let retryDelay = 1000
  const maxDelay = 30000
  let es: EventSource | null = null

  function connect(): EventSource | null {
    try {
      es = new EventSource(url)
      es.onmessage = (e) => {
        retryDelay = 1000
        onEvent(e)
      }
      es.onerror = () => {
        es?.close()
        onError?.()
        retryDelay = Math.min(retryDelay * 2, maxDelay)
        setTimeout(connect, retryDelay)
      }
      return es
    } catch {
      return null
    }
  }

  return connect()
}

/** Poll all endpoints at interval, return combined snapshot */
export interface DashboardSnapshot {
  sessions: { data: KernelSession[] | null; ref: SourceRef }
  agents: { data: KernelAgent[] | null; ref: SourceRef }
  performance: { data: KernelPerformance | null; ref: SourceRef }
  health: { data: KernelHealth | null; ref: SourceRef }
  captured_at: string
}

export async function captureSnapshot(): Promise<DashboardSnapshot> {
  const [sessions, agents, performance, health] = await Promise.all([
    fetchSessions(),
    fetchAgents(),
    fetchPerformance(),
    fetchHealth(),
  ])
  return {
    sessions,
    agents,
    performance,
    health,
    captured_at: new Date().toISOString(),
  }
}

