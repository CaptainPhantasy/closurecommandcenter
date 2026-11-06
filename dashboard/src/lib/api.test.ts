import { describe, it, expect, beforeEach, vi } from "vitest"
import {
  fetchSessions, fetchAgents, fetchPerformance, fetchHealth, connectEventStream, captureSnapshot,
  cpuPercentFromSample, resetCpuBaseline, MIN_CPU_WINDOW_S,
} from "./api"

// Mock global fetch
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

// CPU rate state is module-level; every test starts from a clean baseline.
beforeEach(() => {
  resetCpuBaseline()
})

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
    // First sample → rate is 0 (no previous sample to diff against)
    mockFetch.mockReturnValue(mockResponse({
      cpu_user_s: 30.2,
      cpu_system_s: 15.0,
      max_rss_kb: 524288,
      running_agents: 2,
      timestamp: 1716192000,
    }))
    const first = await fetchPerformance()
    expect(first.data?.cpu_percent).toBe(0)
    expect(first.data?.memory_mb).toBeCloseTo(512, 0)
    expect(first.data?.active_agents).toBe(2)
    expect(first.ref.status).toBe("ok")

    // Second sample: Δcpu = 3.0 s over 10 s of wall time → 30%
    mockFetch.mockReturnValue(mockResponse({
      cpu_user_s: 32.2,
      cpu_system_s: 16.0,
      max_rss_kb: 524288,
      running_agents: 2,
      timestamp: 1716192010,
    }))
    const second = await fetchPerformance()
    expect(second.data?.cpu_percent).toBeCloseTo(30, 1)
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
    expect(snap.performance.data?.cpu_percent).toBe(0) // first sample: no rate yet
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


describe("cpuPercentFromSample", () => {
  it("reports 0 for the first sample and the rate for the next", () => {
    expect(cpuPercentFromSample(45.2, 1000)).toBe(0)
    expect(cpuPercentFromSample(48.2, 1010)).toBeCloseTo(30, 5)
  })

  it("resets the baseline when the counter goes backwards (server restart)", () => {
    cpuPercentFromSample(100, 1000)
    cpuPercentFromSample(110, 1010) // 100%
    expect(cpuPercentFromSample(1, 1015)).toBe(0) // restart: never negative, never huge
    expect(cpuPercentFromSample(2, 1025)).toBeCloseTo(10, 5) // measured from the new process
  })

  it("does not amplify 0.01 s rounding over very short windows", () => {
    cpuPercentFromSample(10.0, 1000)
    expect(cpuPercentFromSample(10.5, 1010)).toBeCloseTo(5, 5)
    // 0.02 s of rounding noise over 50 ms would read as 40%; short windows keep the last rate
    expect(cpuPercentFromSample(10.52, 1010.05)).toBeCloseTo(5, 5)
    // once the window from the kept baseline is long enough, the rate is computed from it
    expect(cpuPercentFromSample(10.52 + 0.08, 1010 + MIN_CPU_WINDOW_S * 2)).toBeCloseTo(2.5, 5)
  })

  it("ignores out-of-order responses so an older sample never replaces the newer baseline", () => {
    cpuPercentFromSample(10, 1000)
    expect(cpuPercentFromSample(12, 1020)).toBeCloseTo(10, 5)
    // a slow, older response arrives last
    expect(cpuPercentFromSample(11, 1010)).toBeCloseTo(10, 5)
    // next sample is measured against 1020, not 1010
    expect(cpuPercentFromSample(12.5, 1030)).toBeCloseTo(5, 5)
  })

  it("ignores non-finite samples", () => {
    cpuPercentFromSample(10, 1000)
    expect(cpuPercentFromSample(Number.NaN, 1010)).toBe(0)
    expect(cpuPercentFromSample(11, 1010)).toBeCloseTo(10, 5)
  })
})

describe("fetchPerformance CPU rate", () => {
  it("reports 0 after a backend restart instead of a negative or stale rate", async () => {
    const sample = (cpu: number, ts: number) =>
      mockResponse({ cpu_user_s: cpu, cpu_system_s: 0, max_rss_kb: 1024, running_agents: 0, timestamp: ts })
    mockFetch.mockReset()
    mockFetch.mockReturnValueOnce(sample(100, 1000)).mockReturnValueOnce(sample(105, 1005)).mockReturnValueOnce(sample(1, 1010))
    expect((await fetchPerformance()).data?.cpu_percent).toBe(0)
    expect((await fetchPerformance()).data?.cpu_percent).toBeCloseTo(100, 5)
    expect((await fetchPerformance()).data?.cpu_percent).toBe(0)
  })
})
