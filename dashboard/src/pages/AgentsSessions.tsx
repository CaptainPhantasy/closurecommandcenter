import { useEffect, useState, useCallback } from "react"
import { motion, AnimatePresence } from "motion/react"
import { Search, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent } from "@/components/ui/card"
import { StatusBadge } from "@/components/StatusBadge"
import { SourceRef, type SourceRefData } from "@/components/SourceRef"
import { fetchSessions, fetchAgents, type KernelSession, type KernelAgent } from "@/lib/api"
import { layoutTransition, useMotionEnabled, STAGGER_MS, DURATION_DEFAULT } from "@/lib/motion"

export function AgentsSessions() {
  const [sessions, setSessions] = useState<{ data: KernelSession[] | null; ref: SourceRefData }>({ data: null, ref: { url: "", fetched_at: "", status: "error" } })
  const [agents, setAgents] = useState<{ data: KernelAgent[] | null; ref: SourceRefData }>({ data: null, ref: { url: "", fetched_at: "", status: "error" } })
  const [filter, setFilter] = useState("")
  const motionEnabled = useMotionEnabled()

  const refresh = useCallback(async () => {
    const [s, a] = await Promise.all([fetchSessions(), fetchAgents()])
    setSessions(s)
    setAgents(a)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const filtered = (sessions.data ?? []).filter((s) => {
    if (!filter) return true
    const label = (s.name ?? s.id).toLowerCase()
    return label.includes(filter.toLowerCase()) || s.id.toLowerCase().includes(filter.toLowerCase())
  })

  return (
    <div className="space-y-[var(--spacing-section)]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[var(--font-size-3xl)] font-bold tracking-tight">
            Agents & Sessions
          </h1>
          <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1">
            {sessions.data ? `${sessions.data.length} sessions · ${agents.data?.length ?? 0} agents` : "Loading…"}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          <RefreshCw size={14} />
          Refresh
        </Button>
      </div>

      {/* Search — shadcn Input */}
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
        <Input
          type="text"
          placeholder="Filter by agent or session ID…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="pl-9"
        />
      </div>

      {/* Session list — layout animations for reordering (spring-for-interruptible) */}
      <div className="space-y-1" role="list" aria-label="Sessions">
        <AnimatePresence mode="popLayout">
          {filtered.map((s) => (
            <motion.div
              key={s.id}
              layout={motionEnabled}
              initial={motionEnabled ? { opacity: 0, x: -6 } : undefined}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, scale: 0.97 }}
              transition={motionEnabled ? { layout: layoutTransition(), opacity: { duration: DURATION_DEFAULT } } : { duration: 0 }}
              role="listitem"
            >
              <Card className="hover:bg-muted/30 transition-colors">
                <CardContent className="flex items-center gap-3 px-[var(--spacing-card)] py-2">
                  <StatusBadge status={mapStatus(s.status)} showDot />
                  <div className="flex-1 min-w-0">
                    <div className="text-[var(--font-size-base)] font-medium truncate">{s.name ?? s.id}</div>
                    <div className="text-[var(--font-size-sm)] text-muted-foreground truncate font-mono tabular-nums">
                      {s.id}{s.command ? ` · ${s.command}` : ""}
                    </div>
                  </div>
                  {s.pid && (
                    <span className="text-xs text-muted-foreground font-mono shrink-0 tabular-nums">
                      PID {s.pid}
                    </span>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </AnimatePresence>
        {filtered.length === 0 && sessions.data && (
          <div className="text-center py-8 text-muted-foreground text-[var(--font-size-sm)]">
            No sessions match filter.
          </div>
        )}
      </div>

      {/* Provenance */}
      <div className="space-y-1">
        <SourceRef ref={sessions.ref} />
        <SourceRef ref={agents.ref} />
      </div>
    </div>
  )
}

function mapStatus(s: string): "healthy" | "busy" | "error" | "stale" | "waiting" | "idle" {
  const map: Record<string, "healthy" | "busy" | "error" | "stale" | "waiting" | "idle"> = {
    ready: "healthy", busy: "busy", waiting_for_input: "waiting",
    error: "error", stale: "stale", idle: "idle",
  }
  return map[s] ?? "idle"
}

