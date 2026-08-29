import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { DetailSplitPane } from '@/components/layout/DetailSplitPane'
import { MetaChip } from '@/components/ui/MetaChips'
import { SummaryStrip } from '@/components/detail/SummaryStrip'
import { SectionCard } from '@/components/detail/SectionCard'
import { InfoGrid } from '@/components/detail/InfoGrid'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { ArrowLeft, ExternalLink, TrendingUp, TrendingDown, BarChart3, Zap, Settings, Link2 } from 'lucide-react'
import { useExperiment, useCompareExperiments } from '@/lib/hooks/useApi'
import { formatRelativeTime } from '@/lib/utils'
import { MetricsTable } from '@/components/experiments/MetricsTable'
import { MetricChart } from '@/components/charts/MetricChart'

export function ExperimentDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data: experiment, isLoading, error } = useExperiment(id!)
  const [activeTab, setActiveTab] = useState('overview')

  // For compare tab - select runs to compare
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([])
  const { data: compareExperiments } = useCompareExperiments(
    selectedRunIds.length > 0 ? selectedRunIds : []
  )

  const toggleRunSelection = (runId: string) => {
    setSelectedRunIds(prev =>
      prev.includes(runId)
        ? prev.filter(id => id !== runId)
        : [...prev, runId]
    )
  }

  if (isLoading) {
    return (
      <AppPageLayout title="Loading..." subtitle="Fetching experiment details">
        <Skeleton className="h-96 w-full" />
      </AppPageLayout>
    )
  }

  if (error || !experiment) {
    return (
      <AppPageLayout title="Experiment Not Found" subtitle="Unable to load experiment details">
        <div className="bg-white border border-slate-200 rounded-lg p-8">
          <p className="text-center text-red-600">
            {error?.message || 'Experiment not found'}
          </p>
        </div>
      </AppPageLayout>
    )
  }

  const costMetric = experiment.metrics.find(m => m.name === 'cost')
  const latencyMetric = experiment.metrics.find(m => m.name === 'latency_ms')
  const accuracyMetric = experiment.metrics.find(m => m.name === 'accuracy')

  return (
    <AppPageLayout
      title={`Experiment ${experiment.id.slice(0, 8)}`}
      subtitle={experiment.name || 'Experiment Details'}
      headerViz="donut"
      headerVizData={accuracyMetric ? [accuracyMetric.value * 100] : undefined}
      breadcrumb={
        <button onClick={() => navigate('/experiments')} className="flex items-center gap-2 text-teal-600 hover:text-teal-700">
          <ArrowLeft className="h-4 w-4" />
          Back to Experiments
        </button>
      }
      actions={
        <Badge variant="outline" className="capitalize">{experiment.status}</Badge>
      }
    >
      {/* Metrics Summary Strip */}
      <SummaryStrip>
        <MetaChip label={experiment.task} variant="teal" icon={<BarChart3 className="h-3 w-3" />} />
        <MetaChip label={`${experiment.runIds.length} runs`} variant="cyan" />
        {costMetric && <MetaChip label={`$${costMetric.mean.toFixed(2)} avg cost`} variant="indigo" />}
        {latencyMetric && <MetaChip label={`${latencyMetric.mean.toFixed(0)}ms avg latency`} variant="slate" />}
        {accuracyMetric && <MetaChip label={`${(accuracyMetric.mean * 100).toFixed(1)}% accuracy`} variant="teal" />}
      </SummaryStrip>

      <DetailSplitPane
        leftContent={
          <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="metrics">Metrics</TabsTrigger>
              <TabsTrigger value="compare">Compare</TabsTrigger>
            </TabsList>

            <TabsContent value="overview">
              <div className="space-y-6">
                {/* Experiment Summary */}
                <SectionCard title="Experiment Summary" icon={Zap} accentColor="teal">
                  <InfoGrid
                    columns={3}
                    items={[
                      {
                        label: 'Task',
                        value: experiment.task
                      },
                      {
                        label: 'Status',
                        value: <Badge variant="outline" className="capitalize">{experiment.status}</Badge>
                      },
                      {
                        label: 'Linked Runs',
                        value: experiment.runIds.length
                      },
                      {
                        label: 'Created',
                        value: formatRelativeTime(experiment.createdAt)
                      },
                      {
                        label: 'Updated',
                        value: formatRelativeTime(experiment.updatedAt)
                      },
                    ]}
                  />
                </SectionCard>

                {/* Parameters */}
                <SectionCard title="Configuration" icon={Settings} accentColor="cyan">
                  <pre className="text-xs bg-muted/50 p-4 rounded border overflow-x-auto font-mono">
                    {JSON.stringify(experiment.parameters, null, 2)}
                  </pre>
                </SectionCard>

                {/* Linked Runs */}
                <SectionCard title={`Linked Runs (${experiment.runIds.length})`} icon={Link2} accentColor="indigo">
                  <div className="space-y-2">
                    {experiment.runIds.map((runId) => (
                      <Link
                        key={runId}
                        to={`/runs/${runId}`}
                        className="flex items-center justify-between p-3 border rounded-md hover:bg-muted/50 transition-colors"
                      >
                        <span className="text-sm font-mono">{runId}</span>
                        <ExternalLink className="h-4 w-4 text-muted-foreground" />
                      </Link>
                    ))}
                  </div>
                </SectionCard>
              </div>
            </TabsContent>

            <TabsContent value="metrics">
              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle>Performance Metrics</CardTitle>
                    <CardDescription>Aggregated results with mean and standard deviation</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <MetricsTable metrics={experiment.metrics} />
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Metrics Visualization</CardTitle>
                    <CardDescription>Visual comparison of metric values</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-6">
                    <div className="grid gap-6 md:grid-cols-2">
                      {experiment.metrics.slice(0, 4).map((metric, index) => (
                        <MetricChart
                          key={metric.name}
                          data={[
                            { name: 'Min', value: metric.mean - (metric.std || 0) },
                            { name: 'Mean', value: metric.mean },
                            { name: 'Max', value: metric.mean + (metric.std || 0) },
                          ]}
                          dataKey="value"
                          title={metric.name.replace(/_/g, ' ').toUpperCase()}
                          color={['#8884d8', '#82ca9d', '#ffc658', '#ff7c7c'][index % 4]}
                        />
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Cost vs Latency Trade-off</CardTitle>
                    <CardDescription>Performance efficiency analysis</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="grid gap-4 md:grid-cols-2">
                      {experiment.metrics.find(m => m.name === 'cost') && (
                        <div className="flex items-center gap-3 p-4 border rounded-md">
                          <div className="flex-1">
                            <div className="text-sm font-medium">Average Cost</div>
                            <div className="text-2xl font-bold">
                              ${experiment.metrics.find(m => m.name === 'cost')?.mean.toFixed(2)}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              ±${experiment.metrics.find(m => m.name === 'cost')?.std?.toFixed(2)}
                            </div>
                          </div>
                          <TrendingDown className="h-8 w-8 text-success" />
                        </div>
                      )}
                      {experiment.metrics.find(m => m.name === 'latency_ms') && (
                        <div className="flex items-center gap-3 p-4 border rounded-md">
                          <div className="flex-1">
                            <div className="text-sm font-medium">Average Latency</div>
                            <div className="text-2xl font-bold">
                              {experiment.metrics.find(m => m.name === 'latency_ms')?.mean.toFixed(0)}ms
                            </div>
                            <div className="text-xs text-muted-foreground">
                              ±{experiment.metrics.find(m => m.name === 'latency_ms')?.std?.toFixed(0)}ms
                            </div>
                          </div>
                          <TrendingUp className="h-8 w-8 text-muted-foreground" />
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            <TabsContent value="compare">
              <Card>
                <CardHeader>
                  <CardTitle>Compare Runs</CardTitle>
                  <CardDescription>
                    Select runs to compare metrics across different executions
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <div className="text-sm font-medium">Select Runs to Compare:</div>
                    <div className="space-y-2">
                      {experiment.runIds.map((runId) => (
                        <label
                          key={runId}
                          className="flex items-center gap-3 p-3 border rounded-md cursor-pointer hover:bg-muted/50 transition-colors"
                        >
                          <input
                            type="checkbox"
                            checked={selectedRunIds.includes(runId)}
                            onChange={() => toggleRunSelection(runId)}
                            className="h-4 w-4"
                          />
                          <span className="text-sm font-mono flex-1">{runId}</span>
                          <Link to={`/runs/${runId}`}>
                            <ExternalLink className="h-4 w-4 text-muted-foreground" />
                          </Link>
                        </label>
                      ))}
                    </div>
                  </div>

                  {selectedRunIds.length > 0 && compareExperiments && (
                    <div className="space-y-4 pt-4 border-t">
                      <div className="text-sm font-medium">
                        Comparing {selectedRunIds.length} run{selectedRunIds.length > 1 ? 's' : ''}
                      </div>
                      <div className="rounded-md border">
                        <table className="w-full">
                          <thead className="border-b bg-muted/50">
                            <tr>
                              <th className="px-4 py-3 text-left text-sm font-medium">Metric</th>
                              {compareExperiments.map((exp) => (
                                <th key={exp.id} className="px-4 py-3 text-right text-sm font-medium">
                                  {exp.id.split('_').pop()}
                                </th>
                              ))}
                              <th className="px-4 py-3 text-right text-sm font-medium">Best</th>
                            </tr>
                          </thead>
                          <tbody>
                            {experiment.metrics.map((metric) => {
                              const values = compareExperiments.map(exp =>
                                exp.metrics.find(m => m.name === metric.name)?.mean || 0
                              )
                              const bestValue = Math.max(...values)

                              return (
                                <tr key={metric.name} className="border-b last:border-0">
                                  <td className="px-4 py-3 text-sm font-medium capitalize">
                                    {metric.name.replace(/_/g, ' ')}
                                  </td>
                                  {compareExperiments.map((exp) => {
                                    const expMetric = exp.metrics.find(m => m.name === metric.name)
                                    const isBest = expMetric?.mean === bestValue
                                    return (
                                      <td
                                        key={exp.id}
                                        className={`px-4 py-3 text-sm text-right font-mono ${isBest ? 'font-bold text-success' : ''}`}
                                      >
                                        {expMetric?.mean.toFixed(3) || '—'}
                                      </td>
                                    )
                                  })}
                                  <td className="px-4 py-3 text-sm text-right font-mono font-bold text-success">
                                    {bestValue.toFixed(3)}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {selectedRunIds.length === 0 && (
                    <div className="text-center py-8 text-sm text-muted-foreground">
                      Select at least one run to compare metrics
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        }
        rightContent={
          <div className="space-y-4">
            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <h3 className="text-sm font-semibold text-slate-900 mb-3">Metadata</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-slate-600">Task:</span>
                  <span className="font-medium">{experiment.task}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-600">Status:</span>
                  <Badge variant="outline" className="capitalize text-xs">{experiment.status}</Badge>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-600">Runs:</span>
                  <span>{experiment.runIds.length}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-600">Created:</span>
                  <span className="text-xs">{formatRelativeTime(experiment.createdAt)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-600">Updated:</span>
                  <span className="text-xs">{formatRelativeTime(experiment.updatedAt)}</span>
                </div>
              </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <h3 className="text-sm font-semibold text-slate-900 mb-3">Key Metrics</h3>
              <div className="space-y-3">
                {costMetric && (
                  <div>
                    <div className="text-xs text-slate-600 mb-1">Cost</div>
                    <div className="text-lg font-semibold text-slate-900">${costMetric.mean.toFixed(2)}</div>
                    <div className="text-xs text-slate-500">±${costMetric.std?.toFixed(2) || '0.00'}</div>
                  </div>
                )}
                {latencyMetric && (
                  <div>
                    <div className="text-xs text-slate-600 mb-1">Latency</div>
                    <div className="text-lg font-semibold text-slate-900">{latencyMetric.mean.toFixed(0)}ms</div>
                    <div className="text-xs text-slate-500">±{latencyMetric.std?.toFixed(0) || '0'}ms</div>
                  </div>
                )}
                {accuracyMetric && (
                  <div>
                    <div className="text-xs text-slate-600 mb-1">Accuracy</div>
                    <div className="text-lg font-semibold text-slate-900">{(accuracyMetric.mean * 100).toFixed(1)}%</div>
                    <div className="text-xs text-slate-500">±{((accuracyMetric.std || 0) * 100).toFixed(1)}%</div>
                  </div>
                )}
              </div>
            </div>

            <div className="bg-teal-50 border border-teal-200 rounded-lg p-4">
              <h3 className="text-sm font-semibold text-teal-900 mb-2">Linked Runs</h3>
              <div className="space-y-1">
                {experiment.runIds.slice(0, 3).map((runId) => (
                  <Link
                    key={runId}
                    to={`/runs/${runId}`}
                    className="block text-xs font-mono text-teal-700 hover:text-teal-900 hover:underline"
                  >
                    {runId}
                  </Link>
                ))}
                {experiment.runIds.length > 3 && (
                  <div className="text-xs text-teal-600">+{experiment.runIds.length - 3} more</div>
                )}
              </div>
            </div>
          </div>
        }
        rightTitle="Inspector"
      />
    </AppPageLayout>
  )
}
