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
  Code2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useReviewLocale } from '@/lib/reviewLocale'
import { AppLogo } from '@/components/branding/AppLogo'

const navigation = [
  { enName: 'Dashboard', zhName: '仪表盘', href: '/', icon: LayoutDashboard },
  { enName: 'Pipeline', zhName: '科研流程', href: '/research/pipeline', icon: FlaskConical },
  { enName: 'Code', zhName: 'Code', href: '/code', icon: Code2 },
  { enName: 'Runs', zhName: '运行记录', href: '/runs', icon: PlayCircle },
  { enName: 'Experiments', zhName: '实验', href: '/experiments', icon: BarChart3 },
  { enName: 'Papers', zhName: '论文', href: '/papers', icon: FileEdit },
  { enName: 'ReviewX', zhName: 'ReviewX', href: '/review/consistency', icon: CheckCircle },
  { enName: 'Settings', zhName: '设置', href: '/settings/providers', icon: Settings },
  { enName: 'System', zhName: '系统', href: '/system/health', icon: Activity },
]

export function Sidebar() {
  const { isChinese } = useReviewLocale()

  return (
    <aside className="w-16 shrink-0 border-r bg-muted/40 sm:w-64">
      <div className="flex h-14 items-center justify-center border-b px-2 sm:justify-start sm:px-4">
        <AppLogo size="sm" variant="icon" className="sm:hidden" />
        <AppLogo size="sm" variant="full" className="hidden sm:flex" />
      </div>
      <nav className="flex flex-col gap-1 p-2 sm:p-4">
        {navigation.map((item) => (
          <NavLink
            key={item.href}
            to={item.href}
            title={isChinese ? item.zhName : item.enName}
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
            <span className="hidden sm:inline">{isChinese ? item.zhName : item.enName}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
