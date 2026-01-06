import { useState, useEffect, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Plus, Search, Download, FileText, Loader2, X, Eye } from 'lucide-react'
import { cn } from '@/lib/utils'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5001/api'

type AvailableDocument = {
    year: number
    type: string
    url: string
    label: string
}

type ResearchRun = {
    id: number
    stock_id: number
    stock_symbol: string
    stock_name: string
    document_years: number[]
    status: 'pending' | 'in_progress' | 'done' | 'error'
    model_provider?: string
    model_id?: string
    error_message?: string
    created_at: string
    updated_at: string
    llm_output?: string
    rendered_html?: string
}

type Stock = {
    id?: number
    symbol: string
    name: string
}

const DEFAULT_PROMPT = `You are a financial analyst reviewing annual reports.

Analyze the provided annual report(s) and create a comprehensive research summary covering:

1. **Business Overview**: Key business segments, revenue breakdown, and market position
2. **Financial Performance**: Revenue growth, profitability trends, key ratios
3. **Management Commentary**: Key insights from management discussion
4. **Risk Factors**: Major risks and challenges identified
5. **Future Outlook**: Growth drivers, expansion plans, guidance if any
6. **Key Metrics**: Important KPIs and how they've trended

Be specific with numbers and percentages. Compare year-over-year where multiple years are provided.`

export default function Research() {
    // Left sidebar - past runs
    const [runs, setRuns] = useState<ResearchRun[]>([])
    const [runsLoading, setRunsLoading] = useState(true)
    const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
    const [selectedRunContent, setSelectedRunContent] = useState<string | null>(null)
    const [contentLoading, setContentLoading] = useState(false)

    // Right panel - new research
    const [stockSearchQuery, setStockSearchQuery] = useState('')
    const [searchResults, setSearchResults] = useState<Stock[]>([])
    const [isSearching, setIsSearching] = useState(false)
    const [selectedStock, setSelectedStock] = useState<Stock | null>(null)
    const [availableDocs, setAvailableDocs] = useState<AvailableDocument[]>([])
    const [docsLoading, setDocsLoading] = useState(false)
    const [selectedYears, setSelectedYears] = useState<Set<number>>(new Set())
    const [researchPrompt, setResearchPrompt] = useState(DEFAULT_PROMPT)
    const [isStarting, setIsStarting] = useState(false)
    const [downloadingId, setDownloadingId] = useState<number | null>(null)

    const searchContainerRef = useRef<HTMLDivElement>(null)

    // Fetch runs on mount and periodically
    useEffect(() => {
        fetchRuns()
        const interval = setInterval(fetchRuns, 10000) // Poll every 10 seconds
        return () => clearInterval(interval)
    }, [])

    // Search stocks with debounce
    useEffect(() => {
        const delayDebounceFn = setTimeout(() => {
            if (stockSearchQuery) {
                searchStocks(stockSearchQuery)
            } else {
                setSearchResults([])
            }
        }, 300)
        return () => clearTimeout(delayDebounceFn)
    }, [stockSearchQuery])

    // Close search dropdown when clicking outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (searchContainerRef.current && !searchContainerRef.current.contains(event.target as Node)) {
                setSearchResults([])
                setStockSearchQuery('')
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const fetchRuns = async () => {
        try {
            const response = await fetch(`${API_URL}/research/runs`)
            if (response.ok) {
                const data = await response.json()
                setRuns(data)
            }
        } catch (error) {
            console.error('Error fetching runs:', error)
        } finally {
            setRunsLoading(false)
        }
    }

    const searchStocks = async (query: string) => {
        setIsSearching(true)
        try {
            const response = await fetch(`${API_URL}/stocks?q=${encodeURIComponent(query)}`)
            if (response.ok) {
                const data = await response.json()
                setSearchResults(data)
            }
        } catch (error) {
            console.error('Error searching stocks:', error)
        } finally {
            setIsSearching(false)
        }
    }

    const selectStock = async (stock: Stock) => {
        setSelectedStock(stock)
        setSearchResults([])
        setStockSearchQuery('')
        setSelectedYears(new Set())
        setAvailableDocs([])
        setDocsLoading(true)

        try {
            const response = await fetch(`${API_URL}/research/documents/${stock.symbol}`)
            if (response.ok) {
                const data = await response.json()
                setAvailableDocs(data.documents || [])
                // Pre-select latest 2 years
                const years = (data.documents || []).slice(0, 2).map((d: AvailableDocument) => d.year)
                setSelectedYears(new Set(years))
            }
        } catch (error) {
            console.error('Error fetching documents:', error)
        } finally {
            setDocsLoading(false)
        }
    }

    const toggleYear = (year: number) => {
        const newSelected = new Set(selectedYears)
        if (newSelected.has(year)) {
            newSelected.delete(year)
        } else {
            newSelected.add(year)
        }
        setSelectedYears(newSelected)
    }

    const startResearch = async () => {
        if (!selectedStock?.id || selectedYears.size === 0) return

        setIsStarting(true)
        try {
            const response = await fetch(`${API_URL}/research/runs`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    stock_id: selectedStock.id,
                    document_years: Array.from(selectedYears),
                    prompt: researchPrompt
                })
            })

            if (response.ok) {
                // Reset form
                setSelectedStock(null)
                setAvailableDocs([])
                setSelectedYears(new Set())
                // Refresh runs list
                fetchRuns()
            } else {
                const error = await response.json()
                alert(error.error || 'Failed to start research')
            }
        } catch (error) {
            console.error('Error starting research:', error)
        } finally {
            setIsStarting(false)
        }
    }

    const viewRunContent = async (runId: number) => {
        if (selectedRunId === runId) {
            setSelectedRunId(null)
            setSelectedRunContent(null)
            return
        }

        setSelectedRunId(runId)
        setContentLoading(true)
        try {
            const response = await fetch(`${API_URL}/research/runs/${runId}`)
            if (response.ok) {
                const data = await response.json()
                setSelectedRunContent(data.rendered_html || data.llm_output || '')
            }
        } catch (error) {
            console.error('Error fetching run content:', error)
        } finally {
            setContentLoading(false)
        }
    }

    const downloadPdf = async (runId: number, e: React.MouseEvent) => {
        e.stopPropagation()
        setDownloadingId(runId)
        try {
            const response = await fetch(`${API_URL}/research/runs/${runId}/download`)
            if (!response.ok) {
                alert('Failed to download PDF')
                return
            }

            const blob = await response.blob()
            const disposition = response.headers.get('Content-Disposition') || ''
            const match = disposition.match(/filename=\"?([^\"]+)\"?/)
            const fileName = match?.[1] || 'research-report.pdf'

            const url = window.URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.download = fileName
            document.body.appendChild(link)
            link.click()
            link.remove()
            window.URL.revokeObjectURL(url)
        } catch (error) {
            console.error('Error downloading PDF:', error)
        } finally {
            setDownloadingId(null)
        }
    }

    const getStatusBadge = (run: ResearchRun) => {
        switch (run.status) {
            case 'done':
                return (
                    <Badge className="bg-green-500/20 text-green-400 border-green-500/50">
                        ✓ Done
                    </Badge>
                )
            case 'in_progress':
                return (
                    <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/50">
                        <Loader2 className="h-3 w-3 animate-spin mr-1" />
                        Processing...
                    </Badge>
                )
            case 'pending':
                return (
                    <Badge className="bg-gray-500/20 text-gray-400 border-gray-500/50">
                        ⏳ Pending
                    </Badge>
                )
            case 'error':
                return (
                    <Badge className="bg-red-500/20 text-red-400 border-red-500/50">
                        ✕ Error
                    </Badge>
                )
            default:
                return <Badge variant="outline">Unknown</Badge>
        }
    }

    return (
        <div className="flex h-[calc(100vh-8rem)] max-w-7xl mx-auto p-6 gap-6">
            {/* Left Sidebar - Past Runs */}
            <div className="w-1/3 bg-card border border-border rounded-lg flex flex-col overflow-hidden">
                <div className="p-4 border-b border-border bg-muted/30">
                    <h2 className="font-semibold text-lg">Research History</h2>
                </div>

                <div className="flex-1 overflow-y-auto p-2 space-y-2">
                    {runsLoading ? (
                        <div className="flex justify-center p-4">
                            <Loader2 className="animate-spin h-5 w-5 text-muted-foreground" />
                        </div>
                    ) : runs.length === 0 ? (
                        <div className="text-center p-4 text-muted-foreground text-sm">
                            No research runs yet. Start a new one!
                        </div>
                    ) : (
                        runs.map(run => (
                            <div key={run.id}>
                                <div
                                    onClick={() => run.status === 'done' && viewRunContent(run.id)}
                                    className={cn(
                                        "p-3 rounded-md border transition-colors",
                                        selectedRunId === run.id
                                            ? "bg-primary/10 border-primary/30"
                                            : "bg-muted/20 border-border hover:bg-accent/50",
                                        run.status === 'done' && "cursor-pointer"
                                    )}
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="flex-1 min-w-0">
                                            <div className="font-medium truncate">{run.stock_symbol}</div>
                                            <div className="text-xs text-muted-foreground truncate">
                                                {run.stock_name}
                                            </div>
                                            <div className="text-xs text-muted-foreground mt-1">
                                                Years: {run.document_years.sort((a, b) => b - a).join(', ')}
                                            </div>
                                            <div className="text-xs text-muted-foreground">
                                                {new Date(run.created_at).toLocaleDateString('en-IN', {
                                                    month: 'short',
                                                    day: 'numeric',
                                                    year: 'numeric',
                                                    hour: '2-digit',
                                                    minute: '2-digit'
                                                })}
                                            </div>
                                            {run.error_message && (
                                                <div className="text-xs text-red-400 mt-1 truncate">
                                                    {run.error_message}
                                                </div>
                                            )}
                                        </div>
                                        <div className="flex flex-col items-end gap-2">
                                            {getStatusBadge(run)}
                                            {run.status === 'done' && (
                                                <div className="flex gap-1">
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        className="h-7 w-7"
                                                        onClick={(e) => { e.stopPropagation(); viewRunContent(run.id) }}
                                                    >
                                                        <Eye className="h-3.5 w-3.5" />
                                                    </Button>
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        className="h-7 w-7"
                                                        onClick={(e) => downloadPdf(run.id, e)}
                                                        disabled={downloadingId === run.id}
                                                    >
                                                        {downloadingId === run.id ? (
                                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                        ) : (
                                                            <Download className="h-3.5 w-3.5" />
                                                        )}
                                                    </Button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </div>

                                {/* Expanded content */}
                                {selectedRunId === run.id && selectedRunContent && (
                                    <div className="mt-2 p-3 rounded-md bg-background border border-border max-h-96 overflow-auto">
                                        {contentLoading ? (
                                            <div className="flex items-center justify-center p-4">
                                                <Loader2 className="animate-spin h-4 w-4 mr-2" />
                                                Loading...
                                            </div>
                                        ) : (
                                            <div
                                                className="prose prose-sm dark:prose-invert max-w-none"
                                                dangerouslySetInnerHTML={{ __html: selectedRunContent }}
                                            />
                                        )}
                                    </div>
                                )}
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Right Panel - New Research */}
            <div className="flex-1 bg-card border border-border rounded-lg flex flex-col overflow-hidden">
                <div className="p-6 border-b border-border bg-muted/10">
                    <h1 className="text-2xl font-bold">New Research</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Select a stock and choose annual reports to analyze
                    </p>
                </div>

                <div className="flex-1 overflow-y-auto p-6 space-y-6">
                    {/* Stock Search */}
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Stock</label>
                        {selectedStock ? (
                            <div className="flex items-center gap-2 p-3 bg-primary/10 border border-primary/30 rounded-md">
                                <div className="flex-1">
                                    <div className="font-medium">{selectedStock.symbol}</div>
                                    <div className="text-sm text-muted-foreground">{selectedStock.name}</div>
                                </div>
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-8 w-8"
                                    onClick={() => {
                                        setSelectedStock(null)
                                        setAvailableDocs([])
                                        setSelectedYears(new Set())
                                    }}
                                >
                                    <X className="h-4 w-4" />
                                </Button>
                            </div>
                        ) : (
                            <div className="relative" ref={searchContainerRef}>
                                <div className="relative">
                                    <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                                    <Input
                                        placeholder="Search stocks..."
                                        value={stockSearchQuery}
                                        onChange={(e) => setStockSearchQuery(e.target.value)}
                                        className="pl-9"
                                    />
                                </div>
                                {(searchResults.length > 0 || isSearching) && stockSearchQuery && (
                                    <div className="absolute top-full left-0 right-0 mt-2 bg-popover border border-border rounded-md shadow-lg overflow-hidden z-50 max-h-60 overflow-y-auto">
                                        {isSearching ? (
                                            <div className="p-3 text-center text-sm text-muted-foreground flex items-center justify-center gap-2">
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                                Searching...
                                            </div>
                                        ) : (
                                            searchResults.map(stock => (
                                                <div
                                                    key={stock.symbol}
                                                    className="flex items-center justify-between p-3 hover:bg-accent cursor-pointer"
                                                    onClick={() => selectStock(stock)}
                                                >
                                                    <div>
                                                        <div className="font-medium">{stock.symbol}</div>
                                                        <div className="text-sm text-muted-foreground">{stock.name}</div>
                                                    </div>
                                                    <Plus className="h-4 w-4 text-muted-foreground" />
                                                </div>
                                            ))
                                        )}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Available Documents */}
                    {selectedStock && (
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Available Annual Reports</label>
                            {docsLoading ? (
                                <div className="flex items-center gap-2 p-4 text-muted-foreground">
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Fetching available documents...
                                </div>
                            ) : availableDocs.length === 0 ? (
                                <div className="p-4 text-muted-foreground text-sm bg-muted/20 rounded-md">
                                    No annual reports found for this stock on screener.in
                                </div>
                            ) : (
                                <div className="flex flex-wrap gap-2">
                                    {availableDocs.map(doc => (
                                        <button
                                            key={doc.year}
                                            onClick={() => toggleYear(doc.year)}
                                            className={cn(
                                                "px-4 py-2 rounded-md border text-sm font-medium transition-all",
                                                selectedYears.has(doc.year)
                                                    ? "bg-primary text-primary-foreground border-primary"
                                                    : "bg-muted/20 border-border hover:bg-accent text-muted-foreground hover:text-foreground"
                                            )}
                                        >
                                            FY {doc.year}
                                        </button>
                                    ))}
                                </div>
                            )}
                            {selectedYears.size > 0 && (
                                <div className="text-xs text-muted-foreground">
                                    Selected: {Array.from(selectedYears).sort((a, b) => b - a).join(', ')}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Research Prompt */}
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Research Prompt</label>
                        <textarea
                            className="w-full min-h-[200px] p-3 rounded-md border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
                            value={researchPrompt}
                            onChange={(e) => setResearchPrompt(e.target.value)}
                            placeholder="Enter your research prompt..."
                        />
                        <div className="text-xs text-muted-foreground">
                            Customize the prompt to focus on specific aspects like growth, risks, or competitive analysis.
                        </div>
                    </div>

                    {/* Start Button */}
                    <Button
                        className="w-full"
                        size="lg"
                        disabled={!selectedStock || selectedYears.size === 0 || isStarting}
                        onClick={startResearch}
                    >
                        {isStarting ? (
                            <>
                                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                Starting Research...
                            </>
                        ) : (
                            <>
                                <FileText className="h-4 w-4 mr-2" />
                                Start Research
                            </>
                        )}
                    </Button>
                </div>
            </div>
        </div>
    )
}
