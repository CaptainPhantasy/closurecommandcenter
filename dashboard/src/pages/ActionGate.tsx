import { Shield, Lock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"

export function ActionGate() {
  return (
    <div className="space-y-[var(--spacing-section)]">
      <div>
        <h1 className="text-[var(--font-size-3xl)] font-bold tracking-tight">Action Gate</h1>
        <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1">
          Mutation controls · V1 read-only mode
        </p>
      </div>

      <Card className="border-primary/20 bg-primary/5 shadow-elevation-1">
        <CardContent className="p-8 text-center space-y-4">
          <div className="flex justify-center">
            <div className="rounded-full bg-primary/10 p-4">
              <Lock size={32} className="text-primary" />
            </div>
          </div>
          <div>
            <h2 className="text-[var(--font-size-xl)] font-semibold">Read-Only Mode Active</h2>
            <p className="text-[var(--font-size-sm)] text-muted-foreground mt-2 max-w-md mx-auto">
              All mutation controls are disabled in V1. No restart, kill, cancel, write, delete,
              or command-run operations are available. Every recommended action requires explicit approval.
            </p>
          </div>
          <div className="flex items-center justify-center gap-2 text-[var(--font-size-sm)] text-muted-foreground">
            <Shield size={14} />
            <span>All RecommendedAction objects have <code className="text-primary">requires_approval: true</code></span>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

