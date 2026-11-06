// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from "vitest"
import type { ThemeName, Density, MotionLevel, StatusStyle } from "./settings"

describe("settings types", () => {
  it("ThemeName accepts all 7 valid values", () => {
    const themes: ThemeName[] = [
      "slate-cyan", "carbon-blue", "matrix-green",
      "navy-amber", "vaporwave", "thermal", "arctic-light",
    ]
    expect(themes).toHaveLength(7)
    for (const t of themes) {
      expect(typeof t).toBe("string")
      expect(t.length).toBeGreaterThan(0)
    }
  })

  it("Density has exactly 3 valid values", () => {
    const densities: Density[] = ["compact", "balanced", "spacious"]
    expect(densities).toHaveLength(3)
  })

  it("MotionLevel has exactly 3 valid values", () => {
    const levels: MotionLevel[] = ["none", "moderate", "expressive"]
    expect(levels).toHaveLength(3)
  })

  it("StatusStyle has exactly 3 valid values", () => {
    const styles: StatusStyle[] = ["pulse", "static", "shift"]
    expect(styles).toHaveLength(3)
  })
})

describe("applySettingsToDom", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-theme")
    document.documentElement.removeAttribute("data-density")
    document.documentElement.removeAttribute("data-motion")
  })

  it("sets data-theme attribute on document element", async () => {
    const { applySettingsToDom } = await import("./settings")
    const settings = {
      theme: "matrix-green" as ThemeName,
      density: "balanced" as Density,
      motion: "moderate" as MotionLevel,
      statusStyle: "pulse" as StatusStyle,
      sidebarCollapsed: false,
    }
    applySettingsToDom(settings)
    expect(document.documentElement.getAttribute("data-theme")).toBe("matrix-green")
    expect(document.documentElement.getAttribute("data-density")).toBe("balanced")
    expect(document.documentElement.getAttribute("data-motion")).toBe("moderate")
  })
})

describe("THEMES constant", () => {
  it("exports 7 themes with name and label", async () => {
    const { THEMES } = await import("./settings")
    expect(THEMES).toHaveLength(7)
    for (const t of THEMES) {
      expect(t.name).toBeTruthy()
      expect(t.label).toBeTruthy()
    }
    // Commercial labels — no cyberpunk / neon naming in the picker
    const labels = THEMES.map((t) => t.label.toLowerCase())
    for (const banned of ["vaporwave", "matrix", "neon", "cyber"]) {
      expect(labels.join(" ")).not.toContain(banned)
    }
  })
})
