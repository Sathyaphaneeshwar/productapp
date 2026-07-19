import { useEffect, useRef, useState } from 'react'
import { Activity, AlertTriangle, ChevronRight, Loader2, Play, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'

const API_URL = 'http://127.0.0.1:5001/api'

type QueueCounts = {
    waiting?: number
    active?: number
    failed?: number
    current?: string | null
}

type QueueHealthResponse = {
    scheduler_running?: boolean
    running?: boolean
    is_polling?: boolean
    server_now_ms?: number
    next_check_at_ms?: number | null
    next_tick_at_ms?: number | null
    failed_total?: number
    queue_ok?: boolean
    analysis?: QueueCounts
    email?: QueueCounts
    transcripts?: {
        watching?: number
        upcoming?: number
        failed?: number
    }
}

type EngineStatus = 'online' | 'starting' | 'restarting' | 'offline'

type PollStatusButtonProps = {
    onOpenActivity?: () => void
}

const formatCountdown = (seconds: number) => {
    if (seconds >= 86400) return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`
    if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
    if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
    return `${seconds}s`
}

const countLabel = (count: number | undefined, singular: string, plural = `${singular}s`) =>
    `${count ?? 0} ${(count ?? 0) === 1 ? singular : plural}`

export default function PollStatusButton({ onOpenActivity }: PollStatusButtonProps) {
    const [status, setStatus] = useState<QueueHealthResponse | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [now, setNow] = useState(Date.now())
    const [clockOffsetMs, setClockOffsetMs] = useState(0)
    const [isOpen, setIsOpen] = useState(false)
    const [triggering, setTriggering] = useState(false)
    const [retrying, setRetrying] = useState(false)
    const [engineStatus, setEngineStatus] = useState<EngineStatus>('starting')
    const statusRequestInFlight = useRef(false)
    const containerRef = useRef<HTMLDivElement>(null)

    const fetchStatus = async () => {
        if (statusRequestInFlight.current) return
        statusRequestInFlight.current = true
        try {
            const response = await fetch(`${API_URL}/queue/health`)
            if (!response.ok) throw new Error(`Status ${response.status}`)
            const data: QueueHealthResponse = await response.json()
            setStatus(data)
            if (typeof data.server_now_ms === 'number') {
                setClockOffsetMs(data.server_now_ms - Date.now())
            }
            setError(null)
            setEngineStatus('online')
        } catch (requestError) {
            setError(requestError instanceof Error ? requestError.message : 'Engine unavailable')
            setEngineStatus('offline')
        } finally {
            statusRequestInFlight.current = false
        }
    }

    useEffect(() => {
        fetchStatus()
        const interval = window.setInterval(fetchStatus, 15_000)
        return () => window.clearInterval(interval)
    }, [])

    useEffect(() => {
        const interval = window.setInterval(() => setNow(Date.now()), 1000)
        return () => window.clearInterval(interval)
    }, [])

    useEffect(() => {
        window.electronUpdater?.onEngineStatus?.((event) => {
            const nextStatus = event.status as EngineStatus
            setEngineStatus(nextStatus)
            if (nextStatus === 'online') fetchStatus()
        })
    }, [])

    useEffect(() => {
        const handleOutsideClick = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false)
            }
        }
        document.addEventListener('mousedown', handleOutsideClick)
        return () => document.removeEventListener('mousedown', handleOutsideClick)
    }, [])

    const handleTrigger = async () => {
        setTriggering(true)
        try {
            const response = await fetch(`${API_URL}/poll/trigger`, { method: 'POST' })
            if (!response.ok) throw new Error(`Check failed (${response.status})`)
            await fetchStatus()
        } catch (requestError) {
            setError(requestError instanceof Error ? requestError.message : 'Check failed')
        } finally {
            setTriggering(false)
        }
    }

    const handleRetryFailed = async () => {
        setRetrying(true)
        try {
            const response = await fetch(`${API_URL}/queue/retry-failed`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ queue: 'all' }),
            })
            if (!response.ok) throw new Error(`Retry failed (${response.status})`)
            await fetchStatus()
        } catch (requestError) {
            setError(requestError instanceof Error ? requestError.message : 'Retry failed')
        } finally {
            setRetrying(false)
        }
    }

    const schedulerRunning = status?.scheduler_running ?? status?.running ?? false
    const correctedNow = now + clockOffsetMs
    const nextAtMs = status?.next_check_at_ms ?? status?.next_tick_at_ms ?? null
    const nextInSeconds = nextAtMs === null
        ? null
        : Math.max(0, Math.ceil((nextAtMs - correctedNow) / 1000))
    const failedTotal = status?.failed_total ?? 0
    const isWorking = Boolean(
        status?.is_polling
        || (status?.analysis?.active ?? 0) > 0
        || (status?.email?.active ?? 0) > 0
    )

    let dotClass = 'bg-blue-500'
    let stateLabel = nextInSeconds === null ? 'Idle' : `next check in ${formatCountdown(nextInSeconds)}`
    if (error || engineStatus === 'offline') {
        dotClass = 'bg-red-500'
        stateLabel = 'Engine offline'
    } else if (engineStatus === 'restarting' || engineStatus === 'starting') {
        dotClass = 'bg-amber-500 animate-pulse'
        stateLabel = engineStatus === 'restarting' ? 'Engine restarting…' : 'Engine starting…'
    } else if (failedTotal > 0 || !schedulerRunning || status?.queue_ok === false) {
        dotClass = 'bg-amber-500'
        stateLabel = `${failedTotal} failed ${failedTotal === 1 ? 'job' : 'jobs'}`
    } else if (isWorking) {
        dotClass = 'bg-emerald-500 animate-pulse'
        stateLabel = status?.analysis?.current
            ? `Analyzing ${status.analysis.current}`
            : 'Engine working'
    }

    return (
        <div ref={containerRef} className="relative">
            <button
                type="button"
                onClick={() => setIsOpen((open) => !open)}
                className="h-9 max-w-[260px] rounded-full border border-border bg-card px-3 text-sm transition-colors hover:bg-accent"
                title="Open queue engine status"
                aria-expanded={isOpen}
            >
                <span className="flex items-center gap-2">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dotClass}`} />
                    <span className="font-medium">Engine</span>
                    <span className="truncate text-xs text-muted-foreground">{stateLabel}</span>
                    {failedTotal > 0 && (
                        <span className="rounded-full bg-amber-500/20 px-1.5 text-[11px] font-semibold text-amber-500">
                            {failedTotal}
                        </span>
                    )}
                </span>
            </button>

            {isOpen && (
                <div className="absolute right-0 top-11 z-[100] w-[390px] rounded-xl border border-border bg-popover p-4 text-popover-foreground shadow-2xl">
                    <div className="mb-4 flex items-center justify-between">
                        <div>
                            <p className="font-semibold">Queue engine</p>
                            <p className="text-xs text-muted-foreground">{stateLabel}</p>
                        </div>
                        <Activity className="h-5 w-5 text-muted-foreground" />
                    </div>

                    <div className="space-y-3">
                        <div className="rounded-lg border border-border p-3">
                            <div className="flex items-center justify-between">
                                <span className="font-medium">Transcript polling</span>
                                <span className="text-xs text-muted-foreground">
                                    {nextInSeconds === null ? 'Not scheduled' : `next in ${formatCountdown(nextInSeconds)}`}
                                </span>
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                                {countLabel(status?.transcripts?.watching, 'stock')} watched ·{' '}
                                {countLabel(status?.transcripts?.upcoming, 'upcoming call')}
                            </p>
                        </div>

                        <div className="rounded-lg border border-border p-3">
                            <div className="flex items-center justify-between">
                                <span className="font-medium">Analysis</span>
                                {(status?.analysis?.failed ?? 0) > 0 && (
                                    <span className="text-xs font-medium text-amber-500">
                                        {status?.analysis?.failed} failed
                                    </span>
                                )}
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                                {countLabel(status?.analysis?.active, 'running analysis', 'running analyses')} ·{' '}
                                {countLabel(status?.analysis?.waiting, 'waiting job')}
                            </p>
                            {status?.analysis?.current && (
                                <p className="mt-1 truncate text-xs text-emerald-500">
                                    {status.analysis.current}
                                </p>
                            )}
                        </div>

                        <div className="rounded-lg border border-border p-3">
                            <div className="flex items-center justify-between">
                                <span className="font-medium">Email</span>
                                {(status?.email?.failed ?? 0) > 0 && (
                                    <span className="text-xs font-medium text-amber-500">
                                        {status?.email?.failed} failed
                                    </span>
                                )}
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                                {countLabel(status?.email?.active, 'sending email', 'sending emails')} ·{' '}
                                {countLabel(status?.email?.waiting, 'queued email')}
                            </p>
                        </div>
                    </div>

                    {failedTotal > 0 && (
                        <div className="mt-3 flex items-center justify-between rounded-lg bg-amber-500/10 p-3 text-amber-500">
                            <span className="flex items-center gap-2 text-xs">
                                <AlertTriangle className="h-4 w-4" />
                                {failedTotal} jobs need attention
                            </span>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={handleRetryFailed}
                                disabled={retrying}
                                className="h-7 text-amber-500 hover:text-amber-400"
                            >
                                {retrying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="mr-1 h-3.5 w-3.5" />}
                                Retry
                            </Button>
                        </div>
                    )}

                    <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
                        <Button
                            size="sm"
                            onClick={handleTrigger}
                            disabled={triggering || engineStatus === 'offline'}
                        >
                            {triggering ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <Play className="mr-2 h-4 w-4" />
                            )}
                            Check now
                        </Button>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                                setIsOpen(false)
                                onOpenActivity?.()
                            }}
                        >
                            Details <ChevronRight className="ml-1 h-4 w-4" />
                        </Button>
                    </div>
                </div>
            )}
        </div>
    )
}
