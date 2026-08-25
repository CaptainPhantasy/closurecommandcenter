/** Dashboard-side types mirroring buddy-telemetry/schema.py canonical types */

export type SessionStatus =
  | "ready" | "busy" | "waiting_for_input"
  | "error" | "stale" | "unknown"

export type HealthGrade =
  | "healthy" | "stale" | "blocked" | "waiting"
  | "warning" | "error" | "critical" | "unknown"

export type AgentStatus = "idle" | "busy" | "waiting" | "error" | "stale" | "unknown"

export interface SessionState {
  id: string
  agent: string
  status: SessionStatus
  cwd: string
  command: string
  pid: number
  started_at: string
  last_activity: string
}

export interface AgentState {
  id: string
  name: string
  status: AgentStatus
  template: string
  tags: string[]
}

export interface ResourceSnapshot {
  cpu_percent: number
  memory_mb: number
  disk_percent: number
  timestamp: string
}

export interface Alert {
  severity: HealthGrade
  entity_type: "session" | "agent" | "resource"
  entity_id: string
  message: string
  needs_human: boolean
  source_url: string
  timestamp: string
}

export interface RecommendedAction {
  action: string
  target_id: string
  requires_approval: boolean
  rationale: string
}

