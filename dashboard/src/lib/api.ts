import type { SessionState, AgentState } from "./schema"

/** Kernel response shapes — matches actual server.py endpoints */
export interface KernelSession {
  id: string
  name: string
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

/**
 * CPU rate state. The server reports cumulative getrusage seconds (each counter
 * rounded to 0.01 s) plus its wall-clock timestamp, so utilisation is derived
 * from the delta between a baseline sample and the newest one.
 */
/** Minimum window before a rate is computed: two 0.01 s roundings stay within ~1%. */
export const MIN_CPU_WINDOW_S = 2
let cpuBaseline: { cpu_s: number; timestamp: number } | null = null
let newestCpuTimestamp = -Infinity
let lastCpuPercent = 0

/** Forget the CPU baseline (used by tests and when the dashboard reconnects). */
export function resetCpuBaseline() {
  cpuBaseline = null
  newestCpuTimestamp = -Infinity
  lastCpuPercent = 0
}

/**
 * Turn a cumulative CPU sample into a utilisation percentage.
 * - Out-of-order responses (timestamp not newer than the newest seen) never
 *   touch the baseline; they report the last computed rate.
 * - A counter that goes backwards means the server process restarted, so the
 *   baseline is reset and 0 is reported for that window.
 * - Windows shorter than MIN_CPU_WINDOW_S keep the older baseline (so rounding
 *   can't be amplified) and report the last computed rate.
 */
export function cpuPercentFromSample(cpu_s: number, timestamp: number): number {
  if (!Number.isFinite(cpu_s) || !Number.isFinite(timestamp)) return lastCpuPercent
  if (timestamp <= newestCpuTimestamp) return lastCpuPercent
  newestCpuTimestamp = timestamp
  if (!cpuBaseline || cpu_s < cpuBaseline.cpu_s) {
    cpuBaseline = { cpu_s, timestamp }
    lastCpuPercent = 0
    return 0
  }
  const wall_s = timestamp - cpuBaseline.timestamp
  if (wall_s < MIN_CPU_WINDOW_S) return lastCpuPercent
  lastCpuPercent = Math.max(0, ((cpu_s - cpuBaseline.cpu_s) / wall_s) * 100)
  cpuBaseline = { cpu_s, timestamp }
  return lastCpuPercent
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
    const cpu_percent = cpuPercentFromSample(d.cpu_user_s + d.cpu_system_s, d.timestamp)
    return {
      data: {
        cpu_percent,
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

