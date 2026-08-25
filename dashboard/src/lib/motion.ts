/**
 * Centralized motion configuration.
 *
 * BLEED playbook §Implementation Order #3: "Build a local wrapper.
 * Do not scatter third-party APIs through product code."
 *
 * UI Wiki rules applied:
 * - timing-under-300ms: all user-facing animations under 300ms
 * - physics-active-state: interactive elements need :active/tap scale
 * - physics-spring-for-overshoot: springs for overshoot-and-settle
 * - physics-no-excessive-stagger: stagger under 50ms per item
 * - staging-one-focal-point: one prominent animation at a time
 * - easing-entrance-ease-out: entrances use ease-out
 * - easing-exit-ease-in: exits use ease-in
 * - none-high-frequency: no animation on high-frequency interactions
 * - none-keyboard-navigation: keyboard nav instant
 * - spring-for-gestures: gesture-driven motion uses springs
 * - spring-for-interruptible: interruptible motion uses springs
 */

import { type Transition, type Variants } from "motion/react"
import { useSettings, type MotionLevel } from "./settings"
import { useReducedMotion } from "motion/react"

// ─── Spring presets ─────────────────────────────────────────────────────────

/** Gentle settle — sidebar, panel expand/collapse */
export const SPRING_GENTLE: Transition = {
  type: "spring",
  stiffness: 250,
  damping: 30,
  mass: 0.8,
}

/** Snappy response — cards, badges, interactive elements */
export const SPRING_SNAPPY: Transition = {
  type: "spring",
  stiffness: 400,
  damping: 30,
  mass: 0.6,
}

/** Overshoot settle — drag release, layout transitions */
export const SPRING_BOUNCE: Transition = {
  type: "spring",
  stiffness: 300,
  damping: 20,
  mass: 0.8,
}

// ─── Duration presets (all under 300ms per timing-under-300ms) ──────────────

export const DURATION_FAST = 0.15   // 150ms — press/hover feedback
export const DURATION_DEFAULT = 0.2 // 200ms — small state changes
export const DURATION_SLOW = 0.25   // 250ms — enter/exit animations

// ─── Stagger ────────────────────────────────────────────────────────────────

/** Max 50ms per item (physics-no-excessive-stagger) */
export const STAGGER_MS = 0.04 // 40ms

// ─── Active state scale (physics-active-state, physics-subtle-deformation) ──

export const SCALE_TAP = 0.97
export const SCALE_HOVER = 1.01

// ─── Hook: motion-aware transition ──────────────────────────────────────────

/** Returns a transition that respects the user's motion preference */
export function useMotionTransition(level?: MotionLevel): Transition | false {
  const { motion } = useSettings()
  const shouldReduceMotion = useReducedMotion()
  const effective = level ?? motion

  if (shouldReduceMotion || effective === "none") return { duration: 0 }
  if (effective === "moderate") return SPRING_SNAPPY
  return SPRING_BOUNCE
}

/** Returns whether any motion should play */
export function useMotionEnabled(): boolean {
  const { motion } = useSettings()
  const shouldReduceMotion = useReducedMotion()
  return motion !== "none" && !shouldReduceMotion
}

// ─── Variant factories ──────────────────────────────────────────────────────

/** Fade + slide up entrance, reverse exit. For cards, panels, modals. */
export function fadeSlideVariants(y = 8): Variants {
  return {
    hidden: { opacity: 0, y },
    visible: {
      opacity: 1,
      y: 0,
      transition: { duration: DURATION_DEFAULT, ease: [0.25, 0.46, 0.45, 0.94] },
    },
    exit: {
      opacity: 0,
      y,
      transition: { duration: DURATION_FAST, ease: [0.55, 0.085, 0.68, 0.53] },
    },
  }
}

/** Staggered container — wraps children with staggered delay */
export function staggerContainer(staggerMs = STAGGER_MS): Variants {
  return {
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: { staggerChildren: staggerMs },
    },
  }
}

/** Staggered child — pair with staggerContainer */
export function staggerChild(y = 6): Variants {
  return {
    hidden: { opacity: 0, y },
    visible: {
      opacity: 1,
      y: 0,
      transition: { duration: DURATION_DEFAULT, ease: [0.25, 0.46, 0.45, 0.94] },
    },
  }
}

/** Scale entrance for badges, pills, status indicators */
export function scaleInVariants(): Variants {
  return {
    hidden: { opacity: 0, scale: 0.85 },
    visible: {
      opacity: 1,
      scale: 1,
      transition: { type: "spring", stiffness: 400, damping: 25 },
    },
    exit: {
      opacity: 0,
      scale: 0.85,
      transition: { duration: DURATION_FAST },
    },
  }
}

/** Layout transition config for reordering lists */
export function layoutTransition(): Transition {
  return {
    type: "spring",
    stiffness: 350,
    damping: 30,
    mass: 0.7,
  }
}

