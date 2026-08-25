import { type ReactNode } from "react"
import { motion } from "motion/react"
import { Card, CardContent } from "@/components/ui/card"
import {
  SPRING_SNAPPY,
  SCALE_TAP,
  SCALE_HOVER,
  DURATION_DEFAULT,
  useMotionEnabled,
} from "@/lib/motion"
import { cn } from "@/lib/utils"

interface MetricCardProps {
  label: string
  value: string | number
  sub?: string
  icon?: ReactNode
  trend?: "up" | "down" | "flat"
  className?: string
}

/**
 * Dashboard metric card built on shadcn Card.
 *
 * Motion rules applied:
 * - physics-active-state: whileTap scale for interactive feedback
 * - physics-subtle-deformation: hover scale in 0.95–1.05 range
 * - staging-one-focal-point: value is the focal point, icon dims
 * - timing-under-300ms: all transitions under 300ms
 * - type-tabular-nums-for-data: value uses tabular figures
 */
export function MetricCard({ label, value, sub, icon, className }: MetricCardProps) {
  const motionEnabled = useMotionEnabled()

  return (
    <motion.div
      layout={motionEnabled}
      whileHover={motionEnabled ? { scale: SCALE_HOVER, y: -1 } : undefined}
      whileTap={motionEnabled ? { scale: SCALE_TAP } : undefined}
      transition={SPRING_SNAPPY}
    >
      <Card className={cn("group relative overflow-hidden", className)}>
        {/* Subtle gradient accent along top edge */}
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent" />
        <CardContent className="p-[var(--spacing-card)]">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-[var(--font-size-sm)] text-muted-foreground uppercase tracking-wider truncate">
                {label}
              </p>
              {/* type-tabular-nums-for-data: metrics use tabular figures */}
              <p className="text-[var(--font-size-3xl)] font-bold tracking-tight mt-1 tabular-nums">
                {value}
              </p>
              {sub && (
                <p className="text-[var(--font-size-sm)] text-muted-foreground mt-1.5 truncate">
                  {sub}
                </p>
              )}
            </div>
            {icon && (
              <div className="text-muted-foreground/40 transition-colors group-hover:text-muted-foreground/70 shrink-0">
                {icon}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </motion.div>
  )
}

