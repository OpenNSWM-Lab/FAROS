import { useState } from 'react'
import { SettingsLayout } from '@/components/layout/SettingsLayout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { CheckCircle2 } from 'lucide-react'
import { useTheme } from '@/lib/hooks/use-theme'

const STORAGE_KEY = 'faros.preferences'

interface PreferencesData {
  density: 'comfortable' | 'compact'
  tableRowSize: number
  enableNotifications: boolean
  autoSaveDrafts: boolean
  showLineNumbers: boolean
}

const defaultPreferences: PreferencesData = {
  density: 'comfortable',
  tableRowSize: 48,
  enableNotifications: true,
  autoSaveDrafts: true,
  showLineNumbers: false,
}

function loadPreferences(): PreferencesData {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const parsed = JSON.parse(stored)
      return { ...defaultPreferences, ...parsed }
    }
  } catch {
    // corrupted data — fall through to defaults
  }
  return { ...defaultPreferences }
}

export function Preferences() {
  const { theme, setTheme } = useTheme()
  const [prefs, setPrefs] = useState<PreferencesData>(loadPreferences)
  const [showToast, setShowToast] = useState(false)

  const updatePref = <K extends keyof PreferencesData>(key: K, value: PreferencesData[K]) => {
    setPrefs((prev) => ({ ...prev, [key]: value }))
  }

  const handleSave = () => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
      setShowToast(true)
      setTimeout(() => setShowToast(false), 3000)
    } catch {
      // localStorage unavailable (private browsing, quota exceeded)
    }
  }

  return (
    <SettingsLayout>
      <div className="space-y-6">
        {showToast && (
          <div className="fixed top-4 right-4 z-50 bg-primary text-primary-foreground px-4 py-3 rounded-md shadow-lg flex items-center gap-2 animate-in slide-in-from-top">
            <CheckCircle2 className="h-4 w-4" />
            <span className="text-sm font-medium">Preferences saved</span>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
            <CardDescription>Theme and display preferences</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Theme</label>
              <select
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={theme}
                onChange={(e) => setTheme(e.target.value as typeof theme)}
              >
                <option value="light">Light</option>
                <option value="dark">Dark</option>
                <option value="system">System</option>
              </select>
              <p className="text-xs text-muted-foreground">
                Choose your preferred color scheme
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">Density</label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => updatePref('density', 'comfortable')}
                  className={`flex-1 px-4 py-2 rounded-md text-sm font-medium transition-colors ${prefs.density === 'comfortable'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted hover:bg-muted/80'
                    }`}
                >
                  Comfortable
                </button>
                <button
                  type="button"
                  onClick={() => updatePref('density', 'compact')}
                  className={`flex-1 px-4 py-2 rounded-md text-sm font-medium transition-colors ${prefs.density === 'compact'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted hover:bg-muted/80'
                    }`}
                >
                  Compact
                </button>
              </div>
              <p className="text-xs text-muted-foreground">
                Adjust spacing and padding throughout the UI
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Table Settings</CardTitle>
            <CardDescription>Customize table display</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Row Height: {prefs.tableRowSize}px</label>
              <input
                type="range"
                min="32"
                max="64"
                step="4"
                value={prefs.tableRowSize}
                onChange={(e) => updatePref('tableRowSize', parseInt(e.target.value))}
                className="w-full"
              />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>Compact (32px)</span>
                <span>Comfortable (64px)</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Editor</CardTitle>
            <CardDescription>Code and text editor preferences</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <label className="text-sm font-medium">Show Line Numbers</label>
                <p className="text-xs text-muted-foreground">Display line numbers in code editors</p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={prefs.showLineNumbers}
                onClick={() => updatePref('showLineNumbers', !prefs.showLineNumbers)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${prefs.showLineNumbers ? 'bg-primary' : 'bg-input'
                  }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-background transition-transform ${prefs.showLineNumbers ? 'translate-x-6' : 'translate-x-1'
                    }`}
                />
              </button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Notifications</CardTitle>
            <CardDescription>Configure notification preferences</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <label className="text-sm font-medium">Enable Notifications</label>
                <p className="text-xs text-muted-foreground">Show notifications for run completion and errors</p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={prefs.enableNotifications}
                onClick={() => updatePref('enableNotifications', !prefs.enableNotifications)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${prefs.enableNotifications ? 'bg-primary' : 'bg-input'
                  }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-background transition-transform ${prefs.enableNotifications ? 'translate-x-6' : 'translate-x-1'
                    }`}
                />
              </button>
            </div>

            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <label className="text-sm font-medium">Auto-save Drafts</label>
                <p className="text-xs text-muted-foreground">Automatically save paper drafts every 30 seconds</p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={prefs.autoSaveDrafts}
                onClick={() => updatePref('autoSaveDrafts', !prefs.autoSaveDrafts)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${prefs.autoSaveDrafts ? 'bg-primary' : 'bg-input'
                  }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-background transition-transform ${prefs.autoSaveDrafts ? 'translate-x-6' : 'translate-x-1'
                    }`}
                />
              </button>
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-end">
          <Button onClick={handleSave}>Save Preferences</Button>
        </div>
      </div>
    </SettingsLayout>
  )
}
