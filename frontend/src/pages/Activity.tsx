import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
    Activity as ActivityIcon,
    CheckCircle2,
    CircleAlert,
    Clock3,
    ExternalLink,
    Info,
    Loader2,
    RefreshCw,
    Search,
    X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const API_URL = 'http://127.0.0.1:5001/api'

type ActivityLevel = 'info' | 'success' | 'error'

type StockOption = {
    id: number
    symbol: string
    name: string
}

type ActivityEvent = {
    id: string
    stock_id: number
    symbol: string
    stock_name: string
    stage: string
    level: ActivityLevel
    message: string
    quarter?: string | null
    year?: number | null
    event_at?: string | null
    details?: Record<string, unknown> | null
}

type ActivityProps = {
    initialStock?: StockOption | null
    initialLevel?: 'all' | ActivityLevel
    onOpenStock?: (stock: StockOption) => void
}

type PipelineOverview = {
    stock: StockOption
    counts: {
        groups: number
        analyses: number
        deep_research_done: number
        document_research_done: number
    }
    pipeline: {
        fetch_schedule?: {
            last_status?: string | null
            last_checked_at?: string | null
            next_check_at?: string | null
            attempts?: number
            locked_until?: string | null
        } | null
        latest_transcript?: {
            status?: string | null
            analysis_status?: string | null
            analysis_error?: string | null
            quarter?: string | null
            year?: number | null
        } | null
        latest_analysis_job?: {
            status?: string | null
            attempts?: number
            retry_next_at?: string | null
            locked_until?: string | null
            error_message?: string | null
            updated_at?: string | null
        } | null
        latest_email?: {
            status?: string | null
            attempts?: number
            retry_next_at?: string | null
            updated_at?: string | null
        } | null
        latest_group_research?: {
            status?: string | null
            group_name?: string | null
            error_message?: string | null
            updated_at?: string | null
        } | null
        latest_document_research?: {
            status?: string | null
            error_message?: string | null
            updated_at?: string | null
        } | null
    }
}

const LEVEL_STYLES: Record<ActivityLevel, string> = {
    success: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    error: 'border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400',
    info: 'border-blue-500/30 bg-blue-500/10 text-blue-600 dark:text-blue-400',
}

const STAGE_LABELS: Record<string, string> = {
    transcript: 'Transcript',
    analysis: 'Analysis',
    email: 'Email',
    group_research: 'Group research',
    scheduler: 'Scheduler',
    system: 'System',
}

const formatTimestamp = (value?: string | null) => {
    if (!value) return 'Unknown time'
    const parsed = new Date(value)
    if (Number.isNaN(parsed.getTime())) return value
    return parsed.toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
    })
}

const LevelIcon = ({ level }: { level: ActivityLevel }) => {
    if (level === 'success') return <CheckCircle2 className="h-4 w-4" />
    if (level === 'error') return <CircleAlert className="h-4 w-4" />
    return <Info className="h-4 w-4" />
}

export default function Activity({ initialStock = null, initialLevel = 'all', onOpenStock }: ActivityProps) {
    const [events, setEvents] = useState<ActivityEvent[]>([])
    const [selectedStock, setSelectedStock] = useState<StockOption | null>(initialStock)
    const [searchQuery, setSearchQuery] = useState('')
    const [searchResults, setSearchResults] = useState<StockOption[]>([])
    const [level, setLevel] = useState<'all' | ActivityLevel>(initialLevel)
    const [overview, setOverview] = useState<PipelineOverview | null>(null)
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const requestRef = useRef<AbortController | null>(null)

    useEffect(() => {
        setSelectedStock(initialStock)
        setLevel(initialLevel)
    }, [initialLevel, initialStock])

    const fetchOverview = useCallback(async () => {
        if (!selectedStock) {
            setOverview(null)
            return
        }
        try {
            const response = await fetch(`${API_URL}/stocks/${selectedStock.id}/overview`)
            const payload = await response.json()
            if (!response.ok) throw new Error(payload.error || 'Could not load stock pipeline')
            setOverview(payload)
        } catch {
            setOverview(null)
        }
    }, [selectedStock])

    const fetchActivity = useCallback(async (background = false) => {
        requestRef.current?.abort()
        const controller = new AbortController()
        requestRef.current = controller
        if (background) setRefreshing(true)
        else setLoading(true)

        try {
            const params = new URLSearchParams({ limit: '200' })
            if (selectedStock) params.set('stock_id', String(selectedStock.id))
            if (level !== 'all') params.set('level', level)
            const response = await fetch(`${API_URL}/activity?${params}`, {
                signal: controller.signal,
            })
            const payload = await response.json()
            if (!response.ok) {
                throw new Error(payload.error || `Activity request failed (${response.status})`)
            }
            setEvents(Array.isArray(payload.events) ? payload.events : [])
            setError(null)
        } catch (activityError) {
            if ((activityError as Error).name === 'AbortError') return
            setError(activityError instanceof Error ? activityError.message : 'Could not load activity')
        } finally {
            if (!controller.signal.aborted) {
                setLoading(false)
                setRefreshing(false)
            }
        }
    }, [level, selectedStock])

    useEffect(() => {
        void fetchActivity()
        void fetchOverview()
        const timer = window.setInterval(() => void fetchActivity(true), 15000)
        return () => {
            window.clearInterval(timer)
            requestRef.current?.abort()
        }
    }, [fetchActivity, fetchOverview])

    useEffect(() => {
        const query = searchQuery.trim()
        if (query.length < 1) {
            setSearchResults([])
            return
        }

        const controller = new AbortController()
        const timer = window.setTimeout(async () => {
            try {
                const response = await fetch(`${API_URL}/stocks?q=${encodeURIComponent(query)}`, {
                    signal: controller.signal,
                })
                if (!response.ok) throw new Error(`Stock search failed (${response.status})`)
                const payload = await response.json()
                setSearchResults(Array.isArray(payload) ? payload : [])
            } catch (searchError) {
                if ((searchError as Error).name !== 'AbortError') {
                    setSearchResults([])
                }
            }
        }, 250)

        return () => {
            window.clearTimeout(timer)
            controller.abort()
        }
    }, [searchQuery])

    const counts = useMemo(() => ({
        all: events.length,
        success: events.filter((event) => event.level === 'success').length,
        error: events.filter((event) => event.level === 'error').length,
        info: events.filter((event) => event.level === 'info').length,
    }), [events])

    const chooseStock = (stock: StockOption) => {
        setSelectedStock(stock)
        setSearchQuery('')
        setSearchResults([])
    }

    return (
        <main className="mx-auto w-full max-w-6xl px-6 py-8 text-left">
            <div className="mb-8 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
                <div>
                    <div className="mb-2 flex items-center gap-2 text-sm font-medium text-muted-foreground">
                        <ActivityIcon className="h-4 w-4" />
                        Stock pipeline history
                    </div>
                    <h2 className="text-3xl font-bold tracking-tight">
                        {selectedStock ? `${selectedStock.symbol} activity` : 'Recent activity'}
                    </h2>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Transcript checks, analysis jobs, and automatic email delivery in one timeline.
                    </p>
                </div>
                <Button
                    variant="outline"
                    onClick={() => void fetchActivity(true)}
                    disabled={refreshing}
                    className="gap-2"
                >
                    <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
                    Refresh
                </Button>
            </div>

            <section className="mb-6 rounded-xl border bg-card p-4 shadow-sm">
                <div className="relative">
                    <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                    <Input
                        value={searchQuery}
                        onChange={(event) => setSearchQuery(event.target.value)}
                        placeholder="Search a stock by symbol or company name"
                        className="h-10 pl-9 pr-10"
                    />
                    {(searchQuery || selectedStock) && (
                        <button
                            type="button"
                            onClick={() => {
                                setSearchQuery('')
                                setSearchResults([])
                                setSelectedStock(null)
                                setOverview(null)
                            }}
                            className="absolute right-3 top-3 text-muted-foreground hover:text-foreground"
                            aria-label="Clear stock filter"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    )}
                    {searchResults.length > 0 && (
                        <div className="absolute z-20 mt-2 max-h-72 w-full overflow-auto rounded-lg border bg-popover p-1 shadow-xl">
                            {searchResults.map((stock) => (
                                <button
                                    key={stock.id}
                                    type="button"
                                    onClick={() => chooseStock(stock)}
                                    className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left hover:bg-accent"
                                >
                                    <span className="font-semibold">{stock.symbol}</span>
                                    <span className="ml-4 truncate text-sm text-muted-foreground">{stock.name}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {selectedStock && (
                    <div className="mt-3 flex items-center gap-2 text-sm">
                        <span className="rounded-full border bg-secondary px-3 py-1 font-semibold">
                            {selectedStock.symbol}
                        </span>
                        <span className="truncate text-muted-foreground">{selectedStock.name}</span>
                    </div>
                )}
            </section>

            <div className="mb-6 flex flex-wrap gap-2">
                {(['all', 'success', 'error', 'info'] as const).map((filter) => (
                    <button
                        key={filter}
                        type="button"
                        onClick={() => setLevel(filter)}
                        className={`rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
                            level === filter
                                ? 'border-foreground bg-foreground text-background'
                                : 'bg-card text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        {filter === 'all' ? 'All' : filter[0].toUpperCase() + filter.slice(1)}
                        <span className="ml-2 opacity-70">{counts[filter]}</span>
                    </button>
                ))}
            </div>

            {error && (
                <div className="mb-5 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-600 dark:text-red-400">
                    {error}
                </div>
            )}

            <div className={selectedStock ? 'grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]' : ''}>
                {selectedStock && (
                    <aside className="space-y-4 lg:sticky lg:top-5 lg:self-start">
                        <section className="rounded-xl border bg-card p-4 shadow-sm">
                            <div className="mb-3 flex items-start justify-between gap-3">
                                <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Current pipeline</p>
                                    <h3 className="mt-1 text-lg font-bold">{selectedStock.symbol}</h3>
                                </div>
                                {onOpenStock && (
                                    <Button size="sm" variant="outline" onClick={() => onOpenStock(selectedStock)} className="gap-1.5">
                                        Full profile
                                        <ExternalLink className="h-3.5 w-3.5" />
                                    </Button>
                                )}
                            </div>

                            {!overview ? (
                                <div className="flex items-center py-6 text-sm text-muted-foreground">
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Loading pipeline details…
                                </div>
                            ) : (
                                <div className="space-y-3 text-sm">
                                    <div className="rounded-lg border bg-secondary/30 p-3">
                                        <p className="text-xs font-semibold uppercase text-muted-foreground">Transcript</p>
                                        <p className="mt-1 font-medium capitalize">
                                            {overview.pipeline.latest_transcript?.status || overview.pipeline.fetch_schedule?.last_status || 'Waiting'}
                                        </p>
                                        <p className="mt-2 text-xs text-muted-foreground">
                                            Last checked: {formatTimestamp(overview.pipeline.fetch_schedule?.last_checked_at)}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Next check: {formatTimestamp(overview.pipeline.fetch_schedule?.next_check_at)}
                                        </p>
                                    </div>

                                    <div className="rounded-lg border bg-secondary/30 p-3">
                                        <p className="text-xs font-semibold uppercase text-muted-foreground">Analysis</p>
                                        <p className="mt-1 font-medium capitalize">
                                            {overview.pipeline.latest_analysis_job?.status || overview.pipeline.latest_transcript?.analysis_status || 'Not started'}
                                        </p>
                                        <p className="mt-2 text-xs text-muted-foreground">
                                            Attempts: {overview.pipeline.latest_analysis_job?.attempts ?? 0}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Retry: {formatTimestamp(overview.pipeline.latest_analysis_job?.retry_next_at)}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Worker lease: {formatTimestamp(overview.pipeline.latest_analysis_job?.locked_until)}
                                        </p>
                                        {(overview.pipeline.latest_analysis_job?.error_message || overview.pipeline.latest_transcript?.analysis_error) && (
                                            <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 p-2.5 text-xs leading-5 text-red-600 dark:text-red-300">
                                                <p className="mb-1 font-semibold">Detailed error</p>
                                                <p className="whitespace-pre-wrap break-words">
                                                    {overview.pipeline.latest_analysis_job?.error_message || overview.pipeline.latest_transcript?.analysis_error}
                                                </p>
                                            </div>
                                        )}
                                    </div>

                                    <div className="rounded-lg border bg-secondary/30 p-3">
                                        <p className="text-xs font-semibold uppercase text-muted-foreground">Next runs</p>
                                        <div className="mt-2 flex items-start gap-2 text-xs text-muted-foreground">
                                            <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                            <div>
                                                <p>Transcript: {formatTimestamp(overview.pipeline.fetch_schedule?.next_check_at)}</p>
                                                <p>Analysis retry: {formatTimestamp(overview.pipeline.latest_analysis_job?.retry_next_at)}</p>
                                                <p>Email retry: {formatTimestamp(overview.pipeline.latest_email?.retry_next_at)}</p>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-2 gap-2 text-center text-xs">
                                        <div className="rounded-lg border p-2">
                                            <p className="text-lg font-bold">{overview.counts.analyses}</p>
                                            <p className="text-muted-foreground">Analyses</p>
                                        </div>
                                        <div className="rounded-lg border p-2">
                                            <p className="text-lg font-bold">{overview.counts.groups}</p>
                                            <p className="text-muted-foreground">Groups</p>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </section>
                    </aside>
                )}

                <div>
                    {loading ? (
                        <div className="flex min-h-64 items-center justify-center text-muted-foreground">
                            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                            Loading activity…
                        </div>
                    ) : events.length === 0 ? (
                        <div className="rounded-xl border border-dashed p-12 text-center">
                            <ActivityIcon className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
                            <h3 className="font-semibold">No log entries found</h3>
                            <p className="mt-1 text-sm text-muted-foreground">
                                Try another stock or status filter.
                            </p>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {events.map((event) => (
                                <article key={event.id} className="rounded-xl border bg-card p-4 shadow-sm">
                                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                        <div className="min-w-0">
                                            <div className="mb-2 flex flex-wrap items-center gap-2">
                                                <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${LEVEL_STYLES[event.level]}`}>
                                                    <LevelIcon level={event.level} />
                                                    {event.level.toUpperCase()}
                                                </span>
                                                <span className="rounded-full border bg-secondary px-2.5 py-1 text-xs font-medium">
                                                    {STAGE_LABELS[event.stage] || event.stage}
                                                </span>
                                                <span className="text-sm font-semibold">{event.symbol}</span>
                                                {event.quarter && event.year && (
                                                    <span className="text-xs text-muted-foreground">
                                                        {event.quarter} FY{String(event.year).slice(-2)}
                                                    </span>
                                                )}
                                            </div>
                                            <p className="whitespace-pre-wrap break-words text-sm leading-6">{event.message}</p>
                                            {!selectedStock && (
                                                <p className="mt-1 truncate text-xs text-muted-foreground">
                                                    {event.stock_name}
                                                </p>
                                            )}
                                            {Boolean(event.details?.retry_next_at) && (
                                                <p className="mt-2 text-xs font-medium text-amber-500">
                                                    Retry scheduled: {formatTimestamp(String(event.details?.retry_next_at))}
                                                </p>
                                            )}
                                        </div>
                                        <time className="shrink-0 text-xs text-muted-foreground">
                                            {formatTimestamp(event.event_at)}
                                        </time>
                                    </div>
                                </article>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </main>
    )
}
