import { NavLink, useLocation } from "react-router-dom"
import { motion, AnimatePresence } from "motion/react"
import {
  LayoutDashboard,
  Users,
  Activity,
  Heart,
  Shield,
  Settings,
  Brain,
  PanelLeftClose,
  PanelLeftOpen,
  ChevronRight,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useSettings } from "@/lib/settings"
import { SPRING_GENTLE, useMotionEnabled } from "@/lib/motion"
import { cn } from "@/lib/utils"

const NAV_ITEMS = [
  { to: "/", icon: LayoutDashboard, label: "Mission Control" },
  { to: "/agents", icon: Users, label: "Agents & Sessions" },
  { to: "/events", icon: Activity, label: "Event Explorer" },
  { to: "/memory", icon: Brain, label: "Memory & Provenance" },
  { to: "/health", icon: Heart, label: "Source Health" },
  { to: "/actions", icon: Shield, label: "Action Gate" },
  { to: "/settings", icon: Settings, label: "Settings" },
]

/**
 * Sidebar using shadcn Button, Separator, and Tooltip primitives.
 *
 * Motion rules:
 * - physics-spring-for-overshoot: width animation uses gentle spring
 * - spring-for-interruptible: collapse is interruptible via spring physics
 * - none-keyboard-navigation: keyboard focus is instant (CSS only, no motion)
 * - timing-consistent: all nav items share identical timing
 */
export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useSettings()
  const location = useLocation()
  const motionEnabled = useMotionEnabled()
  const collapsed = sidebarCollapsed

  return (
    <TooltipProvider delayDuration={0}>
      <motion.aside
        initial={false}
        animate={{ width: collapsed ? 56 : 220 }}
        transition={motionEnabled ? SPRING_GENTLE : { duration: 0 }}
        className="h-screen flex flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground shrink-0 overflow-hidden relative"
      >
        {/* Brand */}
        <div className="flex items-center gap-2 px-3 h-14 border-b border-sidebar-border shrink-0">
          {!collapsed && (
            <span className="text-sm font-bold tracking-tight text-primary whitespace-nowrap">
              F.U.C.K.B.U.T.T.
            </span>
          )}
          {collapsed && (
            <span className="text-xs font-bold text-primary mx-auto">F</span>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 py-2 flex flex-col gap-0.5 px-1.5 overflow-y-auto" role="navigation" aria-label="Main navigation">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => {
            const isActive = location.pathname === to

            const link = (
              <NavLink
                key={to}
                to={to}
                className={cn(
                  "flex items-center gap-2.5 rounded-md text-sm transition-colors outline-none focus-visible:ring-1 focus-visible:ring-ring",
                  collapsed ? "justify-center px-0 py-2" : "px-2.5 py-1.5",
                  isActive
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
                )}
                aria-current={isActive ? "page" : undefined}
              >
                <Icon size={18} className="shrink-0" />
                <AnimatePresence>
                  {!collapsed && (
                    <motion.span
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.12 }}
                      className="whitespace-nowrap overflow-hidden"
                    >
                      {label}
                    </motion.span>
                  )}
                </AnimatePresence>
              </NavLink>
            )

            // When collapsed, wrap each nav item in a Tooltip
            if (collapsed) {
              return (
                <Tooltip key={to}>
                  <TooltipTrigger asChild>{link}</TooltipTrigger>
                  <TooltipContent side="right" sideOffset={8}>
                    {label}
                  </TooltipContent>
                </Tooltip>
              )
            }

            return link
          })}
        </nav>

        <Separator />

        {/* Collapse toggle */}
        <div className="p-1.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={toggleSidebar}
            className="w-full justify-center"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </Button>
        </div>
      </motion.aside>
    </TooltipProvider>
  )
}

/** Top bar when sidebar is collapsed — shows current page breadcrumb */
export function CollapsedTopBar() {
  const { sidebarCollapsed, toggleSidebar } = useSettings()
  const location = useLocation()

  if (!sidebarCollapsed) return null

  const current = NAV_ITEMS.find((n) => n.to === location.pathname)
  const label = current?.label ?? "Dashboard"

  return (
    <div className="h-11 flex items-center gap-3 px-4 border-b border-border bg-card/50 backdrop-blur-sm shrink-0">
      <Button
        variant="ghost"
        size="icon"
        onClick={toggleSidebar}
        aria-label="Open sidebar"
        className="h-7 w-7"
      >
        <PanelLeftOpen size={14} />
      </Button>
      <ChevronRight size={12} className="text-muted-foreground" />
      <span className="text-sm font-medium">{label}</span>
    </div>
  )
}

