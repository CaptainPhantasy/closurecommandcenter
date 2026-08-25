import { useEffect, useState, useCallback } from "react"
import { Heart, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { StatusBadge } from "@/components/StatusBadge"
import { SourceRef, type SourceRefData } from "@/components/SourceRef"
import { fetchHealth, fetchPerformance, type KernelHealth, type KernelPerformance } from "@/lib/api"

export function SourceHealth() {
  const [health, setHealth] = useState<{ data: KernelHealth | null; ref: SourceRefData }>({ data: null, ref: { url: "", fetched_at: "", status: "error" } })
  const [perf, setPerf] = useState<{ data: KernelPerformance | null; ref: SourceRefData }>({ data: null, ref: { url: "", fetched_at: "", status: "error" } })

  const refresh = useCallback(async () => {
    const [h, p] = await Promise.all([fetchHealth(), fetchPerformance()])
    setHealth(h)
    setPerf(p)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const h = health.data
  const p = perf.data

  return (
    <div className="space-y-[var(--spacing-section)]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[var(--font-size-3xl)] font-bold tracking-tight">Source Health</h1>
          <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1">
            Kernel API connectivity and resource status
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          <RefreshCw size={14} />
          Refresh
        </Button>
      </div>

      {/* Health card — shadcn Card */}
      <Card className="shadow-elevation-1">
        <CardContent className="p-[var(--spacing-card)] space-y-4">
          <div className="flex items-center gap-3">
            <Heart size={20} className={health.ref.status === "ok" ? "text-healthy" : "text-error"} />
            <div>
              <div className="text-[var(--font-size-lg)] font-medium">Kernel Status</div>
              <div className="flex items-center gap-2 mt-1">
                <StatusBadge status={health.ref.status === "ok" ? "healthy" : "error"} />
                {h?.version && <span className="text-[var(--font-size-sm)] text-muted-foreground tabular-nums">v{h.version}</span>}
              </div>
            </div>
          </div>
          {p && (
            <>
              <Separator />
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <HealthMetric label="Uptime" value={formatUptime(p.uptime_seconds)} />
                <HealthMetric label="CPU" value={`${p.cpu_percent.toFixed(1)}%`} warn={p.cpu_percent > 70} />
                <HealthMetric label="Memory" value={`${p.memory_mb.toFixed(0)} MB`} warn={p.memory_mb > 4096} />
                <HealthMetric label="Sessions" value={String(p.active_sessions)} />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Provenance */}
      <div className="space-y-1">
        <SourceRef ref={health.ref} />
        <SourceRef ref={perf.ref} />
      </div>
    </div>
  )
}

function HealthMetric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <div className="text-[var(--font-size-sm)] text-muted-foreground">{label}</div>
      {/* type-tabular-nums-for-data */}
      <div className={`text-[var(--font-size-lg)] font-semibold tabular-nums ${warn ? "text-stale" : ""}`}>{value}</div>
    </div>
  )
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

