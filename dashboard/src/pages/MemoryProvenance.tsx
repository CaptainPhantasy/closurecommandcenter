import { Brain, CheckCircle2, XCircle, ExternalLink, Clock, Tag } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { motion, AnimatePresence } from "motion/react"
import { captureSnapshot, type DashboardSnapshot, type SourceRef } from "@/lib/api"
import { useSettings } from "@/lib/settings"
import { StatusBadge } from "@/components/StatusBadge"
import { staggerChild, staggerContainer, useMotionEnabled, SPRING_SNAPPY } from "@/lib/motion"
import { useEffect, useState, useCallback } from "react"

// Types matching the Python MemoryCandidate schema
interface MemoryCandidate {
  candidate_id: string
  summary: string
  category: string
  source: SourceRef
  evidence_snippet: string
  approved: boolean
  rejected: boolean
  timestamp: number
}

// Local candidate generator — mirrors Python memory.py logic client-side
function generateLocalCandidates(snap: DashboardSnapshot): MemoryCandidate[] {
  const candidates: MemoryCandidate[] = []
  const now = Date.now() / 1000

  if (snap.sessions.data) {
    for (const s of snap.sessions.data) {
      if (s.status === "error") {
        candidates.push({
          candidate_id: `mc-${now.toFixed(0)}-${s.id}`,
          summary: `Session '${s.name ?? s.id}' entered error state`,
          category: "incident",
          source: snap.sessions.ref,
          evidence_snippet: `session_id=${s.id} status=error`,
          approved: false, rejected: false, timestamp: now,
        })
      }
      if (s.status === "stale") {
        candidates.push({
          candidate_id: `mc-${now.toFixed(0)}-${s.id}-stale`,
          summary: `Session '${s.name ?? s.id}' went stale`,
          category: "incident",
          source: snap.sessions.ref,
          evidence_snippet: `session_id=${s.id} status=stale`,
          approved: false, rejected: false, timestamp: now,
        })
      }
    }
  }

  if (snap.performance.data) {
    const p = snap.performance.data
    if (p.cpu_percent >= 85) {
      candidates.push({
        candidate_id: `mc-${now.toFixed(0)}-cpu`,
        summary: `Sustained high CPU: ${p.cpu_percent.toFixed(1)}%`,
        category: "metric_threshold",
        source: snap.performance.ref,
        evidence_snippet: `cpu_percent=${p.cpu_percent.toFixed(1)} active_agents=${p.active_agents}`,
        approved: false, rejected: false, timestamp: now,
      })
    }
    if (p.memory_mb >= 6144) {
      candidates.push({
        candidate_id: `mc-${now.toFixed(0)}-mem`,
        summary: `High memory usage: ${p.memory_mb.toFixed(0)} MB`,
        category: "metric_threshold",
        source: snap.performance.ref,
        evidence_snippet: `memory_mb=${p.memory_mb.toFixed(0)}`,
        approved: false, rejected: false, timestamp: now,
      })
    }
  }

  return candidates
}

const CATEGORY_VARIANT: Record<string, "destructive" | "secondary" | "default" | "outline"> = {
  incident: "destructive",
  metric_threshold: "outline",
  pattern: "default",
  behavior: "secondary",
}

export function MemoryProvenance() {
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const motionEnabled = useMotionEnabled()

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const snap = await captureSnapshot()
      setCandidates(generateLocalCandidates(snap))
    } catch {
      setError("Failed to capture snapshot for candidate generation")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <div className="space-y-[var(--spacing-section)]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[var(--font-size-3xl)] font-bold tracking-tight">
            Memory & Provenance
          </h1>
          <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1">
            Memory candidates derived from live telemetry · V1 preview (writes disabled)
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <Brain size={14} />
          Scan
        </Button>
      </div>

      {/* Provenance model explanation */}
      <Card className="bg-muted/30 border-border">
        <CardContent className="p-4 text-sm text-muted-foreground space-y-2">
          <p className="font-medium text-foreground">Provenance Model</p>
          <p>
            Every memory candidate carries a <code className="text-primary">SourceRef</code> pointing
            to the exact API endpoint, timestamp, and fetch status that produced it. In V1, all
            approve/reject actions are disabled — candidates are proposals only.
          </p>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/20 bg-destructive/5">
          <CardContent className="p-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {loading && candidates.length === 0 && (
        <div className="text-center py-12 text-muted-foreground text-sm">
          Scanning telemetry for memory candidates…
        </div>
      )}

      {!loading && !error && candidates.length === 0 && (
        <div className="text-center py-12 text-muted-foreground text-sm">
          No memory candidates detected from current telemetry state.
        </div>
      )}

      {/* Candidate list — staggered entrance */}
      <motion.div
        className="space-y-3"
        variants={motionEnabled ? staggerContainer() : undefined}
        initial="hidden"
        animate="visible"
      >
        <AnimatePresence mode="popLayout">
          {candidates.map((c) => (
            <motion.div
              key={c.candidate_id}
              variants={motionEnabled ? staggerChild() : undefined}
              layout={motionEnabled}
              transition={SPRING_SNAPPY}
            >
              <CandidateCard candidate={c} />
            </motion.div>
          ))}
        </AnimatePresence>
      </motion.div>

      {candidates.length > 0 && (
        <p className="text-xs text-muted-foreground text-center tabular-nums">
          {candidates.length} candidate{candidates.length !== 1 ? "s" : ""} · all require human review · V1 writes disabled
        </p>
      )}
    </div>
  )
}

function CandidateCard({ candidate }: { candidate: MemoryCandidate }) {
  const timeStr = new Date(candidate.timestamp * 1000).toLocaleTimeString()

  return (
    <Card className="shadow-elevation-1">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium leading-snug">{candidate.summary}</p>
            <div className="flex items-center gap-2 mt-1.5">
              <Badge variant={CATEGORY_VARIANT[candidate.category] ?? "secondary"}>
                <Tag size={10} className="mr-1" />
                {candidate.category}
              </Badge>
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
                <Clock size={10} />
                {timeStr}
              </span>
            </div>
          </div>
          {candidate.approved && (
            <span className="flex items-center gap-1 text-xs text-healthy font-medium">
              <CheckCircle2 size={14} />
              Approved
            </span>
          )}
          {candidate.rejected && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground line-through">
              <XCircle size={14} />
              Rejected
            </span>
          )}
        </div>

        {candidate.evidence_snippet && (
          <div className="rounded bg-muted/50 px-3 py-2 text-xs font-mono text-muted-foreground">
            {candidate.evidence_snippet}
          </div>
        )}

        {/* Source */}
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <ExternalLink size={10} />
          <span className="font-mono">{candidate.source.url}</span>
          <StatusBadge status={candidate.source.status === "ok" ? "healthy" : "error"} showDot />
        </span>

        {!candidate.approved && !candidate.rejected && (
          <div className="flex items-center gap-2 pt-1">
            <Button size="sm" variant="outline" disabled className="opacity-50" title="Memory writes are disabled in V1">
              <CheckCircle2 size={12} />
              Approve
            </Button>
            <Button size="sm" variant="ghost" disabled className="opacity-50" title="Memory writes are disabled in V1">
              <XCircle size={12} />
              Reject
            </Button>
            <span className="text-xs text-muted-foreground italic ml-2">
              V1: memory writes disabled
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

