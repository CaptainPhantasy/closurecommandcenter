import { describe, it, expect } from "vitest"
import type { KernelSession, KernelAgent, KernelPerformance, KernelHealth, SourceRef } from "./api"

describe("schema types compile and constrain", () => {
  it("KernelSession has required fields", () => {
    const session: KernelSession = {
      id: "s-001",
      agent: "claude",
      status: "running",
    }
    expect(session.id).toBe("s-001")
    expect(session.agent).toBe("claude")
    expect(session.status).toBe("running")
  })

  it("KernelSession allows optional fields", () => {
    const session: KernelSession = {
      id: "s-002",
      agent: "gpt",
      status: "stopped",
      cwd: "/tmp",
      pid: 12345,
    }
    expect(session.cwd).toBe("/tmp")
    expect(session.pid).toBe(12345)
  })

  it("KernelAgent has required fields", () => {
    const agent: KernelAgent = {
      id: "a-001",
      name: "coder",
      status: "idle",
    }
    expect(agent.name).toBe("coder")
  })

  it("KernelPerformance has numeric metrics", () => {
    const perf: KernelPerformance = {
      cpu_percent: 42.5,
      memory_mb: 1024,
      uptime_seconds: 86400,
      active_sessions: 3,
      active_agents: 2,
    }
    expect(perf.cpu_percent).toBeGreaterThanOrEqual(0)
    expect(perf.memory_mb).toBeGreaterThan(0)
  })

  it("KernelHealth tracks status and uptime", () => {
    const health: KernelHealth = {
      status: "ok",
      uptime: 7200,
      last_check: "2026-05-20T12:00:00Z",
    }
    expect(health.status).toBe("ok")
  })

  it("SourceRef tracks provenance", () => {
    const ref: SourceRef = {
      url: "/api/agents",
      fetched_at: "2026-05-20T12:00:00Z",
      status: "ok",
    }
    expect(ref.url).toBeTruthy()
    expect(["ok", "stale", "error"]).toContain(ref.status)
  })
})

