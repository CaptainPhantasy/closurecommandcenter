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
          Appearance, density, and motion preferences
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
                  {s === "pulse" ? "Soft pulse" : s === "static" ? "Color only" : "Transition on change"}
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
    "slate-cyan": ["#111923", "#1a2633", "#2b394a", "#bacdda", "#eef2f6"],
    "carbon-blue": ["#111923", "#1a2633", "#2b394a", "#afc5dc", "#eef2f6"],
    "matrix-green": ["#111923", "#1a2633", "#2b394a", "#b5c9bb", "#eef2f6"],
    "navy-amber": ["#111923", "#1a2633", "#2b394a", "#d5bf98", "#eef2f6"],
    "vaporwave": ["#111923", "#1a2633", "#2b394a", "#c7c2b9", "#eef2f6"],
    "thermal": ["#111923", "#1a2633", "#2b394a", "#d5b6a4", "#eef2f6"],
    "arctic-light": ["#f1f5f8", "#ffffff", "#e5ecf2", "#34566f", "#172534"],
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

