import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { AppShell } from '@/components/layout/AppShell'
import { PublicLayout } from '@/components/layout/PublicLayout'
import { Homepage } from '@/pages/Homepage'

// Lazy load route components for code splitting
const ResearchPipeline = lazy(() => import('@/pages/Research/Pipeline').then(m => ({ default: m.ResearchPipeline })))
const ResearchWorkflows = lazy(() => import('@/pages/Research/Workflows').then(m => ({ default: m.ResearchWorkflows })))
const RunsList = lazy(() => import('@/pages/Runs/RunsList').then(m => ({ default: m.RunsList })))
const RunDetail = lazy(() => import('@/pages/Runs/RunDetail').then(m => ({ default: m.RunDetail })))
const ExperimentsDashboard = lazy(() => import('@/pages/Experiments/ExperimentsDashboard').then(m => ({ default: m.ExperimentsDashboard })))
const ExperimentDetail = lazy(() => import('@/pages/Experiments/ExperimentDetail').then(m => ({ default: m.ExperimentDetail })))
const PapersList = lazy(() => import('@/pages/Papers/PapersList').then(m => ({ default: m.PapersList })))
const PaperEditor = lazy(() => import('@/pages/Papers/PaperEditor').then(m => ({ default: m.PaperEditor })))
const ConsistencyChecker = lazy(() => import('@/pages/Review/ConsistencyChecker').then(m => ({ default: m.ConsistencyChecker })))
const ReviewerSimulator = lazy(() => import('@/pages/Review/ReviewerSimulator').then(m => ({ default: m.ReviewerSimulator })))
const LLMProviders = lazy(() => import('@/pages/Settings/LLMProviders').then(m => ({ default: m.LLMProviders })))
const Preferences = lazy(() => import('@/pages/Settings/Preferences').then(m => ({ default: m.Preferences })))
const SystemHealth = lazy(() => import('@/pages/System/Health').then(m => ({ default: m.SystemHealth })))
const SystemLogs = lazy(() => import('@/pages/System/Logs').then(m => ({ default: m.SystemLogs })))
const SystemMetrics = lazy(() => import('@/pages/System/Metrics').then(m => ({ default: m.SystemMetrics })))
const CodeDashboard = lazy(() => import('@/pages/Code/CodeDashboard').then(m => ({ default: m.CodeDashboard })))
const CodeSessionPage = lazy(() => import('@/pages/Code/CodeSession').then(m => ({ default: m.CodeSessionPage })))
const NewCodeSession = lazy(() => import('@/pages/Code/NewCodeSession').then(m => ({ default: m.NewCodeSession })))
const CodeSessionDebug = lazy(() => import('@/pages/Code/CodeSessionDebug').then(m => ({ default: m.CodeSessionDebug })))
const CodeProjects = lazy(() => import('@/pages/Code/CodeProjects').then(m => ({ default: m.CodeProjects })))
const CodeProjectBrowser = lazy(() => import('@/pages/Code/CodeProjectBrowser').then(m => ({ default: m.CodeProjectBrowser })))
const CodeProjectWorkspace = lazy(() => import('@/pages/Code/CodeProjectWorkspace').then(m => ({ default: m.CodeProjectWorkspace })))

// Loading fallback component
function PageLoader() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center">
        <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid border-primary border-r-transparent mb-4" />
        <p className="text-sm text-muted-foreground">Loading...</p>
      </div>
    </div>
  )
}

function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* Public routes (no sidebar) */}
            <Route element={<PublicLayout />}>
              <Route path="/" element={<Homepage />} />
            </Route>

            {/* App routes (with sidebar) */}
            <Route element={<AppShell />}>

              {/* Research */}
              <Route path="/research/pipeline" element={<ResearchPipeline />} />
              <Route path="/research/planning" element={<Navigate to="/research/pipeline" replace />} />
              <Route path="/research/workflows" element={<ResearchWorkflows />} />
              <Route path="/research/ideas" element={<Navigate to="/research/pipeline" replace />} />

              {/* Runs */}
              <Route path="/runs" element={<RunsList />} />
              <Route path="/runs/:id" element={<RunDetail />} />

              {/* Experiments */}
              <Route path="/experiments" element={<ExperimentsDashboard />} />
              <Route path="/experiments/:id" element={<ExperimentDetail />} />

              {/* Papers */}
              <Route path="/papers" element={<PapersList />} />
              <Route path="/papers/:id" element={<PaperEditor />} />

              {/* Review */}
              <Route path="/review/consistency" element={<ConsistencyChecker />} />
              <Route path="/review/simulator" element={<ReviewerSimulator />} />

              {/* Settings */}
              <Route path="/settings/providers" element={<LLMProviders />} />
              <Route path="/settings/preferences" element={<Preferences />} />
              <Route path="/settings/llm" element={<Navigate to="/settings/providers" replace />} />
              <Route path="/settings/keys" element={<Navigate to="/settings/providers" replace />} />
              <Route path="/settings/workspace" element={<Navigate to="/settings/providers" replace />} />

              {/* System */}
              <Route path="/system/health" element={<SystemHealth />} />
              <Route path="/system/logs" element={<SystemLogs />} />
              <Route path="/system/metrics" element={<SystemMetrics />} />

              {/* Code Generation */}
              <Route path="/code" element={<CodeProjectWorkspace />} />
              <Route path="/code/projects" element={<CodeProjects />} />
              <Route path="/code/projects/:projectId" element={<CodeProjectBrowser />} />
              <Route path="/code/sessions" element={<CodeDashboard />} />
              <Route path="/code/sessions/new" element={<NewCodeSession />} />
              <Route path="/code/sessions/:sessionId" element={<CodeSessionPage />} />
              <Route path="/code/sessions/:sessionId/debug" element={<CodeSessionDebug />} />

              {/* Catch-all redirect */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  )
}

export default App
