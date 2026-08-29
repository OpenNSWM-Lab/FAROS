import { cn } from '@/lib/utils'

interface AppLogoProps {
  size?: 'sm' | 'md' | 'lg'
  variant?: 'full' | 'icon'
  className?: string
}

export function AppLogo({ size = 'md', variant = 'full', className }: AppLogoProps) {
  const sizeClasses = {
    sm: 'h-6 w-6',
    md: 'h-8 w-8',
    lg: 'h-12 w-12',
  }

  const textSizeClasses = {
    sm: 'text-base',
    md: 'text-lg',
    lg: 'text-2xl',
  }

  return (
    <div className={cn('flex items-center gap-2', className)}>
      {/* Geometric Logo Icon - Neural Network / Research Graph */}
      <div className={cn(
        'relative rounded-lg bg-gradient-to-br from-teal-500 via-cyan-500 to-indigo-500 flex items-center justify-center shadow-sm',
        sizeClasses[size]
      )}>
        {/* Abstract geometric pattern: interconnected nodes representing research network */}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          className="w-[70%] h-[70%]"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* Central node */}
          <circle cx="12" cy="12" r="2.5" fill="white" fillOpacity="0.95" />
          
          {/* Outer nodes */}
          <circle cx="6" cy="6" r="1.5" fill="white" fillOpacity="0.8" />
          <circle cx="18" cy="6" r="1.5" fill="white" fillOpacity="0.8" />
          <circle cx="6" cy="18" r="1.5" fill="white" fillOpacity="0.8" />
          <circle cx="18" cy="18" r="1.5" fill="white" fillOpacity="0.8" />
          
          {/* Connection lines */}
          <line x1="12" y1="12" x2="6" y2="6" stroke="white" strokeWidth="1.2" strokeOpacity="0.6" />
          <line x1="12" y1="12" x2="18" y2="6" stroke="white" strokeWidth="1.2" strokeOpacity="0.6" />
          <line x1="12" y1="12" x2="6" y2="18" stroke="white" strokeWidth="1.2" strokeOpacity="0.6" />
          <line x1="12" y1="12" x2="18" y2="18" stroke="white" strokeWidth="1.2" strokeOpacity="0.6" />
          
          {/* Outer ring for system boundary */}
          <circle cx="12" cy="12" r="9" stroke="white" strokeWidth="0.8" strokeOpacity="0.4" fill="none" />
        </svg>
      </div>

      {/* Text */}
      {variant === 'full' && (
        <span className={cn('font-semibold font-display text-foreground', textSizeClasses[size])}>
          FAROS
        </span>
      )}
    </div>
  )
}
