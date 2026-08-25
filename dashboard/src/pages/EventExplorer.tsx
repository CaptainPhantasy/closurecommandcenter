import { useEffect, useState } from "react"
import { Activity, Pause, Play } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { connectEventStream } from "@/lib/api"

interface LogEvent {
  id: string
  type: string
  data: unknown
  timestamp: string
}

/**
 * Live event stream viewer.
 *
 * Motion rules applied:
 * - none-high-frequency: NO AnimatePresence on individual events.
 *   Events stream in rapidly — animating each one is wasteful and janky.
 *   Instead, we use CSS-only transitions on scroll and a simple count badge.
 * - ux-doherty-under-400ms: new events appear instantly (no delay)
 * - ux-millers-chunking: limit to 200 visible events (chunking for scannability)
 */
export function EventExplorer() {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    if (paused) return
    const es = connectEventStream(
      (e) => {
        try {
          const parsed = JSON.parse(e.data)
          setEvents((prev) => [
            { id: crypto.randomUUID(), type: e.type, data: parsed, timestamp: new Date().toISOString() },
            ...prev,
          ].slice(0, 200))
        } catch {
          setEvents((prev) => [
            { id: crypto.randomUUID(), type: e.type, data: e.data, timestamp: new Date().toISOString() },
            ...prev,
          ].slice(0, 200))
        }
      },
      () => setPaused(true),
    )
    return () => es?.close()
  }, [paused])

  return (
    <div className="space-y-[var(--spacing-section)]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[var(--font-size-3xl)] font-bold tracking-tight">Event Explorer</h1>
          <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1">
            <span className="tabular-nums">{events.length}</span> events · {paused ? "Paused" : "Streaming"}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setPaused(!paused)}
        >
          {paused ? <Play size={14} /> : <Pause size={14} />}
          {paused ? "Resume" : "Pause"}
        </Button>
      </div>

      <Card>
        <CardContent className="p-0 overflow-hidden">
          <div className="max-h-[70vh] overflow-y-auto divide-y divide-border">
            {events.map((e) => (
              <div
                key={e.id}
                className="px-[var(--spacing-card)] py-2 text-[var(--font-size-sm)] font-mono"
              >
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground tabular-nums">
                    {new Date(e.timestamp).toLocaleTimeString()}
                  </span>
                  <span className="text-primary font-semibold">{e.type}</span>
                </div>
                <pre className="mt-1 text-muted-foreground text-xs overflow-x-auto whitespace-pre-wrap">
                  {typeof e.data === "string" ? e.data : JSON.stringify(e.data, null, 2)}
                </pre>
              </div>
            ))}
            {events.length === 0 && (
              <div className="py-12 text-center text-muted-foreground">
                <Activity size={24} className="mx-auto mb-2 opacity-40" />
                {paused ? "Paused — click Resume to reconnect" : "Waiting for events…"}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

