import { useSettings, THEMES, type ThemeName, type Density, type MotionLevel, type StatusStyle } from "@/lib/settings"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

export function SettingsPage() {
  const settings = useSettings()

  return (
    <div className="space-y-[var(--spacing-section)] max-w-2xl">
      <div>
        <h1 className="text-[var(--font-size-3xl)] font-bold tracking-tight">Settings</h1>
        <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1">
          Personalize your dashboard experience
        </p>
      </div>

      {/* Theme */}
      <Card>
        <CardHeader>
          <CardTitle className="text-[var(--font-size-lg)]">Theme</CardTitle>
          <CardDescription>Choose your color palette</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
            {THEMES.map((t) => (
              <button
                key={t.name}
                onClick={() => settings.setTheme(t.name)}
                className={cn(
                  "rounded-lg border p-3 text-left transition-all outline-none focus-visible:ring-1 focus-visible:ring-ring",
                  settings.theme === t.name
                    ? "border-primary ring-1 ring-primary"
                    : "border-border hover:border-muted-foreground/30",
                )}
              >
                <div className="flex gap-0.5 mb-2">
                  <ThemeSwatch theme={t.name} />
                </div>
                <span className="text-[var(--font-size-sm)] font-medium">{t.label}</span>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Density */}
      <Card>
        <CardHeader>
          <CardTitle className="text-[var(--font-size-lg)]">Information Density</CardTitle>
          <CardDescription>How much detail per screen</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-2">
            {(["compact", "balanced", "spacious"] as Density[]).map((d) => (
              <Button
                key={d}
                variant={settings.density === d ? "default" : "outline"}
                onClick={() => settings.setDensity(d)}
                className="capitalize"
              >
                {d}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Motion */}
      <Card>
        <CardHeader>
          <CardTitle className="text-[var(--font-size-lg)]">Motion Level</CardTitle>
          <CardDescription>Animation intensity (respects prefers-reduced-motion)</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-2">
            {(["none", "moderate", "expressive"] as MotionLevel[]).map((m) => (
              <Button
                key={m}
                variant={settings.motion === m ? "default" : "outline"}
                onClick={() => settings.setMotion(m)}
                className="capitalize"
              >
                {m}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Status Style */}
      <Card>
        <CardHeader>
          <CardTitle className="text-[var(--font-size-lg)]">Status Indicators</CardTitle>
          <CardDescription>How agent/session status is displayed</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-2">
            {(["pulse", "static", "shift"] as StatusStyle[]).map((s) => (
              <Button
                key={s}
                variant={settings.statusStyle === s ? "default" : "outline"}
                onClick={() => settings.setStatusStyle(s)}
                className="flex-col h-auto py-3"
              >
                <span className="capitalize font-medium">{s}</span>
                <span className="text-xs opacity-70 mt-0.5 normal-case font-normal">
                  {s === "pulse" ? "Animated glow" : s === "static" ? "Color only" : "Transition on change"}
                </span>
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

/** Renders a 5-color swatch for a theme */
function ThemeSwatch({ theme }: { theme: ThemeName }) {
  const swatches: Record<ThemeName, string[]> = {
    "slate-cyan": ["#0f172a", "#1e293b", "#334155", "#22d3ee", "#e2e8f0"],
    "carbon-blue": ["#09090b", "#18181b", "#27272a", "#3b82f6", "#fafafa"],
    "matrix-green": ["#0a0a0a", "#141414", "#262626", "#22c55e", "#d4d4d4"],
    "navy-amber": ["#0c1222", "#162032", "#1e3048", "#f59e0b", "#e2e8f0"],
    "vaporwave": ["#1a1033", "#241745", "#3b2766", "#e879f9", "#e8d5f5"],
    "thermal": ["#0f0f0f", "#1c1917", "#292524", "#f97316", "#f5f5f4"],
    "arctic-light": ["#f8fafc", "#ffffff", "#e2e8f0", "#0ea5e9", "#0f172a"],
  }
  const colors = swatches[theme] ?? swatches["slate-cyan"]

  return (
    <div className="flex gap-0.5 h-4 w-full rounded overflow-hidden">
      {colors.map((c, i) => (
        <div key={i} className="flex-1" style={{ backgroundColor: c }} />
      ))}
    </div>
  )
}

