import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

const API_URL = 'http://localhost:5001/api'

type PollStatusResponse = {
    scheduler_running: boolean
    is_polling: boolean
    poll_interval_seconds: number
    next_poll_at?: string | null
    next_poll_in_seconds?: number | null
}

export default function PollStatusButton() {
    const [status, setStatus] = useState<PollStatusResponse | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [now, setNow] = useState(Date.now())
    const [triggering, setTriggering] = useState(false)

    const fetchStatus = async () => {
        try {
            const response = await fetch(`${API_URL}/poll/status`)
            if (!response.ok) {
                throw new Error(`Status ${response.status}`)
            }
            const data = await response.json()
            setStatus(data)
            setError(null)
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch poll status')
        }
    }

    useEffect(() => {
        fetchStatus()
        const interval = setInterval(fetchStatus, 1000)
        return () => clearInterval(interval)
    }, [])

    useEffect(() => {
        const interval = setInterval(() => setNow(Date.now()), 1000)
        return () => clearInterval(interval)
    }, [])

    const handleTrigger = async () => {
        setTriggering(true)
        try {
            await fetch(`${API_URL}/poll/trigger`, { method: 'POST' })
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to trigger poll')
        } finally {
            setTriggering(false)
            fetchStatus()
        }
    }

    const schedulerRunning = status?.scheduler_running ?? false
    const isPolling = status?.is_polling ?? false
    const intervalSeconds = status?.poll_interval_seconds ?? 120
    const nextPollAtMs = status?.next_poll_at ? Date.parse(status.next_poll_at) : null
    const nextInSeconds = nextPollAtMs
        ? Math.max(0, Math.ceil((nextPollAtMs - now) / 1000))
        : status?.next_poll_in_seconds ?? null

    const displaySeconds = nextInSeconds !== null ? Math.min(nextInSeconds, 999) : null
    const label = !error && schedulerRunning && displaySeconds !== null ? `${displaySeconds}` : '--'

    const progress = !error && schedulerRunning && displaySeconds !== null && intervalSeconds > 0
        ? ((intervalSeconds - Math.min(displaySeconds, intervalSeconds)) / intervalSeconds) * 100
        : 0

    let ringClass = 'stroke-muted-foreground opacity-60'
    let progressClass = 'stroke-blue-500'
    if (error) {
        progressClass = 'stroke-red-500'
    } else if (!schedulerRunning) {
        progressClass = 'stroke-amber-500'
    } else if (isPolling || triggering) {
        progressClass = 'stroke-emerald-500 animate-pulse'
    }

    const tooltip = error
        ? 'Poll status unavailable'
        : isPolling || triggering
            ? 'Poll running'
            : 'Click to run poll now'

    return (
        <Button
            variant="ghost"
            size="icon"
            onClick={handleTrigger}
            disabled={triggering}
            title={tooltip}
            className="rounded-full hover:bg-accent transition-all duration-200"
        >
            <div className="relative h-8 w-8">
                <svg className="absolute inset-0 -rotate-90" viewBox="0 0 36 36">
                    <circle
                        cx="18"
                        cy="18"
                        r="15.5"
                        fill="transparent"
                        className={ringClass}
                        strokeWidth="3"
                    />
                    <circle
                        cx="18"
                        cy="18"
                        r="15.5"
                        fill="transparent"
                        className={`${progressClass} transition-[stroke-dasharray] duration-300`}
                        strokeWidth="3"
                        strokeDasharray={`${progress} 100`}
                        strokeLinecap="round"
                    />
                </svg>
                <div className="absolute inset-0 flex items-center justify-center text-[10px] font-semibold text-foreground">
                    {label}
                </div>
            </div>
        </Button>
    )
}
