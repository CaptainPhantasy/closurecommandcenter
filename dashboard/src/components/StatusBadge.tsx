import { motion } from "motion/react"
import { Badge } from "@/components/ui/badge"
import { scaleInVariants, SPRING_SNAPPY, useMotionEnabled } from "@/lib/motion"
import { useSettings, type StatusStyle } from "@/lib/settings"
import { cn } from "@/lib/utils"

type Status = "healthy" | "stale" | "error" | "critical" | "waiting" | "idle" | "busy"

interface StatusBadgeProps {
  status: Status
  style?: StatusStyle
  showDot?: boolean
  className?: string
}

/**
 * Status indicator built on shadcn Badge with Motion spring physics.
 *
 * Motion rules applied:
 * - physics-spring-for-overshoot: scale transition uses spring
 * - timing-under-300ms: spring params settle under 300ms
 * - staging-one-focal-point: status dot pulses only for critical states
 */
export function StatusBadge({ status, style: propStyle, showDot = true, className }: StatusBadgeProps) {
  const settings = useSettings()
  const statusStyle = propStyle ?? settings.statusStyle
  const motionEnabled = useMotionEnabled()

  const dot = showDot ? (
    statusStyle === "pulse" && motionEnabled && (status === "stale" || status === "error" || status === "critical") ? (
      <motion.span
        className={cn("inline-block w-1.5 h-1.5 rounded-full bg-current")}
        animate={{
          opacity: [1, 0.4, 1],
          scale: status === "critical" ? [1, 1.4, 1] : [1, 1.15, 1],
        }}
        transition={{
          duration: status === "critical" ? 0.6 : 1.5,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />
    ) : (
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-current" />
    )
  ) : null

  return (
    <motion.div
      key={status}
      initial={statusStyle === "shift" && motionEnabled ? { scale: 1.08, opacity: 0.6 } : false}
      animate={{ scale: 1, opacity: 1 }}
      transition={motionEnabled ? SPRING_SNAPPY : { duration: 0 }}
      className="inline-flex"
    >
      <Badge
        variant={status as "healthy" | "stale" | "error" | "critical" | "waiting" | "idle" | "busy"}
        className={cn("gap-1.5", className)}
      >
        {dot}
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </Badge>
    </motion.div>
  )
}

