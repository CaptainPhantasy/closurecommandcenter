import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MissionControl } from "./MissionControl"
import type { DashboardSnapshot } from "@/lib/api"

vi.mock("@/lib/api", async (orig) => {
	const actual = await orig<typeof import("@/lib/api")>()
	return {
		...actual,
		captureSnapshot: vi.fn(),
	}
})

import { captureSnapshot } from "@/lib/api"
const mocked = vi.mocked(captureSnapshot)

function snapshot(overrides: Partial<DashboardSnapshot> = {}): DashboardSnapshot {
	return {
		sessions: {
			data: [
				{ id: "s1", agent: "floyd", status: "running", cwd: "/tmp" },
				{ id: "s2", agent: "floyd", status: "error" },
			],
			ref: { url: "/api/aterm/status", fetched_at: "2026-07-13T00:00:00Z", status: "ok" },
		},
		agents: {
			data: [
				{ id: "a1", name: "coder", status: "busy" },
				{ id: "a2", name: "reader", status: "idle" },
			],
			ref: { url: "/api/agents", fetched_at: "2026-07-13T00:00:00Z", status: "ok" },
		},
		performance: {
			data: {
				cpu_percent: 12.5,
				memory_mb: 512,
				uptime_seconds: 3600,
				active_sessions: 2,
				active_agents: 1,
			},
			ref: { url: "/api/system/performance", fetched_at: "2026-07-13T00:00:00Z", status: "ok" },
		},
		health: {
			data: { status: "ok", version: "1.2.3", uptime: 3600 },
			ref: { url: "/api/system/health", fetched_at: "2026-07-13T00:00:00Z", status: "ok" },
		},
		captured_at: "2026-07-13T14:00:00.000Z",
		...overrides,
	}
}

beforeEach(() => {
	mocked.mockReset()
})

afterEach(() => {
	vi.restoreAllMocks()
})

describe("MissionControl", () => {
	it("renders metrics from a successful snapshot (regression ISSUE-002: memory uses current not peak)", async () => {
		mocked.mockResolvedValue(snapshot())
		render(<MissionControl />)

		await waitFor(() => {
			expect(screen.getByText("Active Sessions")).toBeInTheDocument()
		})
		expect(screen.getByText("12.5%")).toBeInTheDocument()
		expect(screen.getByText("512 MB")).toBeInTheDocument()
	})

	it("shows kernel version when available (regression ISSUE-003: version not em-dash)", async () => {
		mocked.mockResolvedValue(snapshot())
		render(<MissionControl />)

		await waitFor(() => {
			expect(screen.getByText(/Kernel v1\.2\.3/)).toBeInTheDocument()
		})
	})

	it("shows error message when backend is unreachable (error boundary regression ISSUE-001)", async () => {
		mocked.mockRejectedValue(new Error("backend down"))
		render(<MissionControl />)

		await waitFor(() => {
			expect(screen.getByText(/backend down/i)).toBeInTheDocument()
		})
	})

	it("does not crash with null performance data", async () => {
		mocked.mockResolvedValue(snapshot({ performance: { data: null, ref: { url: "x", fetched_at: "x", status: "error" } } }))
		render(<MissionControl />)

		await waitFor(() => {
			expect(screen.getByText("Active Sessions")).toBeInTheDocument()
		})
		expect(screen.getAllByText("—").length).toBeGreaterThan(0)
	})
})

