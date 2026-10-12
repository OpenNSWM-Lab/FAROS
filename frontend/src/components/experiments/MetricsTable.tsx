import type { MetricValue } from '@/lib/types'

interface MetricsTableProps {
  metrics: MetricValue[]
}

export function MetricsTable({ metrics }: MetricsTableProps) {
  return (
    <div className="rounded-md border">
      <table className="w-full">
        <thead className="border-b bg-muted/50">
          <tr>
            <th className="px-4 py-3 text-left text-sm font-medium">Metric</th>
            <th className="px-4 py-3 text-right text-sm font-medium">Value</th>
            <th className="px-4 py-3 text-right text-sm font-medium">Mean</th>
            <th className="px-4 py-3 text-right text-sm font-medium">Std Dev</th>
            <th className="px-4 py-3 text-left text-sm font-medium">Unit</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric) => (
            <tr key={metric.name} className="border-b last:border-0">
              <td className="px-4 py-3 text-sm font-medium capitalize">
                {metric.name.replace(/_/g, ' ')}
              </td>
              <td className="px-4 py-3 text-sm text-right font-mono">
                {metric.value.toFixed(3)}
              </td>
              <td className="px-4 py-3 text-sm text-right font-mono">
                {metric.mean.toFixed(3)}
              </td>
              <td className="px-4 py-3 text-sm text-right font-mono text-muted-foreground">
                {metric.std ? `±${metric.std.toFixed(3)}` : '—'}
              </td>
              <td className="px-4 py-3 text-sm text-muted-foreground">
                {metric.unit || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
