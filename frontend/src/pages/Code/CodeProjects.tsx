/**
 * Code Projects List Page
 * 
 * Lists generated or imported code projects.
 */

import { useState, useEffect, useRef } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  Code2, Search, FolderOpen, Loader2,
  AlertTriangle, FileCode, Clock, FlaskConical, GitBranch, Trash2, ArchiveRestore
} from 'lucide-react'
import {
  listProjects, deleteProject, uploadFinishedBundle,
  CodeProjectV2,
} from '@/lib/api/codeProjects'
import { useReviewLocale } from '@/lib/reviewLocale'

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
  const { text } = useReviewLocale()
  const [projects, setProjects] = useState<CodeProjectV2[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const bundleInputRef = useRef<HTMLInputElement>(null)

  const loadProjects = async (searchTerm?: string) => {
    try {
      setLoading(true)
      setError(null)
      const resp = await listProjects({ search: searchTerm || undefined })
      setProjects(resp.projects)
    } catch (err) {
      setError(err instanceof Error ? err.message : text('项目加载失败', 'Failed to load projects'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadProjects() }, [])

  const handleSearch = () => { loadProjects(search) }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch()
  }

  const handleImportBundle = async (file: File) => {
    try {
      setImporting(true)
      setError(null)
      const imported = await uploadFinishedBundle(file)
      navigate(`/code/projects/${imported.projectId}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : text('导入交付包失败', 'Failed to import bundle'))
    } finally {
      setImporting(false)
    }
  }

  const handleBundleSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (file) void handleImportBundle(file)
  }

  const handleDeleteProject = async (e: React.MouseEvent, projectId: string, title: string) => {
    e.stopPropagation()  // prevent card click
    if (!confirm(text(
      `删除项目“${title}”？这会删除项目文件与流程历史。`,
      `Delete project "${title}"? This removes project files and pipeline history.`,
    ))) return
    try {
      await deleteProject(projectId)
      setProjects(prev => prev.filter(p => p.id !== projectId))
    } catch (err) {
      setError(err instanceof Error ? err.message : text('删除失败', 'Delete failed'))
    }
  }

  return (
    <AppPageLayout
      title={text('Code 项目', 'Code Projects')}
      subtitle={text('管理从科研计划生成或导入的实验工程', 'Manage experiment projects generated from plans or imported bundles')}
      icon={Code2}
      iconColor="violet"
      accentColor="violet"
    >
      {/* Code sub-navigation tabs */}
      <div className="flex items-center gap-1 mb-6 border-b pb-2">
        {[
          { label: text('项目', 'Projects'), href: '/code/projects', icon: FolderOpen },
          { label: text('生成工作区', 'Workspace'), href: '/code/workspace', icon: FlaskConical },
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
      <div className="mb-6 flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="flex min-w-0 flex-1 gap-2">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={text('按标题搜索项目...', 'Search projects by title...')}
            className="max-w-md"
          />
          <Button variant="outline" onClick={handleSearch}>
            <Search className="h-4 w-4 mr-1" /> {text('搜索', 'Search')}
          </Button>
        </div>
        <Button
          onClick={() => navigate('/code/workspace')}
          variant="outline"
          title={text('从科研计划生成代码', 'Generate code from a research plan')}
        >
          <FlaskConical className="h-4 w-4 mr-1" /> {text('从计划生成', 'Generate from Plan')}
        </Button>
        <input
          ref={bundleInputRef}
          type="file"
          accept=".zip,application/zip"
          className="hidden"
          onChange={handleBundleSelected}
        />
        <Button onClick={() => bundleInputRef.current?.click()} disabled={importing} variant="outline">
          {importing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <ArchiveRestore className="h-4 w-4 mr-1" />}
          {text('导入交付包', 'Import Bundle')}
        </Button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-red-600" />
          <span className="text-sm text-red-900">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError(null)} className="ml-auto">
            {text('关闭', 'Dismiss')}
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
            <p className="text-lg font-medium mb-2">{text('暂无 Code 项目', 'No code projects yet')}</p>
            <p className="text-sm text-muted-foreground mb-4">
              {text('批准 PlanPackage 后生成实验工程，或导入已有交付包。', 'Generate an experiment project from an approved PlanPackage, or import an existing bundle.')}
            </p>
            <div className="flex gap-3 justify-center">
              <Button onClick={() => navigate('/code/workspace')}>
                <FlaskConical className="h-4 w-4 mr-2" /> {text('从计划生成', 'Generate from Plan')}
              </Button>
              <Button variant="outline" onClick={() => bundleInputRef.current?.click()} disabled={importing}>
                <ArchiveRestore className="h-4 w-4 mr-2" /> {text('导入交付包', 'Import Bundle')}
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
                    {project.fileCount} {text('个文件', 'files')}
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
                    {text('来自创意会话', 'From Idea Session')}
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
