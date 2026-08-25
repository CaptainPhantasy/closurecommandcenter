import { describe, it, expect, beforeEach, vi } from "vitest"
import { fetchSessions, fetchAgents, fetchPerformance, fetchHealth, connectEventStream, captureSnapshot } from "./api"

// Mock global fetch
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

function mockResponse(data: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(data),
  } as Response)
}

describe("fetchJson helpers", () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it("fetchSessions returns sessions from wrapped response", async () => {
    const fakeSessions = [{ id: "s1", agent: "claude", status: "running", name: "test" }]
    mockFetch.mockReturnValue(mockResponse({ ok: true, sessions: fakeSessions, total: 1 }))

    const result = await fetchSessions()
    expect(result.data).toEqual(fakeSessions)
    expect(result.ref.status).toBe("ok")
  })

  it("fetchSessions returns empty array when sessions field missing", async () => {
    mockFetch.mockReturnValue(mockResponse({ ok: true }))

    const result = await fetchSessions()
    expect(result.data).toEqual([])
    expect(result.ref.status).toBe("ok")
  })

  it("fetchAgents returns data with SourceRef", async () => {
    const fakeAgents = [{ id: "a1", name: "coder", status: "idle" }]
    mockFetch.mockReturnValue(mockResponse(fakeAgents))

    const result = await fetchAgents()
    expect(result.data).toEqual(fakeAgents)
    expect(result.ref.url).toBe("/api/agents")
  })

  it("fetchPerformance maps raw metrics to KernelPerformance", async () => {
    mockFetch.mockReturnValue(mockResponse({
      cpu_user_s: 30.2,
      cpu_system_s: 15.0,
      max_rss_kb: 524288,
      running_agents: 2,
      timestamp: 1716192000,
    }))

    const result = await fetchPerformance()
    expect(result.data?.cpu_percent).toBeCloseTo(45.2, 1)
    expect(result.data?.memory_mb).toBeCloseTo(512, 0)
    expect(result.data?.active_agents).toBe(2)
    expect(result.ref.status).toBe("ok")
  })

  it("fetchPerformance returns null on fetch error", async () => {
    mockFetch.mockRejectedValue(new Error("fail"))

    const result = await fetchPerformance()
    expect(result.data).toBeNull()
    expect(result.ref.status).toBe("error")
  })

  it("fetchHealth returns data with SourceRef", async () => {
    const health = { status: "ok", uptime: 7200, last_check: "2026-05-20T00:00:00Z" }
    mockFetch.mockReturnValue(mockResponse(health))

    const result = await fetchHealth()
    expect(result.data?.status).toBe("ok")
    expect(result.ref.url).toBe("/api/health")
  })

  it("handles fetch failure gracefully", async () => {
    mockFetch.mockRejectedValue(new Error("network error"))

    const result = await fetchSessions()
    expect(result.data).toEqual([])
    expect(result.ref.status).toBe("error")
  })

  it("handles non-200 response", async () => {
    mockFetch.mockReturnValue(mockResponse(null, false, 500))

    const result = await fetchSessions()
    expect(result.data).toEqual([])
    expect(result.ref.status).toBe("error")
  })
})

describe("connectEventStream", () => {
  it("returns null when EventSource throws", () => {
    vi.stubGlobal("EventSource", class {
      constructor() { throw new Error("no SSE") }
    })
    const result = connectEventStream(vi.fn())
    expect(result).toBeNull()
  })
})

describe("captureSnapshot", () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it("combines all four endpoints into one snapshot", async () => {
    mockFetch
      .mockReturnValueOnce(mockResponse({ ok: true, sessions: [{ id: "s1", agent: "a", status: "running", name: "t" }], total: 1 }))
      .mockReturnValueOnce(mockResponse([{ id: "a1", name: "coder", status: "idle" }]))
      .mockReturnValueOnce(mockResponse({ cpu_user_s: 5, cpu_system_s: 5, max_rss_kb: 262144, running_agents: 1, timestamp: 0 }))
      .mockReturnValueOnce(mockResponse({ status: "ok", uptime: 60, last_check: "now" }))

    const snap = await captureSnapshot()
    expect(snap.sessions.data).toHaveLength(1)
    expect(snap.agents.data).toHaveLength(1)
    expect(snap.performance.data?.cpu_percent).toBeCloseTo(10, 0)
    expect(snap.health.data?.status).toBe("ok")
    expect(snap.captured_at).toBeTruthy()
  })

  it("handles partial failures", async () => {
    mockFetch
      .mockReturnValueOnce(mockResponse({ ok: true, sessions: [{ id: "s1", agent: "a", status: "ok", name: "t" }], total: 1 }))
      .mockRejectedValueOnce(new Error("fail"))
      .mockReturnValueOnce(mockResponse({ cpu_user_s: 3, cpu_system_s: 2, max_rss_kb: 131072, running_agents: 0, timestamp: 0 }))
      .mockRejectedValueOnce(new Error("fail"))

    const snap = await captureSnapshot()
    expect(snap.sessions.data).toHaveLength(1)
    expect(snap.agents.data).toBeNull()
    expect(snap.agents.ref.status).toBe("error")
    expect(snap.performance.data).toBeTruthy()
    expect(snap.health.data).toBeNull()
  })
})

