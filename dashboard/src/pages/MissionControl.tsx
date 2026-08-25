import { useEffect, useState, useCallback } from "react"
import { motion, AnimatePresence } from "motion/react"
import { Users, Cpu, MemoryStick, Activity, AlertTriangle } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { MetricCard } from "@/components/MetricCard"
import { StatusBadge } from "@/components/StatusBadge"
import { SourceRef } from "@/components/SourceRef"
import { captureSnapshot, type DashboardSnapshot } from "@/lib/api"
import {
  staggerContainer,
  staggerChild,
  fadeSlideVariants,
  SPRING_SNAPPY,
  useMotionEnabled,
} from "@/lib/motion"

const REFRESH_MS = 5000

export function MissionControl() {
  const [snap, setSnap] = useState<DashboardSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const motionEnabled = useMotionEnabled()

  const refresh = useCallback(async () => {
    try {
      const s = await captureSnapshot()
      setSnap(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed")
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(id)
  }, [refresh])

  if (!snap && !error) {
    return <LoadingSkeleton />
  }

  const perf = snap?.performance.data
  const sessions = snap?.sessions.data ?? []
  const agents = snap?.agents.data ?? []
  const health = snap?.health.data

  const busyAgents = agents.filter((a) => a.status === "busy").length
  const errorSessions = sessions.filter((s) => s.status === "error").length
  const staleSessions = sessions.filter((s) => s.status === "stale").length

  return (
    <div className="space-y-[var(--spacing-section)]">
      {/* Header */}
      <motion.div
        initial={motionEnabled ? { opacity: 0, y: 6 } : undefined}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
      >
        <h1 className="text-[var(--font-size-3xl)] font-bold tracking-tight">
          Mission Control
        </h1>
        <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1">
          Read-only telemetry · Kernel {health?.version ? `v${health.version}` : "—"} ·
          Last refresh: {snap?.captured_at ? new Date(snap.captured_at).toLocaleTimeString() : "—"}
        </p>
      </motion.div>

      {/* Metric row — staggered entrance using Motion variants */}
      <motion.div
        className="grid grid-cols-2 lg:grid-cols-4 gap-[var(--spacing-card)]"
        variants={motionEnabled ? staggerContainer() : undefined}
        initial="hidden"
        animate="visible"
      >
        <motion.div variants={motionEnabled ? staggerChild() : undefined}>
          <MetricCard
            label="Active Sessions"
            value={perf?.active_sessions ?? "—"}
            sub={sessions.length > 0 ? `${busyAgents} busy · ${sessions.length - busyAgents} other` : undefined}
            icon={<Users size={20} />}
          />
        </motion.div>
        <motion.div variants={motionEnabled ? staggerChild() : undefined}>
          <MetricCard
            label="Active Agents"
            value={perf?.active_agents ?? "—"}
            sub={agents.length > 0 ? `${busyAgents} running` : undefined}
            icon={<Activity size={20} />}
          />
        </motion.div>
        <motion.div variants={motionEnabled ? staggerChild() : undefined}>
          <MetricCard
            label="CPU"
            value={perf ? `${perf.cpu_percent.toFixed(1)}%` : "—"}
            sub={perf && perf.cpu_percent > 70 ? "High usage" : undefined}
            icon={<Cpu size={20} />}
          />
        </motion.div>
        <motion.div variants={motionEnabled ? staggerChild() : undefined}>
          <MetricCard
            label="Memory"
            value={perf ? `${perf.memory_mb.toFixed(0)} MB` : "—"}
            sub={perf && perf.memory_mb > 4096 ? "High usage" : undefined}
            icon={<MemoryStick size={20} />}
          />
        </motion.div>
      </motion.div>

      {/* Alerts row — only appears when there are issues (staging-dim-background) */}
      <AnimatePresence>
        {(errorSessions > 0 || staleSessions > 0) && (
          <motion.div
            initial={motionEnabled ? { opacity: 0, height: 0 } : undefined}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={SPRING_SNAPPY}
          >
            <Card className="border-error/30 bg-error/5 shadow-elevation-1">
              <CardContent className="p-[var(--spacing-card)]">
                <div className="flex items-center gap-2 text-error mb-2">
                  <AlertTriangle size={16} />
                  <span className="text-[var(--font-size-base)] font-medium">Attention Required</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {errorSessions > 0 && (
                    <span className="text-sm text-error">
                      {errorSessions} error{errorSessions !== 1 ? "s" : ""}
                    </span>
                  )}
                  {staleSessions > 0 && (
                    <span className="text-sm text-stale">
                      {staleSessions} stale
                    </span>
                  )}
                </div>
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Source provenance */}
      {snap && (
        <div className="space-y-2 text-[var(--font-size-sm)]">
          <h3 className="text-muted-foreground font-medium">Data Provenance</h3>
          <div className="grid gap-1">
            <SourceRef ref={snap.sessions.ref} />
            <SourceRef ref={snap.agents.ref} />
            <SourceRef ref={snap.performance.ref} />
            <SourceRef ref={snap.health.ref} />
          </div>
        </div>
      )}

      {/* Error banner */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={motionEnabled ? { opacity: 0 } : undefined}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <Card className="border-error/30 bg-error/5">
              <CardContent className="p-4 text-error text-sm">
                Connection error: {error}. Retrying…
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div className="space-y-[var(--spacing-section)]">
      <Skeleton className="h-8 w-48" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-[var(--spacing-card)]">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardContent className="p-[var(--spacing-card)] space-y-3">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-8 w-12" />
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}

