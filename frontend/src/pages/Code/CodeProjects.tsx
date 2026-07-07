/**
 * Code Projects List Page
 * 
 * Lists all code projects with search, create, and generate sample buttons.
 */

import { useState, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  Code2, Search, FolderOpen, Loader2,
  AlertTriangle, FileCode, Clock, Sparkles, FlaskConical, GitBranch, Trash2
} from 'lucide-react'
import {
  listProjects, generateSampleProject, deleteProject,
  CodeProjectV2,
} from '@/lib/api/codeProjects'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

export function CodeProjects() {
  const navigate = useNavigate()
  const location = useLocation()
  const [projects, setProjects] = useState<CodeProjectV2[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)

  const loadProjects = async (searchTerm?: string) => {
    try {
      setLoading(true)
      setError(null)
      const resp = await listProjects({ search: searchTerm || undefined })
      setProjects(resp.projects)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load projects')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadProjects() }, [])

  const handleSearch = () => { loadProjects(search) }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch()
  }

  const handleGenerateSample = async () => {
    try {
      setGenerating(true)
      setError(null)
      const project = await generateSampleProject(
        'Sample FastAPI Project',
        'python',
        'A sample project demonstrating multi-file code generation'
      )
      navigate(`/code/projects/${project.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate sample')
    } finally {
      setGenerating(false)
    }
  }

  const handleDeleteProject = async (e: React.MouseEvent, projectId: string, title: string) => {
    e.stopPropagation()  // prevent card click
    if (!confirm(`Delete project "${title}"?\nThis will remove all files and pipeline history.`)) return
    try {
      await deleteProject(projectId)
      setProjects(prev => prev.filter(p => p.id !== projectId))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  return (
    <AppPageLayout
      title="Code Projects"
      subtitle="GitHub-like browsing for generated code"
      icon={Code2}
      iconColor="violet"
      accentColor="violet"
    >
      {/* Code sub-navigation tabs */}
      <div className="flex items-center gap-1 mb-6 border-b pb-2">
        {[
          { label: 'Projects', href: '/code/projects', icon: FolderOpen },
          { label: 'Workspace', href: '/code/workspace', icon: FlaskConical },
          { label: 'Blueprint', href: '/code/blueprint', icon: GitBranch },
        ].map((tab) => (
          <Button
            key={tab.href}
            variant={location.pathname === tab.href ? 'default' : 'ghost'}
            size="sm"
            onClick={() => navigate(tab.href)}
            className={cn(
              'text-sm',
              location.pathname === tab.href
                ? 'bg-violet-600 text-white hover:bg-violet-700'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <tab.icon className="h-4 w-4 mr-1.5" />
            {tab.label}
          </Button>
        ))}
      </div>

      {/* Top bar: search + actions */}
      <div className="flex items-center gap-3 mb-6">
        <div className="flex-1 flex gap-2">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search projects by title..."
            className="max-w-md"
          />
          <Button variant="outline" onClick={handleSearch}>
            <Search className="h-4 w-4 mr-1" /> Search
          </Button>
        </div>
        <Button
          onClick={() => navigate('/code/workspace')}
          variant="outline"
          title="Generate code from a research plan"
        >
          <FlaskConical className="h-4 w-4 mr-1" /> Generate from Plan
        </Button>
        <Button
          onClick={handleGenerateSample}
          disabled={generating}
          variant="outline"
        >
          {generating ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Sparkles className="h-4 w-4 mr-1" />}
          Generate Sample
        </Button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-red-600" />
          <span className="text-sm text-red-900">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError(null)} className="ml-auto">
            Dismiss
          </Button>
        </div>
      )}

      {/* Loading state */}
      {loading ? (
        <div className="flex items-center justify-center h-48">
          <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
        </div>
      ) : projects.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <FolderOpen className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
            <p className="text-lg font-medium mb-2">No projects yet</p>
            <p className="text-sm text-muted-foreground mb-4">
              Create a sample project to explore, or generate code from a research plan.
            </p>
            <div className="flex gap-3 justify-center">
              <Button onClick={handleGenerateSample} disabled={generating}>
                <Sparkles className="h-4 w-4 mr-2" /> Sample Project
              </Button>
              <Button variant="outline" onClick={() => navigate('/code/workspace')}>
                <FlaskConical className="h-4 w-4 mr-2" /> Generate from Plan
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => (
            <Card
              key={project.id}
              className="cursor-pointer hover:shadow-md transition-shadow border-l-4 border-l-violet-400 relative group"
              onClick={() => navigate(`/code/projects/${project.id}`)}
            >
              <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-all">
                <button
                  className="p-1 rounded hover:bg-violet-50 text-muted-foreground hover:text-violet-600"
                  onClick={(e) => { e.stopPropagation(); navigate(`/code/blueprint?projectId=${project.id}`) }}
                  title="View Blueprint"
                >
                  <GitBranch className="h-4 w-4" />
                </button>
                <button
                  className="p-1 rounded hover:bg-red-50 text-muted-foreground hover:text-red-600"
                  onClick={(e) => handleDeleteProject(e, project.id, project.title)}
                  title="Delete project"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <FileCode className="h-4 w-4 text-violet-500" />
                  {project.title}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {project.description && (
                  <p className="text-sm text-muted-foreground mb-3 line-clamp-2">
                    {project.description}
                  </p>
                )}
                <div className="flex items-center gap-2 flex-wrap">
                  {project.language && (
                    <Badge variant="secondary" className="text-xs">
                      {project.language}
                    </Badge>
                  )}
                  {project.framework && (
                    <Badge variant="outline" className="text-xs">
                      {project.framework}
                    </Badge>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {project.fileCount} files
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {formatBytes(project.totalSizeBytes)}
                  </span>
                </div>
                <div className="flex items-center gap-1 mt-2 text-xs text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  {formatDate(project.createdAt)}
                </div>
                {project.sourceIdeaSessionId && (
                  <Badge variant="outline" className="mt-2 text-xs">
                    From Idea Session
                  </Badge>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppPageLayout>
  )
}
