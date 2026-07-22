import { useCallback, useEffect, useMemo, useState } from 'react'
import {
    Activity,
    ArrowLeft,
    BookOpen,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    CircleAlert,
    Clock3,
    FileText,
    Layers3,
    Loader2,
    RefreshCw,
    Sparkles,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

const API_URL = 'http://127.0.0.1:5001/api'

type StockDetailProps = {
    stockId: number
    onBack: () => void
    onOpenActivity: (level?: 'all' | 'info' | 'success' | 'error') => void
}

type Overview = {
    stock: {
        id: number
        symbol: string
        stock_symbol?: string | null
        bse_code?: string | null
        isin_number: string
        name: string
        source?: string
        is_active: boolean
        in_watchlist: boolean
        watchlist_added_at?: string | null
    }
    counts: {
        groups: number
        active_groups: number
        transcripts: number
        analyses: number
        deep_research_runs: number
        deep_research_done: number
        document_research_runs: number
        document_research_done: number
        activity_errors: number
    }
    groups: Array<{
        id: number
        name: string
        is_active: boolean
        added_at?: string | null
        deep_research_count: number
        deep_research_done_count: number
        latest_research_at?: string | null
    }>
    transcripts: Array<{
        id: number
        quarter: string
        year: number
        status: string
        event_date?: string | null
        source_url?: string | null
        analysis_status?: string | null
        analysis_error?: string | null
        analysis_count: number
        updated_at?: string | null
    }>
    analyses: Array<{
        id: number
        transcript_id: number
        llm_output?: string | null
        created_at?: string | null
        model_provider?: string | null
        model_name?: string | null
        tokens_used_input?: number | null
        tokens_used_output?: number | null
        cost_usd?: number | null
        quarter: string
        year: number
    }>
    analysis_jobs: Array<{
        id: number
        status: string
        attempts: number
        retry_next_at?: string | null
        locked_until?: string | null
        updated_at?: string | null
        quarter: string
        year: number
        error_message?: string | null
    }>
    group_research: Array<{
        id: number
        group_id: number
        group_name: string
        group_is_active: boolean
        quarter: string
        year: number
        status: string
        model_provider?: string | null
        model_id?: string | null
        error_message?: string | null
        llm_output?: string | null
        updated_at?: string | null
    }>
    document_research: Array<{
        id: number
        document_years: string
        document_type: string
        status: string
        model_provider?: string | null
        model_id?: string | null
        error_message?: string | null
        llm_output?: string | null
        updated_at?: string | null
    }>
    pipeline: {
        fetch_schedule?: {
            last_status?: string | null
            last_checked_at?: string | null
            next_check_at?: string | null
            attempts?: number
            locked_until?: string | null
        } | null
        latest_transcript?: Overview['transcripts'][number] | null
        latest_analysis_job?: Overview['analysis_jobs'][number] | null
        latest_email?: {
            status?: string | null
            attempts?: number
            retry_next_at?: string | null
            updated_at?: string | null
        } | null
        latest_group_research?: Overview['group_research'][number] | null
        latest_document_research?: Overview['document_research'][number] | null
    }
}

const formatTimestamp = (value?: string | null) => {
    if (!value) return 'Not scheduled'
    const parsed = new Date(value)
    if (Number.isNaN(parsed.getTime())) return value
    return parsed.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

const statusClass = (status?: string | null) => {
    const normalized = (status || '').toLowerCase()
    if (['done', 'available', 'success', 'sent'].includes(normalized)) {
        return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
    }
    if (['error', 'failed'].includes(normalized)) {
        return 'border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400'
    }
    if (['retrying', 'pending', 'queued', 'in_progress', 'preparing', 'generating'].includes(normalized)) {
        return 'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400'
    }
    return 'border-border bg-secondary text-muted-foreground'
}

const StatusBadge = ({ status }: { status?: string | null }) => (
    <Badge className={`capitalize ${statusClass(status)}`}>{(status || 'Not started').replaceAll('_', ' ')}</Badge>
)

export default function StockDetail({ stockId, onBack, onOpenActivity }: StockDetailProps) {
    const [overview, setOverview] = useState<Overview | null>(null)
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [expandedAnalysisId, setExpandedAnalysisId] = useState<number | null>(null)

    const loadOverview = useCallback(async (background = false, signal?: AbortSignal) => {
        if (background) setRefreshing(true)
        else setLoading(true)
        try {
            const response = await fetch(`${API_URL}/stocks/${stockId}/overview`, { signal })
            const payload = await response.json()
            if (!response.ok) throw new Error(payload.error || 'Could not load stock profile')
            setOverview(payload)
            setError(null)
        } catch (loadError) {
            if ((loadError as Error).name === 'AbortError') return
            setError(loadError instanceof Error ? loadError.message : 'Could not load stock profile')
        } finally {
            if (!signal?.aborted) {
                setLoading(false)
                setRefreshing(false)
            }
        }
    }, [stockId])

    useEffect(() => {
        const controller = new AbortController()
        void loadOverview(false, controller.signal)
        return () => controller.abort()
    }, [loadOverview])

    const latestError = useMemo(() => (
        overview?.pipeline.latest_analysis_job?.error_message
        || overview?.pipeline.latest_transcript?.analysis_error
        || overview?.pipeline.latest_group_research?.error_message
        || overview?.pipeline.latest_document_research?.error_message
        || null
    ), [overview])

    if (loading) {
        return (
            <main className="flex min-h-[60vh] items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Loading stock profile…
            </main>
        )
    }

    if (!overview || error) {
        return (
            <main className="mx-auto max-w-4xl px-6 py-10">
                <Button variant="ghost" onClick={onBack} className="mb-6 gap-2"><ArrowLeft className="h-4 w-4" />Back</Button>
                <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-6 text-red-600 dark:text-red-400">
                    {error || 'Stock profile is unavailable.'}
                </div>
            </main>
        )
    }

    const { stock, counts, pipeline } = overview

    return (
        <main className="mx-auto w-full max-w-7xl px-6 py-8 text-left">
            <div className="mb-7 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <Button variant="ghost" onClick={onBack} className="mb-3 -ml-3 gap-2">
                        <ArrowLeft className="h-4 w-4" />Watchlist
                    </Button>
                    <div className="flex flex-wrap items-center gap-3">
                        <h2 className="text-3xl font-bold tracking-tight">{stock.symbol}</h2>
                        {stock.in_watchlist && <Badge className="border-blue-500/30 bg-blue-500/10 text-blue-500">Watchlist</Badge>}
                        <Badge variant="outline">{stock.is_active ? 'Active stock' : 'Inactive stock'}</Badge>
                    </div>
                    <p className="mt-2 text-lg text-muted-foreground">{stock.name}</p>
                    <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
                        <span>ISIN: {stock.isin_number}</span>
                        {stock.stock_symbol && <span>NSE: {stock.stock_symbol}</span>}
                        {stock.bse_code && <span>BSE: {stock.bse_code}</span>}
                        {stock.watchlist_added_at && <span>Watching since: {formatTimestamp(stock.watchlist_added_at)}</span>}
                    </div>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={() => void loadOverview(true)} disabled={refreshing} className="gap-2">
                        <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />Refresh
                    </Button>
                    <Button onClick={() => onOpenActivity(latestError ? 'error' : 'all')} className="gap-2">
                        <Activity className="h-4 w-4" />View activity
                    </Button>
                </div>
            </div>

            <section className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                {[
                    { label: 'Groups', value: counts.groups, Icon: Layers3 },
                    { label: 'Analyses', value: counts.analyses, Icon: Sparkles },
                    { label: 'Transcripts', value: counts.transcripts, Icon: FileText },
                    { label: 'Deep research', value: `${counts.deep_research_done}/${counts.deep_research_runs}`, Icon: BookOpen },
                    { label: 'Errors logged', value: counts.activity_errors, Icon: CircleAlert },
                ].map(({ label, value, Icon }) => (
                    <div key={label} className="rounded-xl border bg-card p-4 shadow-sm">
                        <Icon className="mb-3 h-5 w-5 text-muted-foreground" />
                        <p className="text-2xl font-bold">{String(value)}</p>
                        <p className="text-sm text-muted-foreground">{label}</p>
                    </div>
                ))}
            </section>

            <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
                <div className="space-y-6">
                    <section className="rounded-xl border bg-card p-5 shadow-sm">
                        <h3 className="mb-4 flex items-center gap-2 font-semibold"><Clock3 className="h-4 w-4" />Current pipeline</h3>
                        <div className="space-y-4 text-sm">
                            <div className="border-l-2 border-blue-500 pl-3">
                                <div className="flex items-center justify-between gap-2"><span className="font-medium">Transcript</span><StatusBadge status={pipeline.latest_transcript?.status || pipeline.fetch_schedule?.last_status} /></div>
                                <p className="mt-2 text-xs text-muted-foreground">Last check: {formatTimestamp(pipeline.fetch_schedule?.last_checked_at)}</p>
                                <p className="text-xs text-muted-foreground">Next check: {formatTimestamp(pipeline.fetch_schedule?.next_check_at)}</p>
                            </div>
                            <div className="border-l-2 border-purple-500 pl-3">
                                <div className="flex items-center justify-between gap-2"><span className="font-medium">Analysis</span><StatusBadge status={pipeline.latest_analysis_job?.status || pipeline.latest_transcript?.analysis_status} /></div>
                                <p className="mt-2 text-xs text-muted-foreground">Attempts: {pipeline.latest_analysis_job?.attempts ?? 0}</p>
                                <p className="text-xs text-muted-foreground">Retry: {formatTimestamp(pipeline.latest_analysis_job?.retry_next_at)}</p>
                            </div>
                            <div className="border-l-2 border-emerald-500 pl-3">
                                <div className="flex items-center justify-between gap-2"><span className="font-medium">Email</span><StatusBadge status={pipeline.latest_email?.status} /></div>
                                <p className="mt-2 text-xs text-muted-foreground">Updated: {formatTimestamp(pipeline.latest_email?.updated_at)}</p>
                            </div>
                        </div>
                        {latestError && (
                            <button type="button" onClick={() => onOpenActivity('error')} className="mt-5 w-full rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-left text-xs leading-5 text-red-600 hover:bg-red-500/15 dark:text-red-300">
                                <span className="mb-1 block font-semibold">Latest detailed error</span>
                                <span className="line-clamp-6 whitespace-pre-wrap break-words">{latestError}</span>
                                <span className="mt-2 block font-semibold underline">Open error activity</span>
                            </button>
                        )}
                    </section>

                    <section className="rounded-xl border bg-card p-5 shadow-sm">
                        <h3 className="mb-4 flex items-center gap-2 font-semibold"><Layers3 className="h-4 w-4" />Group membership</h3>
                        {overview.groups.length === 0 ? (
                            <p className="text-sm text-muted-foreground">This stock is not assigned to any group.</p>
                        ) : (
                            <div className="space-y-3">
                                {overview.groups.map(group => (
                                    <div key={group.id} className="rounded-lg border bg-secondary/20 p-3">
                                        <div className="flex items-center justify-between gap-3">
                                            <p className="font-medium">{group.name}</p>
                                            <StatusBadge status={group.is_active ? 'active' : 'inactive'} />
                                        </div>
                                        <p className="mt-2 text-xs text-muted-foreground">
                                            Deep research completed: {group.deep_research_done_count}/{group.deep_research_count}
                                        </p>
                                        <p className="text-xs text-muted-foreground">Latest: {formatTimestamp(group.latest_research_at)}</p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </section>
                </div>

                <div className="space-y-6">
                    <section className="rounded-xl border bg-card p-5 shadow-sm">
                        <div className="mb-4 flex items-center justify-between">
                            <h3 className="flex items-center gap-2 font-semibold"><Sparkles className="h-4 w-4" />Stock analyses</h3>
                            <span className="text-sm text-muted-foreground">{counts.analyses} total</span>
                        </div>
                        {overview.analyses.length === 0 ? (
                            <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">No completed analysis yet.</p>
                        ) : (
                            <div className="space-y-3">
                                {overview.analyses.map(analysis => {
                                    const expanded = expandedAnalysisId === analysis.id
                                    return (
                                        <article key={analysis.id} className="rounded-lg border">
                                            <button type="button" onClick={() => setExpandedAnalysisId(expanded ? null : analysis.id)} className="flex w-full items-center justify-between gap-4 p-4 text-left hover:bg-accent/40">
                                                <div>
                                                    <p className="font-medium">{analysis.quarter} FY{String(analysis.year).slice(-2)} analysis</p>
                                                    <p className="mt-1 text-xs text-muted-foreground">{analysis.model_provider || 'Unknown provider'} · {analysis.model_name || 'Unknown model'} · {formatTimestamp(analysis.created_at)}</p>
                                                </div>
                                                {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                                            </button>
                                            {expanded && (
                                                <div className="border-t bg-secondary/20 p-4">
                                                    <p className="whitespace-pre-wrap break-words text-sm leading-6">{analysis.llm_output || 'No analysis text stored.'}</p>
                                                </div>
                                            )}
                                        </article>
                                    )
                                })}
                            </div>
                        )}
                    </section>

                    <section className="rounded-xl border bg-card p-5 shadow-sm">
                        <div className="mb-4 flex items-center justify-between">
                            <h3 className="flex items-center gap-2 font-semibold"><FileText className="h-4 w-4" />Transcript history</h3>
                            <span className="text-sm text-muted-foreground">{counts.transcripts} total</span>
                        </div>
                        {overview.transcripts.length === 0 ? (
                            <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">No transcript records yet.</p>
                        ) : (
                            <div className="grid gap-3 md:grid-cols-2">
                                {overview.transcripts.map(transcript => (
                                <div key={transcript.id} className="rounded-lg border bg-secondary/20 p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <p className="font-medium">{transcript.quarter} FY{String(transcript.year).slice(-2)}</p>
                                        <StatusBadge status={transcript.status} />
                                    </div>
                                    <p className="mt-2 text-xs text-muted-foreground">Analyses: {transcript.analysis_count}</p>
                                    <p className="text-xs text-muted-foreground">Updated: {formatTimestamp(transcript.updated_at)}</p>
                                    {transcript.source_url && (
                                        <a href={transcript.source_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs font-medium text-primary hover:underline">
                                            Open transcript source
                                        </a>
                                    )}
                                    {transcript.analysis_error && <p className="mt-2 line-clamp-3 text-xs text-red-500">{transcript.analysis_error}</p>}
                                </div>
                                ))}
                            </div>
                        )}
                    </section>

                    <section className="rounded-xl border bg-card p-5 shadow-sm">
                        <h3 className="mb-4 flex items-center gap-2 font-semibold"><BookOpen className="h-4 w-4" />Deep research</h3>
                        <div className="space-y-5">
                            <div>
                                <p className="mb-2 text-sm font-medium">Group research</p>
                                {overview.group_research.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">No group deep research includes this stock.</p>
                                ) : overview.group_research.map(run => (
                                    <div key={run.id} className="mb-3 rounded-lg border bg-secondary/20 p-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <p className="font-medium">{run.group_name} · {run.quarter} FY{String(run.year).slice(-2)}</p>
                                            <StatusBadge status={run.status} />
                                        </div>
                                        <p className="mt-1 text-xs text-muted-foreground">{run.model_provider || 'No provider'} · {formatTimestamp(run.updated_at)}</p>
                                        {run.error_message && <p className="mt-2 whitespace-pre-wrap text-xs text-red-500">{run.error_message}</p>}
                                        {run.llm_output && (
                                            <details className="mt-3 rounded-md border bg-background/60 p-3">
                                                <summary className="cursor-pointer text-sm font-medium">Show complete research output</summary>
                                                <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6">{run.llm_output}</p>
                                            </details>
                                        )}
                                    </div>
                                ))}
                            </div>
                            <div className="border-t pt-4">
                                <p className="mb-2 text-sm font-medium">Annual report / document research</p>
                                {overview.document_research.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">No document research has been run for this stock.</p>
                                ) : overview.document_research.map(run => (
                                    <div key={run.id} className="mb-3 rounded-lg border bg-secondary/20 p-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <p className="font-medium">{run.document_type.replaceAll('_', ' ')} · {run.document_years}</p>
                                            <StatusBadge status={run.status} />
                                        </div>
                                        <p className="mt-1 text-xs text-muted-foreground">{run.model_provider || 'No provider'} · {formatTimestamp(run.updated_at)}</p>
                                        {run.error_message && <p className="mt-2 whitespace-pre-wrap text-xs text-red-500">{run.error_message}</p>}
                                        {run.llm_output && (
                                            <details className="mt-3 rounded-md border bg-background/60 p-3">
                                                <summary className="cursor-pointer text-sm font-medium">Show complete research output</summary>
                                                <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6">{run.llm_output}</p>
                                            </details>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </section>

                    <section className="rounded-xl border bg-card p-5 shadow-sm">
                        <h3 className="mb-4 flex items-center gap-2 font-semibold"><Activity className="h-4 w-4" />Recent analysis jobs</h3>
                        <div className="space-y-2">
                            {overview.analysis_jobs.slice(0, 8).map(job => (
                                <button key={job.id} type="button" onClick={() => onOpenActivity(job.status === 'failed' || job.status === 'error' || job.status === 'retrying' ? 'error' : 'all')} className="flex w-full items-center justify-between gap-3 rounded-lg border p-3 text-left hover:bg-accent/40">
                                    <div>
                                        <p className="text-sm font-medium">{job.quarter} FY{String(job.year).slice(-2)}</p>
                                        <p className="text-xs text-muted-foreground">Attempt {job.attempts} · {formatTimestamp(job.updated_at)}</p>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <StatusBadge status={job.status} />
                                        {job.status === 'done' ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : job.error_message ? <CircleAlert className="h-4 w-4 text-red-500" /> : null}
                                    </div>
                                </button>
                            ))}
                        </div>
                    </section>
                </div>
            </div>
        </main>
    )
}
