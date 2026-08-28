import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Code2, ArrowLeft, Play, Loader2 } from 'lucide-react'
import { createSession, startSession } from '@/lib/api/code'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''
const DEFAULT_REPO_PATH = import.meta.env.VITE_DEFAULT_REPO_PATH || ''

export function NewCodeSession() {
  const navigate = useNavigate()
  
  const [repoPath, setRepoPath] = useState(DEFAULT_REPO_PATH)
  const [goal, setGoal] = useState('')
  const [maxCandidates, setMaxCandidates] = useState(3)
  const [constraints, setConstraints] = useState('')
  const [settingsLlmLabel, setSettingsLlmLabel] = useState<string | null>(null)
  
  const [isCreating, setIsCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/v1/providers`)
        if (!r.ok || cancelled) return
        const data = await r.json()
        const pname = data.activeProvider || ''
        const providerInfo = (data.providers || []).find(
          (p: { providerName: string; model: string }) => p.providerName === pname
        )
        const m = providerInfo?.model || ''
        if (!cancelled) setSettingsLlmLabel(`${pname} / ${m}`)
      } catch {
        if (!cancelled) setSettingsLlmLabel(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const handleCreate = async (autoStart: boolean = false) => {
    if (!repoPath.trim() || !goal.trim()) {
      setError('Repository path and goal are required')
      return
    }

    setIsCreating(true)
    setError(null)

    try {
      const session = await createSession({
        repoPath: repoPath.trim(),
        goal: goal.trim(),
        maxCandidates,
        constraints: constraints.trim() || undefined,
      })

      if (autoStart) {
        await startSession(session.id)
      }

      navigate(`/code/${session.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create session')
      setIsCreating(false)
    }
  }

  return (
    <AppPageLayout
      title="New Code Session"
      subtitle="Configure and start a code generation session"
      icon={Code2}
      iconColor="violet"
      accentColor="violet"
    >
      <Button variant="outline" onClick={() => navigate('/code')} className="mb-6">
        <ArrowLeft className="h-4 w-4 mr-2" /> Back to Dashboard
      </Button>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <p className="text-red-800 text-sm">{error}</p>
        </div>
      )}

      <p className="text-sm text-muted-foreground mb-4">
        代码生成将使用设置中的活跃 LLM（当前：{settingsLlmLabel ?? '加载中…'}）。请到{' '}
        <button type="button" className="text-violet-600 underline" onClick={() => navigate('/settings/providers')}>
          设置 → LLM 提供商
        </button>
        {' '}修改。
      </p>

      <div className="grid gap-6 lg:grid-cols-1 max-w-2xl">
        {/* Main Config */}
        <Card>
          <CardHeader>
            <CardTitle>Session Configuration</CardTitle>
            <CardDescription>Define what code to generate</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">Repository Path *</label>
              <input
                type="text"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                className="w-full px-3 py-2 border rounded-md text-sm"
                placeholder="/path/to/repository"
              />
              <p className="text-xs text-muted-foreground mt-1">
                Absolute path to the repository to analyze
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Goal *</label>
              <textarea
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                className="w-full px-3 py-2 border rounded-md text-sm min-h-24"
                placeholder="Describe what code you want to generate or modify..."
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Constraints (Optional)</label>
              <textarea
                value={constraints}
                onChange={(e) => setConstraints(e.target.value)}
                className="w-full px-3 py-2 border rounded-md text-sm min-h-16"
                placeholder="Any constraints or requirements..."
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Max Candidates</label>
              <select
                value={maxCandidates}
                onChange={(e) => setMaxCandidates(Number(e.target.value))}
                className="w-full px-3 py-2 border rounded-md text-sm"
              >
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
                <option value={5}>5</option>
              </select>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-4 mt-6">
        <Button
          onClick={() => handleCreate(true)}
          disabled={isCreating || !repoPath.trim() || !goal.trim()}
          className="bg-violet-500 hover:bg-violet-600"
        >
          {isCreating ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <Play className="h-4 w-4 mr-2" />
          )}
          Create & Start
        </Button>
        <Button
          variant="outline"
          onClick={() => handleCreate(false)}
          disabled={isCreating || !repoPath.trim() || !goal.trim()}
        >
          Create Only
        </Button>
      </div>
    </AppPageLayout>
  )
}
