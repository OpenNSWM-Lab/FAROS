import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { AppShell } from '@/components/layout/AppShell'
import { PublicLayout } from '@/components/layout/PublicLayout'
import { Homepage } from '@/pages/Homepage'
import { useReviewLocale } from '@/lib/reviewLocale'

// Lazy load route components for code splitting
const ResearchPipeline = lazy(() => import('@/pages/Research/Pipeline').then(m => ({ default: m.ResearchPipeline })))
const RunsList = lazy(() => import('@/pages/Runs/RunsList').then(m => ({ default: m.RunsList })))
const RunDetail = lazy(() => import('@/pages/Runs/RunDetail').then(m => ({ default: m.RunDetail })))
const ExperimentsDashboard = lazy(() => import('@/pages/Experiments/ExperimentsDashboard').then(m => ({ default: m.ExperimentsDashboard })))
const PapersList = lazy(() => import('@/pages/Papers/PapersList').then(m => ({ default: m.PapersList })))
const PaperWritingWorkspace = lazy(() => import('@/pages/Papers/PaperWritingWorkspace').then(m => ({ default: m.PaperWritingWorkspace })))
const ConsistencyChecker = lazy(() => import('@/pages/Review/ConsistencyChecker').then(m => ({ default: m.ConsistencyChecker })))
const CompetitionEvidence = lazy(() => import('@/pages/Review/CompetitionEvidence').then(m => ({ default: m.CompetitionEvidence })))
const LLMProviders = lazy(() => import('@/pages/Settings/LLMProviders').then(m => ({ default: m.LLMProviders })))
const SystemHealth = lazy(() => import('@/pages/System/Health').then(m => ({ default: m.SystemHealth })))
const CodeBlueprint = lazy(() => import('@/pages/Code/CodeBlueprint').then(m => ({ default: m.CodeBlueprint })))
const CodeStepDetail = lazy(() => import('@/pages/Code/CodeStepDetail').then(m => ({ default: m.CodeStepDetail })))
const CodeProjects = lazy(() => import('@/pages/Code/CodeProjects').then(m => ({ default: m.CodeProjects })))
const CodeProjectBrowser = lazy(() => import('@/pages/Code/CodeProjectBrowser').then(m => ({ default: m.CodeProjectBrowser })))
const CodeProjectWorkspace = lazy(() => import('@/pages/Code/CodeProjectWorkspace').then(m => ({ default: m.CodeProjectWorkspace })))

// Loading fallback component
function PageLoader() {
  const { text } = useReviewLocale()
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center">
        <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid border-primary border-r-transparent mb-4" />
        <p className="text-sm text-muted-foreground">{text('正在加载...', 'Loading...')}</p>
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
              <Route path="/research/workflows" element={<Navigate to="/research/pipeline" replace />} />
              <Route path="/research/ideas" element={<Navigate to="/research/pipeline" replace />} />

              {/* Runs */}
              <Route path="/runs" element={<RunsList />} />
              <Route path="/runs/:id" element={<RunDetail />} />

              {/* Experiments */}
              <Route path="/experiments" element={<ExperimentsDashboard />} />
              <Route path="/experiments/:id" element={<ExperimentsDashboard />} />

              {/* Papers */}
              <Route path="/papers" element={<PapersList />} />
              <Route path="/papers/legacy/:id" element={<Navigate to="/papers" replace />} />
              <Route path="/papers/:id" element={<Navigate to="start" replace />} />
              <Route path="/papers/:id/:stage" element={<PaperWritingWorkspace />} />

              {/* Review */}
              <Route path="/review/competition" element={<CompetitionEvidence />} />
              <Route path="/review/consistency" element={<ConsistencyChecker />} />
              <Route path="/review/simulator" element={<Navigate to="/review/consistency" replace />} />

              {/* Settings */}
              <Route path="/settings/providers" element={<LLMProviders />} />
              <Route path="/settings/preferences" element={<Navigate to="/settings/providers" replace />} />
              <Route path="/settings/llm" element={<Navigate to="/settings/providers" replace />} />
              <Route path="/settings/keys" element={<Navigate to="/settings/providers" replace />} />
              <Route path="/settings/workspace" element={<Navigate to="/settings/providers" replace />} />

              {/* System */}
              <Route path="/system/health" element={<SystemHealth />} />
              <Route path="/system/logs" element={<Navigate to="/system/health" replace />} />
              <Route path="/system/metrics" element={<Navigate to="/system/health" replace />} />

              {/* Code Generation & Projects */}
              <Route path="/code" element={<Navigate to="/code/projects" replace />} />
              <Route path="/code/workspace" element={<CodeProjectWorkspace />} />
              <Route path="/code/blueprint" element={<CodeBlueprint />} />
              <Route path="/code/blueprint/step/:stepId" element={<CodeStepDetail />} />
              <Route path="/code/projects" element={<CodeProjects />} />
              <Route path="/code/projects/:projectId" element={<CodeProjectBrowser />} />
              <Route path="/code/new" element={<Navigate to="/code/workspace" replace />} />
              <Route path="/code/sessions" element={<Navigate to="/code/projects" replace />} />
              <Route path="/code/sessions/new" element={<Navigate to="/code/workspace" replace />} />
              <Route path="/code/sessions/:sessionId" element={<Navigate to="/runs" replace />} />
              <Route path="/code/sessions/:sessionId/debug" element={<Navigate to="/runs" replace />} />

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
