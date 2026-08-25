import { useEffect } from "react"
import { BrowserRouter, Routes, Route } from "react-router-dom"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useSettings, applySettingsToDom } from "@/lib/settings"
import { Sidebar, CollapsedTopBar } from "@/components/Sidebar"
import { MissionControl } from "@/pages/MissionControl"
import { AgentsSessions } from "@/pages/AgentsSessions"
import { EventExplorer } from "@/pages/EventExplorer"
import { SourceHealth } from "@/pages/SourceHealth"
import { ActionGate } from "@/pages/ActionGate"
import { SettingsPage } from "@/pages/Settings"
import { MemoryProvenance } from "@/pages/MemoryProvenance"

function SettingsApplier({ children }: { children: React.ReactNode }) {
  const settings = useSettings()

  useEffect(() => {
    applySettingsToDom(settings)
  }, [settings.theme, settings.density, settings.motion, settings.statusStyle])

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <CollapsedTopBar />
        <main className="flex-1 overflow-y-auto p-[var(--spacing-page)]">
          {children}
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <TooltipProvider delayDuration={300}>
        <SettingsApplier>
          <Routes>
            <Route path="/" element={<MissionControl />} />
            <Route path="/agents" element={<AgentsSessions />} />
            <Route path="/events" element={<EventExplorer />} />
            <Route path="/health" element={<SourceHealth />} />
            <Route path="/actions" element={<ActionGate />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/memory" element={<MemoryProvenance />} />
          </Routes>
        </SettingsApplier>
      </TooltipProvider>
    </BrowserRouter>
  )
}

