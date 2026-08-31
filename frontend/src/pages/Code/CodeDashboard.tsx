import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Code2, Plus, RefreshCw, Clock, CheckCircle2, XCircle, Loader2, FolderGit2 } from 'lucide-react'
import { listSessions, CodeSession } from '@/lib/api/code'

const statusColors: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800 border-yellow-300',
  running: 'bg-blue-100 text-blue-800 border-blue-300',
  completed: 'bg-green-100 text-green-800 border-green-300',
  failed: 'bg-red-100 text-red-800 border-red-300',
  cancelled: 'bg-gray-100 text-gray-800 border-gray-300',
}

const statusIcons: Record<string, React.ReactNode> = {
  pending: <Clock className="h-3 w-3" />,
  running: <Loader2 className="h-3 w-3 animate-spin" />,
  completed: <CheckCircle2 className="h-3 w-3" />,
  failed: <XCircle className="h-3 w-3" />,
  cancelled: <XCircle className="h-3 w-3" />,
}

export function CodeDashboard() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<CodeSession[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadSessions = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await listSessions()
      setSessions(data.sessions)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sessions')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadSessions()
    const interval = setInterval(loadSessions, 10000)
    return () => clearInterval(interval)
  }, [loadSessions])

  return (
    <AppPageLayout
      title="Code Generation"
      subtitle="Generate and evaluate repository-level code changes"
      icon={Code2}
      iconColor="violet"
      accentColor="violet"
    >
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <Button onClick={() => navigate('/code/new')} className="bg-violet-500 hover:bg-violet-600">
            <Plus className="h-4 w-4 mr-2" /> New Session
          </Button>
          <Button variant="outline" onClick={loadSessions} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>
        <div className="text-sm text-muted-foreground">
          {sessions.length} session{sessions.length !== 1 ? 's' : ''}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <p className="text-red-800 text-sm">{error}</p>
        </div>
      )}

      {isLoading && sessions.length === 0 ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
        </div>
      ) : sessions.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-12">
            <FolderGit2 className="h-12 w-12 text-muted-foreground mb-4" />
            <h3 className="text-lg font-medium mb-2">No code sessions yet</h3>
            <p className="text-muted-foreground text-sm mb-4">
              Create a new session to start generating code
            </p>
            <Button onClick={() => navigate('/code/new')} className="bg-violet-500 hover:bg-violet-600">
              <Plus className="h-4 w-4 mr-2" /> Create First Session
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {sessions.map((session) => (
            <Card
              key={session.id}
              className="cursor-pointer hover:border-violet-300 transition-colors"
              onClick={() => navigate(`/code/${session.id}`)}
            >
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base font-medium">
                    {session.config.goal.slice(0, 60) || 'Untitled Session'}
                    {session.config.goal.length > 60 ? '...' : ''}
                  </CardTitle>
                  <Badge className={`${statusColors[session.status]} flex items-center gap-1`}>
                    {statusIcons[session.status]}
                    {session.status}
                  </Badge>
                </div>
                <CardDescription className="flex items-center gap-4 text-xs">
                  <span className="flex items-center gap-1">
                    <FolderGit2 className="h-3 w-3" />
                    {session.config.repoPath.split('/').slice(-2).join('/') || 'Unknown'}
                  </span>
                  <span>Created: {new Date(session.createdAt).toLocaleString()}</span>
                  {session.duration && <span>Duration: {session.duration}s</span>}
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-2">
                <div className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-4">
                    <span className="text-muted-foreground">
                      Candidates: <span className="font-medium text-foreground">{session.candidateIds?.length || 0}</span>
                    </span>
                    {session.selectedCandidateId && (
                      <Badge variant="outline" className="text-green-600 border-green-300">
                        <CheckCircle2 className="h-3 w-3 mr-1" /> Selected
                      </Badge>
                    )}
                  </div>
                  {session.summary && (
                    <span className="text-muted-foreground text-xs truncate max-w-md">
                      {session.summary}
                    </span>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppPageLayout>
  )
}
