import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { FiltersBar } from '@/components/layout/FiltersBar'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Play, FileText, Search } from 'lucide-react'
import { allTemplates } from '@/lib/taxonomy/templates'
import { CategoryGroup, categoryGroups, getDirectionById, getDirectionsByGroup } from '@/lib/taxonomy/categories'
import { useCreateRun } from '@/lib/hooks/useApi'
import type { ResearchTemplate } from '@/lib/taxonomy/templates'
import { useReviewLocale } from '@/lib/reviewLocale'

export function ResearchWorkflows() {
  const navigate = useNavigate()
  const { text } = useReviewLocale()
  const createRunMutation = useCreateRun()

  const [groupFilter, setGroupFilter] = useState<CategoryGroup | 'all'>('all')
  const [directionFilter, setDirectionFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const filteredTemplates = useMemo(() => {
    return allTemplates.filter(template => {
      // Get direction to check group
      const direction = getDirectionById(template.directionId)
      if (!direction) return false

      if (groupFilter !== 'all' && direction.group !== groupFilter) return false
      if (directionFilter !== 'all' && template.directionId !== directionFilter) return false
      if (searchQuery) {
        const query = searchQuery.toLowerCase()
        return (
          template.name.toLowerCase().includes(query) ||
          template.description.toLowerCase().includes(query)
        )
      }
      return true
    })
  }, [groupFilter, directionFilter, searchQuery])

  const handleApplyToPlanning = (template: ResearchTemplate) => {
    navigate(`/research/planning?direction=${template.directionId}&template=${template.id}`)
  }

  const handleStartRun = async (template: ResearchTemplate) => {
    const direction = getDirectionById(template.directionId)
    if (!direction) return
    try {
      const run = await createRunMutation.mutateAsync({
        instancePath: '/workspace',
        taskLevel: 'task1',
        paperType: 'algorithm',
        model: template.config.model,
        workplaceName: 'default',
        cachePath: '/cache',
        port: 8000,
        maxIterTimes: 10,
        categoryGroup: direction.group,
        categoryDirectionId: template.directionId,
        templateId: template.id,
      })
      navigate(`/runs/${run.id}`)
    } catch (error) {
      console.error('Failed to create run:', error)
    }
  }

  const directions = useMemo(() => {
    if (groupFilter === 'all') return []
    return getDirectionsByGroup(groupFilter)
  }, [groupFilter])

  return (
    <AppPageLayout
      title={text('科研工作流', 'Workflows')}
      subtitle={text('选择、配置并运行科研工作流模板', 'Manage and monitor research workflow templates')}
      headerViz="miniBars"
    >
      <FiltersBar
        searchSlot={
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <Input
              placeholder={text('搜索模板...', 'Search templates...')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 border-slate-300 focus:ring-2 focus:ring-teal-500"
            />
          </div>
        }
        filterSlots={
          <>
            <select
              value={groupFilter}
              onChange={(e) => {
                setGroupFilter(e.target.value as CategoryGroup | 'all')
                setDirectionFilter('all')
              }}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
            >
              <option value="all">{text('全部分组', 'All Groups')}</option>
              {Object.entries(categoryGroups).map(([id, group]) => (
                <option key={id} value={id}>
                  {group.label}
                </option>
              ))}
            </select>

            <select
              value={directionFilter}
              onChange={(e) => setDirectionFilter(e.target.value)}
              disabled={groupFilter === 'all'}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 disabled:opacity-50"
            >
              <option value="all">{text('全部方向', 'All Directions')}</option>
              {directions.map(dir => (
                <option key={dir.id} value={dir.id}>
                  {dir.title}
                </option>
              ))}
            </select>
          </>
        }
      />

      {/* Template Gallery */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {filteredTemplates.length === 0 ? (
          <Card className="col-span-full">
            <CardContent className="py-12 text-center">
              <p className="text-muted-foreground">{text('没有符合筛选条件的模板', 'No templates found matching your filters')}</p>
            </CardContent>
          </Card>
        ) : (
          filteredTemplates.map(template => (
            <Card key={template.id} className="flex flex-col">
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1">
                    <CardTitle className="text-lg">{template.name}</CardTitle>
                    <CardDescription className="mt-1">
                      {template.description}
                    </CardDescription>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1 mt-3">
                  {getDirectionById(template.directionId)?.tags.slice(0, 3).map(tag => (
                    <Badge key={tag} variant="secondary" className="text-xs">
                      {tag}
                    </Badge>
                  ))}
                  {(getDirectionById(template.directionId)?.tags.length || 0) > 3 && (
                    <Badge variant="outline" className="text-xs">
                      +{(getDirectionById(template.directionId)?.tags.length || 0) - 3}
                    </Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent className="flex-1 space-y-4">
                {/* Template Details */}
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{text('步骤', 'Steps')}:</span>
                    <span className="font-medium">{template.workflowSteps?.length || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Artifact:</span>
                    <span className="font-medium">{template.expectedArtifacts.length}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{text('预计时长', 'Est. Duration')}:</span>
                    <span className="font-medium">{template.estimatedDuration}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{text('预算', 'Budget')}:</span>
                    <span className="font-medium">${template.config.budget || 'N/A'}</span>
                  </div>
                </div>

                {/* Steps Preview */}
                {template.workflowSteps && template.workflowSteps.length > 0 && (
                  <div className="space-y-1">
                    <div className="text-xs font-medium text-muted-foreground">{text('步骤', 'Steps')}:</div>
                    <div className="space-y-1">
                      {template.workflowSteps.slice(0, 3).map((step, idx) => (
                        <div key={idx} className="text-xs text-muted-foreground flex items-start gap-2">
                          <span className="text-primary font-mono">{idx + 1}.</span>
                          <span className="flex-1">{step}</span>
                        </div>
                      ))}
                      {template.workflowSteps.length > 3 && (
                        <div className="text-xs text-muted-foreground">+ {template.workflowSteps.length - 3} {text('个步骤', 'more steps')}</div>
                      )}
                    </div>
                  </div>
                )}

                {/* Actions */}
                <div className="flex gap-2 pt-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={() => handleApplyToPlanning(template)}
                  >
                    <FileText className="h-4 w-4 mr-2" />
                    {text('用于计划生成', 'Apply to Planning')}
                  </Button>
                  <Button
                    size="sm"
                    className="flex-1"
                    onClick={() => handleStartRun(template)}
                    disabled={createRunMutation.isPending}
                  >
                    <Play className="h-4 w-4 mr-2" />
                    {text('开始运行', 'Start Run')}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      {/* Summary */}
      <Card>
        <CardContent className="py-4">
          <p className="text-sm text-slate-600 text-center">
            {text(`显示 ${filteredTemplates.length} / ${allTemplates.length} 个模板`, `Showing ${filteredTemplates.length} of ${allTemplates.length} templates`)}
          </p>
        </CardContent>
      </Card>
    </AppPageLayout>
  )
}
