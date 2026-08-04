import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  FlaskConical,
  PlayCircle,
  BarChart3,
  FileEdit,
  CheckCircle,
  MessageSquareText,
  Settings,
  Activity,
  Code2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { AppLogo } from '@/components/branding/AppLogo'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Pipeline', href: '/research/pipeline', icon: FlaskConical },
  { name: 'Code', href: '/code', icon: Code2 },
  { name: 'Runs', href: '/runs', icon: PlayCircle },
  { name: 'Experiments', href: '/experiments', icon: BarChart3 },
  { name: 'Papers', href: '/papers', icon: FileEdit },
  { name: 'ReviewX', href: '/review/consistency', icon: CheckCircle },
  { name: 'Legacy Review', href: '/review/simulator', icon: MessageSquareText },
  { name: 'Settings', href: '/settings/providers', icon: Settings },
  { name: 'System', href: '/system/health', icon: Activity },
]

export function Sidebar() {
  return (
    <aside className="w-16 shrink-0 border-r bg-muted/40 sm:w-64">
      <div className="flex h-14 items-center justify-center border-b px-2 sm:justify-start sm:px-4">
        <AppLogo size="sm" variant="icon" className="sm:hidden" />
        <AppLogo size="sm" variant="full" className="hidden sm:flex" />
      </div>
      <nav className="flex flex-col gap-1 p-2 sm:p-4">
        {navigation.map((item) => (
          <NavLink
            key={item.name}
            to={item.href}
            title={item.name}
            className={({ isActive }) =>
              cn(
                'flex items-center justify-center gap-3 rounded-md px-2 py-2 text-sm font-medium transition-colors sm:justify-start sm:px-3',
                isActive
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
              )
            }
          >
            <item.icon className="h-5 w-5 shrink-0" />
            <span className="hidden sm:inline">{item.name}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
