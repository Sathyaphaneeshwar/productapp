import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Sun, Moon, Monitor } from 'lucide-react'
import Watchlist from './pages/Watchlist'
import Groups from './pages/Groups'
import Research from './pages/Research'
import Settings from './pages/Settings'
import Activity from './pages/Activity'
import StockDetail from './pages/StockDetail'
import UpdateButton from './components/UpdateButton'
import PollStatusButton from './components/PollStatusButton'

type Theme = 'light' | 'dark' | 'system'
type Page = 'watchlist' | 'groups' | 'research' | 'activity' | 'settings' | 'stock'
type StockRef = { id: number; symbol: string; name: string }
type ActivityLevel = 'all' | 'info' | 'success' | 'error'

function App() {
  const [currentPage, setCurrentPage] = useState<Page>('watchlist')
  const [theme, setTheme] = useState<Theme>('dark')
  const [activityTarget, setActivityTarget] = useState<{ stock: StockRef; level: ActivityLevel } | null>(null)
  const [stockTarget, setStockTarget] = useState<StockRef | null>(null)

  const openActivity = (stock?: StockRef, level: ActivityLevel = 'all') => {
    setActivityTarget(stock ? { stock, level } : null)
    setCurrentPage('activity')
  }

  const openStock = (stock: StockRef) => {
    setStockTarget(stock)
    setCurrentPage('stock')
  }

  // Initialize dark mode on mount
  useEffect(() => {
    document.documentElement.classList.add('dark')
  }, [])

  const cycleTheme = async (event: React.MouseEvent<HTMLButtonElement>) => {
    const themes: Theme[] = ['light', 'dark', 'system']
    const currentIndex = themes.indexOf(theme)
    const nextTheme = themes[(currentIndex + 1) % themes.length]

    // Check if View Transitions API is supported
    if (!document.startViewTransition) {
      // Fallback for browsers that don't support View Transitions
      applyTheme(nextTheme)
      setTheme(nextTheme)
      return
    }

    // Get the button position for the circular expansion
    const button = event.currentTarget
    const rect = button.getBoundingClientRect()
    const x = rect.left + rect.width / 2
    const y = rect.top + rect.height / 2

    // Calculate the maximum radius needed to cover the entire viewport
    const maxRadius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    )

    // Start the view transition
    const transition = document.startViewTransition(() => {
      applyTheme(nextTheme)
      setTheme(nextTheme)
    })

    // Apply the circular reveal animation
    await transition.ready
    document.documentElement.animate(
      {
        clipPath: [
          `circle(0px at ${x}px ${y}px)`,
          `circle(${maxRadius}px at ${x}px ${y}px)`,
        ],
      },
      {
        duration: 500,
        easing: 'ease-in-out',
        pseudoElement: '::view-transition-new(root)',
      }
    )
  }

  const applyTheme = (newTheme: Theme) => {
    if (newTheme === 'dark') {
      document.documentElement.classList.add('dark')
    } else if (newTheme === 'light') {
      document.documentElement.classList.remove('dark')
    } else {
      // System theme
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      if (prefersDark) {
        document.documentElement.classList.add('dark')
      } else {
        document.documentElement.classList.remove('dark')
      }
    }
  }

  const getThemeIcon = () => {
    switch (theme) {
      case 'light':
        return <Sun className="h-5 w-5" />
      case 'dark':
        return <Moon className="h-5 w-5" />
      case 'system':
        return <Monitor className="h-5 w-5" />
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground transition-colors duration-300">
      {/* Header with Navigation */}
      <div className="border-b border-border">
        <div className="max-w-7xl mx-auto px-8 py-6">
          <div className="flex items-center justify-between mb-6">
            <div className="flex-1"></div>
            <h1 className="text-4xl font-bold text-foreground text-center">BLOOMira</h1>
            <div className="flex-1 flex justify-end gap-2">
              <UpdateButton />
              <PollStatusButton onOpenActivity={() => openActivity()} />
              <Button
                variant="ghost"
                size="icon"
                onClick={cycleTheme}
                className="rounded-full hover:bg-accent transition-all duration-200"
              >
                {getThemeIcon()}
              </Button>
            </div>
          </div>

          {/* Navigation Tabs */}
          <div className="flex justify-center gap-8">
            <button
              onClick={() => setCurrentPage('watchlist')}
              className={`text-xl font-semibold pb-2 border-b-2 transition-colors ${currentPage === 'watchlist'
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
            >
              Watchlist
            </button>
            <button
              onClick={() => setCurrentPage('groups')}
              className={`text-xl font-semibold pb-2 border-b-2 transition-colors ${currentPage === 'groups'
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
            >
              Groups
            </button>
            <button
              onClick={() => setCurrentPage('research')}
              className={`text-xl font-semibold pb-2 border-b-2 transition-colors ${currentPage === 'research'
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
            >
              Research
            </button>
            <button
              onClick={() => openActivity()}
              className={`text-xl font-semibold pb-2 border-b-2 transition-colors ${currentPage === 'activity'
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
            >
              Activity
            </button>
            <button
              onClick={() => setCurrentPage('settings')}
              className={`text-xl font-semibold pb-2 border-b-2 transition-colors ${currentPage === 'settings'
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
            >
              Settings
            </button>
          </div>
        </div>
      </div>

      {/* Page Content */}
      {currentPage === 'watchlist' && (
        <Watchlist onOpenActivity={openActivity} onOpenStock={openStock} />
      )}
      {currentPage === 'groups' && <Groups />}
      {currentPage === 'research' && <Research />}
      {currentPage === 'activity' && (
        <Activity
          initialStock={activityTarget?.stock ?? null}
          initialLevel={activityTarget?.level ?? 'all'}
          onOpenStock={openStock}
        />
      )}
      {currentPage === 'settings' && <Settings />}
      {currentPage === 'stock' && stockTarget && (
        <StockDetail
          stockId={stockTarget.id}
          onBack={() => setCurrentPage('watchlist')}
          onOpenActivity={(level = 'all') => openActivity(stockTarget, level)}
        />
      )}
    </div>
  )
}

export default App
