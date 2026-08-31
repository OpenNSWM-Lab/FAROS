import { ReactNode } from 'react'

interface InfoGridItem {
  label: string
  value: ReactNode
}

interface InfoGridProps {
  items: InfoGridItem[]
  columns?: 1 | 2 | 3 | 4
}

export function InfoGrid({ items, columns = 3 }: InfoGridProps) {
  const gridCols = {
    1: 'grid-cols-1',
    2: 'md:grid-cols-2',
    3: 'md:grid-cols-2 lg:grid-cols-3',
    4: 'md:grid-cols-2 lg:grid-cols-4',
  }

  return (
    <div className={`grid gap-6 ${gridCols[columns]}`}>
      {items.map((item) => (
        <div key={item.label} className="space-y-2">
          <div className="text-sm font-medium text-slate-600">{item.label}</div>
          <div className="text-sm font-semibold text-slate-900">{item.value}</div>
        </div>
      ))}
    </div>
  )
}
