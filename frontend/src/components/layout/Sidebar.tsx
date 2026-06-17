import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  FlaskConical,
  PlayCircle,
  BarChart3,
  FileEdit,
  CheckCircle,
  Settings,
  Activity,
  Lightbulb,
  Code2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { AppLogo } from '@/components/branding/AppLogo'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Ideas', href: '/research/ideas', icon: Lightbulb },
  { name: 'Plan', href: '/research/planning', icon: FlaskConical },
  { name: 'Code', href: '/code/projects', icon: Code2 },
  { name: 'Runs', href: '/runs', icon: PlayCircle },
  { name: 'Experiments', href: '/experiments', icon: BarChart3 },
  { name: 'Papers', href: '/papers', icon: FileEdit },
  { name: 'Review', href: '/review/consistency', icon: CheckCircle },
  { name: 'Settings', href: '/settings/providers', icon: Settings },
  { name: 'System', href: '/system/health', icon: Activity },
]

export function Sidebar() {
  return (
    <aside className="w-64 border-r bg-muted/40">
      <div className="flex h-14 items-center border-b px-4">
        <AppLogo size="sm" variant="full" />
      </div>
      <nav className="flex flex-col gap-1 p-4">
        {navigation.map((item) => (
          <NavLink
            key={item.name}
            to={item.href}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
              )
            }
          >
            <item.icon className="h-5 w-5" />
            {item.name}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
