import { useReviewLocale } from '@/lib/reviewLocale'

type HeaderVizVariant = 'sparkline' | 'miniBars' | 'donut' | 'metricCapsules'

interface HeaderVizProps {
  variant: HeaderVizVariant
  data?: number[]
  className?: string
}

export function HeaderViz({ variant, data = [], className = '' }: HeaderVizProps) {
  const { text } = useReviewLocale()
  const defaultData = data

  const renderSparkline = () => {
    const points = defaultData
    const max = Math.max(...points)
    const min = Math.min(...points)
    const range = max - min || 1
    const width = 120
    const height = 32
    const step = width / (points.length - 1)

    const pathData = points
      .map((value, i) => {
        const x = i * step
        const y = height - ((value - min) / range) * height
        return `${i === 0 ? 'M' : 'L'} ${x} ${y}`
      })
      .join(' ')

    return (
      <svg width={width} height={height} className={className} data-testid="header-viz">
        <defs>
          <linearGradient id="sparkline-gradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#0EA5A4" stopOpacity="0.3" />
            <stop offset="100%" stopColor="#0EA5A4" stopOpacity="0.05" />
          </linearGradient>
        </defs>
        <path
          d={`${pathData} L ${width} ${height} L 0 ${height} Z`}
          fill="url(#sparkline-gradient)"
        />
        <path
          d={pathData}
          fill="none"
          stroke="#0EA5A4"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    )
  }

  const renderMiniBars = () => {
    const points = defaultData
    const max = Math.max(...points)
    const barWidth = 8
    const gap = 4
    const height = 32

    return (
      <svg
        width={points.length * (barWidth + gap)}
        height={height}
        className={className}
        data-testid="header-viz"
      >
        {points.map((value, i) => {
          const barHeight = (value / max) * height
          return (
            <rect
              key={i}
              x={i * (barWidth + gap)}
              y={height - barHeight}
              width={barWidth}
              height={barHeight}
              fill="#0EA5A4"
              opacity={0.7 + (value / max) * 0.3}
              rx="2"
            />
          )
        })}
      </svg>
    )
  }

  const renderDonut = () => {
    const percentage = defaultData[0] ?? 0
    const radius = 14
    const circumference = 2 * Math.PI * radius
    const offset = circumference - (percentage / 100) * circumference

    return (
      <svg width={40} height={40} className={className} data-testid="header-viz">
        <circle
          cx="20"
          cy="20"
          r={radius}
          fill="none"
          stroke="#E2E8F0"
          strokeWidth="4"
        />
        <circle
          cx="20"
          cy="20"
          r={radius}
          fill="none"
          stroke="#0EA5A4"
          strokeWidth="4"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 20 20)"
        />
        <text
          x="20"
          y="20"
          textAnchor="middle"
          dominantBaseline="middle"
          className="text-xs font-semibold fill-slate-700"
        >
          {percentage}
        </text>
      </svg>
    )
  }

  const renderMetricCapsules = () => {
    const metrics = [
      { label: text('活跃', 'Active'), value: defaultData[0] ?? 0, color: '#0EA5A4' },
      { label: text('成功', 'Success'), value: defaultData[1] ?? 0, color: '#22D3EE' },
      { label: text('待处理', 'Pending'), value: defaultData[2] ?? 0, color: '#6366F1' },
    ]

    return (
      <div className={`flex items-center gap-3 ${className}`} data-testid="header-viz">
        {metrics.map((metric, i) => (
          <div key={i} className="flex items-center gap-2">
            <div className="flex flex-col">
              <span className="text-xs font-medium text-slate-600">{metric.label}</span>
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-bold text-slate-900">{metric.value}%</span>
                <svg width="24" height="12">
                  <rect
                    x="0"
                    y="2"
                    width="24"
                    height="8"
                    rx="4"
                    fill="#E2E8F0"
                  />
                  <rect
                    x="0"
                    y="2"
                    width={(metric.value / 100) * 24}
                    height="8"
                    rx="4"
                    fill={metric.color}
                  />
                </svg>
              </div>
            </div>
          </div>
        ))}
      </div>
    )
  }

  if (defaultData.length === 0) return null

  switch (variant) {
    case 'sparkline':
      return renderSparkline()
    case 'miniBars':
      return renderMiniBars()
    case 'donut':
      return renderDonut()
    case 'metricCapsules':
      return renderMetricCapsules()
    default:
      return null
  }
}
