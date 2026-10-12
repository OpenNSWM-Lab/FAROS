import { useState, useMemo } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ChevronDown, ChevronRight, Search, Star, Sparkles, BookOpen, Clock, Zap } from 'lucide-react'
import {
  Direction,
  CategoryGroup,
  allDirections,
  categoryGroups,
  searchDirections
} from '@/lib/taxonomy/categories'
import { getTemplatesByDirection } from '@/lib/taxonomy/templates'

interface CategoryPickerProps {
  selectedDirection: Direction | null
  onSelectDirection: (direction: Direction) => void
  onApplyTemplate?: (templateId: string) => void
  onStartRun?: () => void
}

export function CategoryPicker({
  selectedDirection,
  onSelectDirection,
  onApplyTemplate,
  onStartRun
}: CategoryPickerProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [groupFilter, setGroupFilter] = useState<CategoryGroup | 'all'>('all')
  const [showTemplatesOnly, setShowTemplatesOnly] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState<Set<CategoryGroup>>(
    new Set(['post-training', 'inference'])
  )
  const [favorites, setFavorites] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem('favoriteDirections')
      const parsed = stored ? JSON.parse(stored) : []
      return new Set(Array.isArray(parsed) ? parsed : [])
    } catch {
      return new Set()
    }
  })

  const filteredDirections = useMemo(() => {
    let results = searchQuery ? searchDirections(searchQuery) : allDirections

    if (groupFilter !== 'all') {
      results = results.filter(d => d.group === groupFilter)
    }

    if (showTemplatesOnly) {
      results = results.filter(d => d.recommendedTemplates.length > 0)
    }

    return results
  }, [searchQuery, groupFilter, showTemplatesOnly])

  const toggleGroup = (group: CategoryGroup) => {
    const newExpanded = new Set(expandedGroups)
    if (newExpanded.has(group)) {
      newExpanded.delete(group)
    } else {
      newExpanded.add(group)
    }
    setExpandedGroups(newExpanded)
  }

  const toggleFavorite = (directionId: string) => {
    const newFavorites = new Set(favorites)
    if (newFavorites.has(directionId)) {
      newFavorites.delete(directionId)
    } else {
      newFavorites.add(directionId)
    }
    setFavorites(newFavorites)
    localStorage.setItem('favoriteDirections', JSON.stringify(Array.from(newFavorites)))
  }

  const handleKeyDown = (e: React.KeyboardEvent, direction: Direction) => {
    if (e.key === 'Enter') {
      onSelectDirection(direction)
    }
  }

  const groupedDirections = useMemo(() => {
    const groups: Record<CategoryGroup, Direction[]> = {
      'post-training': [],
      'inference': []
    }

    filteredDirections.forEach(d => {
      groups[d.group].push(d)
    })

    return groups
  }, [filteredDirections])

  return (
    <div className="grid grid-cols-12 gap-4 h-[600px]">
      {/* Left Panel: Search + Filters */}
      <Card className="col-span-3 overflow-hidden flex flex-col">
        <CardContent className="p-4 space-y-4 flex-1 overflow-y-auto">
          <div>
            <h3 className="font-semibold mb-2">Search & Filters</h3>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search directions..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
          </div>

          <div>
            <label className="text-sm font-medium mb-2 block">Group</label>
            <div className="space-y-1">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="group"
                  checked={groupFilter === 'all'}
                  onChange={() => setGroupFilter('all')}
                />
                <span>All Groups</span>
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="group"
                  checked={groupFilter === 'post-training'}
                  onChange={() => setGroupFilter('post-training')}
                />
                <span>Post-Training</span>
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="group"
                  checked={groupFilter === 'inference'}
                  onChange={() => setGroupFilter('inference')}
                />
                <span>Inference-Time</span>
              </label>
            </div>
          </div>

          <div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={showTemplatesOnly}
                onChange={(e) => setShowTemplatesOnly(e.target.checked)}
              />
              <span>Show only with templates</span>
            </label>
          </div>

          <div className="pt-2 border-t">
            <p className="text-sm text-muted-foreground">
              <span className="font-medium">{filteredDirections.length}</span> matched directions
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Middle Panel: Tree List */}
      <Card className="col-span-5 overflow-hidden flex flex-col">
        <CardContent className="p-4 flex-1 overflow-y-auto">
          <h3 className="font-semibold mb-3">Research Directions</h3>

          {(Object.keys(groupedDirections) as CategoryGroup[]).map(group => {
            const directions = groupedDirections[group]
            if (directions.length === 0) return null

            const isExpanded = expandedGroups.has(group)
            const groupInfo = categoryGroups[group]

            return (
              <div key={group} className="mb-4">
                <button
                  onClick={() => toggleGroup(group)}
                  className="flex items-center gap-2 w-full text-left font-medium mb-2 hover:text-primary transition-colors"
                >
                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                  <span>{groupInfo.label}</span>
                  <Badge variant="outline" className="ml-auto text-xs">
                    {directions.length}
                  </Badge>
                </button>

                {isExpanded && (
                  <div className="ml-6 space-y-1">
                    <p className="text-xs text-muted-foreground mb-2">
                      {groupInfo.subgroupLabel}
                    </p>

                    {directions.map(direction => {
                      const isSelected = selectedDirection?.id === direction.id
                      const isFavorite = favorites.has(direction.id)
                      const accentColor = direction.group === 'post-training' ? 'teal' : direction.group === 'inference' ? 'cyan' : 'indigo'

                      return (
                        <div
                          key={direction.id}
                          role="button"
                          tabIndex={0}
                          onClick={() => onSelectDirection(direction)}
                          onKeyDown={(e) => handleKeyDown(e, direction)}
                          data-testid="direction-card"
                          className={`
                            relative p-4 rounded-lg border bg-white shadow-sm cursor-pointer transition-all duration-200
                            ${isSelected
                              ? `ring-2 ring-${accentColor}-500 border-${accentColor}-500 bg-${accentColor}-50/50 shadow-md`
                              : `border-slate-200 hover:border-${accentColor}-300 hover:shadow-md hover:bg-gradient-to-br hover:from-white hover:to-${accentColor}-50/20`
                            }
                          `}
                          style={{
                            borderLeftWidth: isSelected ? '4px' : '3px',
                            borderLeftColor: isSelected
                              ? accentColor === 'teal' ? '#0EA5A4' : accentColor === 'cyan' ? '#22D3EE' : '#6366F1'
                              : accentColor === 'teal' ? '#99F6E4' : accentColor === 'cyan' ? '#A5F3FC' : '#C7D2FE'
                          }}
                        >
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div className="flex items-center gap-2 flex-1">
                              {isFavorite && (
                                <Star className="h-3.5 w-3.5 fill-yellow-500 text-yellow-500 shrink-0" />
                              )}
                              <span className="font-semibold text-sm text-slate-900">{direction.title}</span>
                            </div>
                            {direction.recommendedTemplates.length > 0 && (
                              <Sparkles className="h-3.5 w-3.5 text-teal-600 shrink-0" />
                            )}
                          </div>

                          <p className="text-xs text-slate-600 leading-relaxed mb-3">
                            {direction.shortDesc}
                          </p>

                          <div className="flex flex-wrap gap-1.5">
                            {direction.tags.slice(0, 3).map(tag => (
                              <Badge key={tag} variant="secondary" className="text-xs px-2 py-0.5">
                                {tag}
                              </Badge>
                            ))}
                            {direction.tags.length > 3 && (
                              <Badge variant="outline" className="text-xs px-2 py-0.5">
                                +{direction.tags.length - 3}
                              </Badge>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}

          {filteredDirections.length === 0 && (
            <div className="text-center py-8 text-sm text-muted-foreground">
              No directions match your filters
            </div>
          )}
        </CardContent>
      </Card>

      {/* Right Panel: Inspector */}
      <Card className="col-span-4 overflow-hidden flex flex-col">
        <CardContent className="p-4 flex-1 overflow-y-auto">
          {selectedDirection ? (
            <div className="space-y-4">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-semibold">{selectedDirection.title}</h3>
                  <button
                    onClick={() => toggleFavorite(selectedDirection.id)}
                    className="p-1 hover:bg-accent rounded transition-colors"
                    aria-label={favorites.has(selectedDirection.id) ? 'Remove from favorites' : 'Add to favorites'}
                  >
                    <Star
                      className={`h-4 w-4 ${favorites.has(selectedDirection.id)
                        ? 'fill-yellow-500 text-yellow-500'
                        : 'text-muted-foreground'
                        }`}
                    />
                  </button>
                </div>

                <div className="flex items-center gap-2 text-xs text-muted-foreground mb-3">
                  <span>{categoryGroups[selectedDirection.group].label}</span>
                  <span>/</span>
                  <span>{categoryGroups[selectedDirection.group].subgroupLabel}</span>
                </div>

                <div className="flex flex-wrap gap-1 mb-3">
                  {selectedDirection.tags.map(tag => (
                    <Badge key={tag} variant="secondary" className="text-xs">
                      {tag}
                    </Badge>
                  ))}
                </div>
              </div>

              <div>
                <h4 className="text-sm font-medium mb-2">Description</h4>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {selectedDirection.longDesc}
                </p>
              </div>

              <div>
                <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <BookOpen className="h-4 w-4" />
                  Example Tasks
                </h4>
                <ul className="space-y-1">
                  {selectedDirection.exampleTasks.map((task, idx) => (
                    <li key={idx} className="text-sm text-muted-foreground flex gap-2">
                      <span className="text-primary">•</span>
                      <span>{task}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {selectedDirection.recommendedTemplates.length > 0 && (() => {
                const templates = getTemplatesByDirection(selectedDirection.id)
                return (
                  <div>
                    <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                      <Sparkles className="h-4 w-4 text-primary" />
                      Recommended Templates ({templates.length})
                    </h4>
                    <div className="space-y-2">
                      {templates.map(template => (
                        <div
                          key={template.id}
                          className="p-3 rounded-md border bg-accent/50 space-y-2"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1">
                              <p className="font-medium text-sm">{template.name}</p>
                              <p className="text-xs text-muted-foreground mt-0.5">
                                {template.description}
                              </p>
                            </div>
                            {onApplyTemplate && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => onApplyTemplate(template.id)}
                                className="shrink-0"
                              >
                                Apply
                              </Button>
                            )}
                          </div>
                          <div className="flex flex-wrap gap-2 text-xs">
                            <Badge variant="outline" className="gap-1">
                              <Zap className="h-3 w-3" />
                              {template.config.model.split('-')[0]}
                            </Badge>
                            <Badge variant="outline" className="gap-1">
                              <Clock className="h-3 w-3" />
                              {template.estimatedDuration || 'N/A'}
                            </Badge>
                            <Badge variant="outline">
                              ${template.config.budget || 'N/A'}
                            </Badge>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })()}

              {selectedDirection.riskNotes && (
                <div className="p-3 rounded-md bg-orange-500/10 border border-orange-500/20">
                  <h4 className="text-sm font-medium mb-1 text-orange-600 dark:text-orange-400">
                    ⚠️ Risk Notes
                  </h4>
                  <p className="text-xs text-muted-foreground">
                    {selectedDirection.riskNotes}
                  </p>
                </div>
              )}

              <div className="space-y-2 pt-2 border-t">
                <h4 className="text-sm font-medium mb-2">Quick Actions</h4>

                {selectedDirection.recommendedTemplates.length > 0 && onApplyTemplate && (
                  <Button
                    variant="outline"
                    className="w-full justify-start"
                    onClick={() => onApplyTemplate(selectedDirection.recommendedTemplates[0])}
                  >
                    <Sparkles className="h-4 w-4 mr-2" />
                    Apply Template
                  </Button>
                )}

                {onStartRun && (
                  <Button
                    className="w-full justify-start"
                    onClick={onStartRun}
                  >
                    Start Run (Mock)
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center py-8 px-4">
              <div className="w-20 h-20 rounded-full bg-gradient-to-br from-teal-100 to-cyan-100 flex items-center justify-center mb-4 shadow-sm">
                <Search className="h-10 w-10 text-teal-600" />
              </div>
              <h4 className="font-semibold text-slate-900 mb-2">No Direction Selected</h4>
              <p className="text-sm text-slate-600 max-w-xs mb-4 leading-relaxed">
                Select a research direction from the list to view details, example tasks, and recommended templates
              </p>
              <div className="bg-teal-50 border border-teal-200 rounded-lg p-3 text-left max-w-xs">
                <p className="text-xs font-medium text-teal-900 mb-1">💡 Tips</p>
                <ul className="text-xs text-teal-800 space-y-1">
                  <li>• Use search to filter directions</li>
                  <li>• Star your favorites for quick access</li>
                  <li>• Look for ✨ icon for template support</li>
                </ul>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
