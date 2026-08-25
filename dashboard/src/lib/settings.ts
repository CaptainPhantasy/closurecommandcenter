import { create } from "zustand"
import { persist } from "zustand/middleware"

export type ThemeName =
  | "slate-cyan"
  | "carbon-blue"
  | "matrix-green"
  | "navy-amber"
  | "vaporwave"
  | "thermal"
  | "arctic-light"

export type Density = "compact" | "balanced" | "spacious"
export type MotionLevel = "none" | "moderate" | "expressive"
export type StatusStyle = "pulse" | "static" | "shift"

export interface DashboardSettings {
  theme: ThemeName
  density: Density
  motion: MotionLevel
  statusStyle: StatusStyle
  sidebarCollapsed: boolean
}

interface SettingsState extends DashboardSettings {
  setTheme: (t: ThemeName) => void
  setDensity: (d: Density) => void
  setMotion: (m: MotionLevel) => void
  setStatusStyle: (s: StatusStyle) => void
  toggleSidebar: () => void
  setSidebarCollapsed: (v: boolean) => void
}

export const THEMES: { name: ThemeName; label: string }[] = [
  { name: "slate-cyan", label: "Slate + Cyan" },
  { name: "carbon-blue", label: "Carbon + Blue" },
  { name: "matrix-green", label: "Matrix Green" },
  { name: "navy-amber", label: "Navy + Amber" },
  { name: "vaporwave", label: "Vaporwave" },
  { name: "thermal", label: "Thermal" },
  { name: "arctic-light", label: "Arctic Light" },
]

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: "slate-cyan",
      density: "balanced",
      motion: "moderate",
      statusStyle: "shift",
      sidebarCollapsed: false,
      setTheme: (theme) => set({ theme }),
      setDensity: (density) => set({ density }),
      setMotion: (motion) => set({ motion }),
      setStatusStyle: (statusStyle) => set({ statusStyle }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
    }),
    { name: "fuckbutt-settings" },
  ),
)

/** Apply all settings as data attributes on <html> */
export function applySettingsToDom(s: DashboardSettings): void {
  const el = document.documentElement
  el.setAttribute("data-theme", s.theme)
  el.setAttribute("data-density", s.density)
  el.setAttribute("data-motion", s.motion)
  el.dataset.statusStyle = s.statusStyle
}

