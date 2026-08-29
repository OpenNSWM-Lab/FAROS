import type { ReactNode } from 'react'

interface SettingsLayoutProps {
  children: ReactNode
}

export function SettingsLayout({ children }: SettingsLayoutProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto w-full max-w-[1480px] px-4 py-8 sm:px-6 lg:px-10" data-testid="settings-panel">
        {children}
      </div>
    </div>
  )
}
