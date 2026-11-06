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
  { name: "slate-cyan", label: "Architectural Slate" },
  { name: "carbon-blue", label: "Carbon Blue" },
  { name: "matrix-green", label: "Sage" },
  { name: "navy-amber", label: "Navy Brass" },
  { name: "vaporwave", label: "Warm Stone" },
  { name: "thermal", label: "Terracotta" },
  { name: "arctic-light", label: "Arctic Light" },
]

export const SETTINGS_STORAGE_KEY = "ccc-settings"
/** Key used by earlier dashboard builds; migrated once so saved preferences survive the rename. */
export const LEGACY_SETTINGS_STORAGE_KEY = "fuckbutt-settings"

/** Copy saved preferences from the legacy key to the current key before hydration. */
export function migrateLegacySettings(storage: Storage | undefined = globalThis.localStorage): void {
  if (!storage) return
  try {
    const legacy = storage.getItem(LEGACY_SETTINGS_STORAGE_KEY)
    if (legacy === null) return
    if (storage.getItem(SETTINGS_STORAGE_KEY) === null) {
      storage.setItem(SETTINGS_STORAGE_KEY, legacy)
    }
    storage.removeItem(LEGACY_SETTINGS_STORAGE_KEY)
  } catch {
    // Storage can be unavailable (private mode, quota); fall back to defaults.
  }
}

migrateLegacySettings()

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
    { name: SETTINGS_STORAGE_KEY },
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
