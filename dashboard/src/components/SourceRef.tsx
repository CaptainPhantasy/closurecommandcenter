import { useState } from "react"
import { ExternalLink, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export interface SourceRefData {
  url: string
  fetched_at: string
  status: "ok" | "stale" | "error"
}

interface SourceRefProps {
  ref: SourceRefData
  className?: string
}

/**
 * Data provenance indicator using shadcn Button for toggle.
 *
 * UX rules applied:
 * - ux-progressive-disclosure: collapsed by default, expand for details
 * - visual-concentric-radius: inner content uses smaller radius
 */
export function SourceRef({ ref: r, className }: SourceRefProps) {
  const [expanded, setExpanded] = useState(false)

  const statusColor = r.status === "ok"
    ? "text-healthy"
    : r.status === "stale"
      ? "text-stale"
      : "text-error"

  return (
    <div className={cn("text-[var(--font-size-sm)]", className)}>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="h-auto p-0 text-muted-foreground hover:text-foreground gap-1 font-normal"
      >
        <ChevronDown
          size={12}
          className={cn("transition-transform", expanded ? "rotate-0" : "-rotate-90")}
        />
        <span className={statusColor}>●</span>
        <span>Source</span>
      </Button>
      {expanded && (
        <div className="mt-1 ml-5 space-y-0.5 text-muted-foreground rounded-md bg-muted/30 p-2">
          <div className="flex items-center gap-1">
            <span className={statusColor}>{r.status}</span>
            <span>·</span>
            <time dateTime={r.fetched_at}>
              {new Date(r.fetched_at).toLocaleTimeString()}
            </time>
          </div>
          <div className="flex items-center gap-1 font-mono text-xs break-all">
            <ExternalLink size={10} className="shrink-0" />
            {r.url}
          </div>
        </div>
      )}
    </div>
  )
}

