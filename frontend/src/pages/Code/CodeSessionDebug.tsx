/**
 * Code Session Debug Page - LlamaFactory-inspired Run & Debug UI
 * 
 * Layout:
 * - Left: Config panel (command, env, presets)
 * - Middle: Console log viewer + status + controls
 * - Right: Artifacts + Evaluation + Patch viewer
 */

import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Code2, ArrowLeft, Play, Square, Download, Copy,
  Terminal, FileCode, XCircle, Clock, Loader2,
  FolderOpen, AlertTriangle, Zap
} from 'lucide-react'
import { getSession, getCandidates, CodeSession, CodeCandidate } from '@/lib/api/code'
import {
  createJob, startJob, stopJob, getJob, getJobLogs, getJobArtifacts,
  getLogDownloadUrl, Job, Artifact
} from '@/lib/api/jobs'

// Status colors
const statusColors: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800 border-yellow-300',
  running: 'bg-blue-100 text-blue-800 border-blue-300',
  succeeded: 'bg-green-100 text-green-800 border-green-300',
  failed: 'bg-red-100 text-red-800 border-red-300',
  cancelled: 'bg-gray-100 text-gray-800 border-gray-300',
}

// Command presets
const COMMAND_PRESETS = [
  { name: 'Python Syntax Check', command: 'python -m py_compile *.py', description: 'Check Python syntax' },
  { name: 'Pytest', command: 'pytest -v', description: 'Run pytest tests' },
  { name: 'Python Module', command: 'python -m compileall .', description: 'Compile all Python files' },
  { name: 'Node Build', command: 'npm run build', description: 'Build Node.js project' },
  { name: 'Custom', command: '', description: 'Enter custom command' },
]

export function CodeSessionDebug() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()

  // Session state
  const [session, setSession] = useState<CodeSession | null>(null)
  const [candidates, setCandidates] = useState<CodeCandidate[]>([])
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null)
  const [isLoadingSession, setIsLoadingSession] = useState(true)

  // Job state
  const [currentJob, setCurrentJob] = useState<Job | null>(null)
  const [isCreatingJob, setIsCreatingJob] = useState(false)
  const [isStopping, setIsStopping] = useState(false)
  const currentJobId = currentJob?.id
  const currentJobStatus = currentJob?.status

  // Config state
  const [command, setCommand] = useState('python -m py_compile *.py')
  const [envVars, setEnvVars] = useState('')
  const [cwdRel, setCwdRel] = useState('')
  const [timeoutSec, setTimeoutSec] = useState(300)

  // Console state
  const [logLines, setLogLines] = useState<string[]>([])
  const [logType, setLogType] = useState<'stdout' | 'stderr'>('stdout')
  const consoleRef = useRef<HTMLDivElement>(null)

  // Artifacts state
  const [artifacts, setArtifacts] = useState<Artifact[]>([])

  // Error state
  const [error, setError] = useState<string | null>(null)

  // Polling interval ref
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Load session and candidates
  useEffect(() => {
    if (!sessionId) return

    const loadData = async () => {
      try {
        setIsLoadingSession(true)
        const [sessionData, candidatesData] = await Promise.all([
          getSession(sessionId),
          getCandidates(sessionId).catch(() => ({ candidates: [], total: 0 })),
        ])

        setSession(sessionData)
        setCandidates(candidatesData.candidates)

        // Auto-select first candidate
        if (candidatesData.candidates.length > 0) {
          setSelectedCandidateId(candidatesData.candidates[0].id)
        }

        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load session')
      } finally {
        setIsLoadingSession(false)
      }
    }

    loadData()
  }, [sessionId])

  // Poll for job updates when running
  useEffect(() => {
    if (!currentJobId || currentJobStatus !== 'running') {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
      return
    }

    pollingRef.current = setInterval(async () => {
      try {
        const [job, logs] = await Promise.all([
          getJob(currentJobId),
          getJobLogs(currentJobId, logType, 200),
        ])

        setCurrentJob(job)
        setLogLines(logs.lines)

        // Auto-scroll console
        if (consoleRef.current) {
          consoleRef.current.scrollTop = consoleRef.current.scrollHeight
        }

        // Stop polling if job completed
        if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
          if (pollingRef.current) {
            clearInterval(pollingRef.current)
            pollingRef.current = null
          }

          // Load artifacts
          const artifactsData = await getJobArtifacts(job.id)
          setArtifacts(artifactsData.artifacts)
        }
      } catch (err) {
        console.error('Polling error:', err)
      }
    }, 1000)

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
      }
    }
  }, [currentJobId, currentJobStatus, logType])

  // Handle launch
  const handleLaunch = async () => {
    if (!sessionId) return

    setIsCreatingJob(true)
    setError(null)
    setLogLines([])
    setArtifacts([])

    try {
      // Parse env vars
      let parsedEnvVars: Record<string, string> | undefined
      if (envVars.trim()) {
        parsedEnvVars = {}
        for (const line of envVars.split('\n')) {
          const [key, ...valueParts] = line.split('=')
          if (key && valueParts.length > 0) {
            parsedEnvVars[key.trim()] = valueParts.join('=').trim()
          }
        }
      }

      // Create job
      const job = await createJob({
        sessionId,
        candidateId: selectedCandidateId || undefined,
        mode: 'debug',
        command,
        envVars: parsedEnvVars,
        cwdRel: cwdRel || undefined,
        timeoutSec,
      })

      // Start job
      const startedJob = await startJob(job.id)
      setCurrentJob(startedJob)

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to launch job')
    } finally {
      setIsCreatingJob(false)
    }
  }

  // Handle stop
  const handleStop = async () => {
    if (!currentJob) return

    setIsStopping(true)
    try {
      const stoppedJob = await stopJob(currentJob.id)
      setCurrentJob(stoppedJob)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop job')
    } finally {
      setIsStopping(false)
    }
  }

  // Handle rerun
  const handleRerun = async () => {
    setCurrentJob(null)
    await handleLaunch()
  }

  // Copy command
  const copyCommand = () => {
    navigator.clipboard.writeText(command)
  }

  // Get selected candidate
  const selectedCandidate = candidates.find(c => c.id === selectedCandidateId)

  if (isLoadingSession) {
    return (
      <AppPageLayout title="Loading..." icon={Code2} iconColor="violet" accentColor="violet">
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
        </div>
      </AppPageLayout>
    )
  }

  if (!session) {
    return (
      <AppPageLayout title="Session Not Found" icon={Code2} iconColor="violet" accentColor="violet">
        <Card>
          <CardContent className="py-8 text-center">
            <XCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
            <p className="text-muted-foreground">Session not found: {sessionId}</p>
            <Button onClick={() => navigate('/code')} className="mt-4">
              <ArrowLeft className="h-4 w-4 mr-2" /> Back to Sessions
            </Button>
          </CardContent>
        </Card>
      </AppPageLayout>
    )
  }

  return (
    <AppPageLayout
      title="Run & Debug"
      subtitle={`Session: ${session.id}`}
      icon={Terminal}
      iconColor="violet"
      accentColor="violet"
    >
      {/* Header with back button and status */}
      <div className="flex items-center justify-between mb-6">
        <Button variant="ghost" onClick={() => navigate(`/code/sessions/${sessionId}`)}>
          <ArrowLeft className="h-4 w-4 mr-2" /> Back to Session
        </Button>

        {currentJob && (
          <Badge className={`${statusColors[currentJob.status]} border`}>
            {currentJob.status === 'running' && <Loader2 className="h-3 w-3 mr-1 animate-spin" />}
            {currentJob.status.toUpperCase()}
          </Badge>
        )}
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-6 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-red-600" />
          <span className="text-sm text-red-900">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError(null)} className="ml-auto">
            Dismiss
          </Button>
        </div>
      )}

      {/* Main 3-column layout */}
      <div className="grid grid-cols-12 gap-4">
        {/* Left: Config Panel */}
        <div className="col-span-3">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Zap className="h-4 w-4" /> Configuration
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Candidate selector */}
              {candidates.length > 0 && (
                <div>
                  <span className="text-xs font-medium">Candidate</span>
                  <select
                    className="w-full mt-1 p-2 border rounded-md text-sm"
                    value={selectedCandidateId || ''}
                    onChange={(e) => setSelectedCandidateId(e.target.value || null)}
                  >
                    <option value="">No candidate (run in base repo)</option>
                    {candidates.map((c) => (
                      <option key={c.id} value={c.id}>
                        #{c.rank} - {c.title}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {/* Command presets */}
              <div>
                <span className="text-xs font-medium">Preset</span>
                <select
                  className="w-full mt-1 p-2 border rounded-md text-sm"
                  onChange={(e) => {
                    const preset = COMMAND_PRESETS.find(p => p.name === e.target.value)
                    if (preset && preset.command) {
                      setCommand(preset.command)
                    }
                  }}
                >
                  {COMMAND_PRESETS.map((p) => (
                    <option key={p.name} value={p.name}>{p.name}</option>
                  ))}
                </select>
              </div>

              {/* Command input */}
              <div>
                <span className="text-xs font-medium">Command</span>
                <div className="flex gap-1 mt-1">
                  <Input
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    placeholder="python -m pytest"
                    className="text-sm font-mono"
                  />
                  <Button variant="ghost" size="sm" onClick={copyCommand}>
                    <Copy className="h-3 w-3" />
                  </Button>
                </div>
              </div>

              {/* Working directory */}
              <div>
                <span className="text-xs font-medium">Working Directory (relative)</span>
                <Input
                  value={cwdRel}
                  onChange={(e) => setCwdRel(e.target.value)}
                  placeholder="e.g., src/"
                  className="text-sm mt-1"
                />
              </div>

              {/* Timeout */}
              <div>
                <span className="text-xs font-medium">Timeout (seconds)</span>
                <Input
                  type="number"
                  value={timeoutSec}
                  onChange={(e) => {
                    const parsed = parseInt(e.target.value, 10)
                    setTimeoutSec(Number.isNaN(parsed) || parsed < 1 ? 300 : parsed)
                  }}
                  min={10}
                  max={3600}
                  className="text-sm mt-1"
                />
              </div>

              {/* Environment variables */}
              <div>
                <span className="text-xs font-medium">Environment Variables</span>
                <textarea
                  value={envVars}
                  onChange={(e) => setEnvVars(e.target.value)}
                  placeholder="KEY=value"
                  className="w-full text-sm font-mono mt-1 h-20 p-2 border rounded-md"
                />
              </div>

              {/* Workspace path display */}
              {currentJob?.workspacePath && (
                <div>
                  <span className="text-xs font-medium">Workspace Path</span>
                  <div className="flex items-center gap-1 mt-1 p-2 bg-gray-50 rounded text-xs font-mono truncate">
                    <FolderOpen className="h-3 w-3 flex-shrink-0" />
                    <span className="truncate">{currentJob.workspacePath}</span>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Middle: Console + Controls */}
        <div className="col-span-6">
          <Card className="h-full flex flex-col">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm flex items-center gap-2">
                  <Terminal className="h-4 w-4" /> Console
                </CardTitle>

                {/* Run controls */}
                <div className="flex items-center gap-2">
                  {(!currentJob || ['succeeded', 'failed', 'cancelled'].includes(currentJob.status)) ? (
                    <Button
                      onClick={currentJob ? handleRerun : handleLaunch}
                      disabled={isCreatingJob || !command.trim()}
                      size="sm"
                      className="bg-green-500 hover:bg-green-600"
                    >
                      {isCreatingJob ? (
                        <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                      ) : (
                        <Play className="h-4 w-4 mr-1" />
                      )}
                      {currentJob ? 'Rerun' : 'Launch'}
                    </Button>
                  ) : (
                    <Button
                      onClick={handleStop}
                      disabled={isStopping}
                      size="sm"
                      variant="destructive"
                    >
                      {isStopping ? (
                        <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                      ) : (
                        <Square className="h-4 w-4 mr-1" />
                      )}
                      Stop
                    </Button>
                  )}

                  {currentJob && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => window.open(getLogDownloadUrl(currentJob.id, logType), '_blank')}
                    >
                      <Download className="h-4 w-4 mr-1" /> Logs
                    </Button>
                  )}
                </div>
              </div>

              {/* Log type tabs */}
              <div className="flex gap-2 mt-2">
                <Button
                  variant={logType === 'stdout' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setLogType('stdout')}
                >
                  stdout
                </Button>
                <Button
                  variant={logType === 'stderr' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setLogType('stderr')}
                >
                  stderr
                </Button>
              </div>
            </CardHeader>

            <CardContent className="flex-1 p-0">
              {/* Console output */}
              <div
                ref={consoleRef}
                className="h-96 bg-gray-900 text-gray-100 font-mono text-xs p-4 overflow-auto"
              >
                {logLines.length === 0 ? (
                  <div className="text-gray-500 italic">
                    {currentJob?.status === 'running'
                      ? 'Waiting for output...'
                      : 'Click Launch to start execution'}
                  </div>
                ) : (
                  logLines.map((line, i) => (
                    <div key={i} className="whitespace-pre-wrap break-all">
                      {line}
                    </div>
                  ))
                )}
              </div>

              {/* Status bar */}
              {currentJob && (
                <div className="p-2 bg-gray-100 border-t flex items-center justify-between text-xs">
                  <div className="flex items-center gap-4">
                    <span>Job: <code>{currentJob.id}</code></span>
                    {currentJob.pid && <span>PID: {currentJob.pid}</span>}
                    {currentJob.exitCode !== null && (
                      <span className={currentJob.exitCode === 0 ? 'text-green-600' : 'text-red-600'}>
                        Exit: {currentJob.exitCode}
                      </span>
                    )}
                  </div>
                  {currentJob.durationSec !== null && (
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" /> {currentJob.durationSec}s
                    </span>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right: Artifacts + Patch */}
        <div className="col-span-3 space-y-4">
          {/* Patch Card */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileCode className="h-4 w-4" /> Patch Diff
              </CardTitle>
            </CardHeader>
            <CardContent>
              {selectedCandidate?.patch ? (
                <pre className="bg-gray-900 text-gray-100 p-3 rounded text-xs overflow-auto max-h-60 font-mono">
                  {selectedCandidate.patch}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No patch available
                </p>
              )}
            </CardContent>
          </Card>

          {/* Artifacts Card */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FolderOpen className="h-4 w-4" /> Artifacts
              </CardTitle>
            </CardHeader>
            <CardContent>
              {artifacts.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No artifacts yet
                </p>
              ) : (
                <div className="space-y-2">
                  {artifacts.map((artifact) => (
                    <div
                      key={artifact.id}
                      className="flex items-center justify-between p-2 bg-gray-50 rounded text-sm"
                    >
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs">
                          {artifact.kind}
                        </Badge>
                        <span className="font-mono text-xs">{artifact.filename}</span>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {(artifact.sizeBytes / 1024).toFixed(1)} KB
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </AppPageLayout>
  )
}
