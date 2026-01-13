import { useState, useEffect, useRef } from 'react'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Download, Sparkles, Trash2, Plus, Loader2 } from 'lucide-react'

const API_URL = 'http://localhost:5001/api'

type Stock = {
    id?: number
    symbol: string
    name: string
    added_at: string
    status: 'no_transcript' | 'upcoming' | 'transcript_ready' | 'analyzed' | 'fetching' | 'analyzing' | 'analysis_failed'
    status_message: string
    status_details: {
        quarter?: string
        year?: number
        event_date?: string
        transcript_date?: string
        analyzed_at?: string
        provider?: string
        analysis_error?: string
    } | null
}

type Quarter = {
    quarter: string
    year: number
    label: string
}

export default function Watchlist() {
    const [stocks, setStocks] = useState<Stock[]>([])
    const [searchQuery, setSearchQuery] = useState('')
    const [searchResults, setSearchResults] = useState<Stock[]>([])
    const [isSearching, setIsSearching] = useState(false)
    const [quarters, setQuarters] = useState<Quarter[]>([])
    const [selectedQuarter, setSelectedQuarter] = useState<Quarter | null>(null)
    const [isLoading, setIsLoading] = useState(true)
    const [reanalyzingId, setReanalyzingId] = useState<number | null>(null)
    const [downloadingId, setDownloadingId] = useState<number | null>(null)
    const reanalyzeIntervalRef = useRef<number | null>(null)
    const reanalyzeTimeoutRef = useRef<number | null>(null)
    const searchContainerRef = useRef<HTMLDivElement>(null)
    const pollingActiveRef = useRef(false)

    // Fetch quarters on mount
    useEffect(() => {
        const fetchQuarters = async () => {
            try {
                const response = await fetch(`${API_URL}/quarters`)
                if (response.ok) {
                    const data = await response.json()
                    setQuarters(data)
                    if (data.length > 0) {
                        setSelectedQuarter(data[0]) // Default to first (previous quarter)
                    }
                }
            } catch (error) {
                console.error('Error fetching quarters:', error)
            }
        }
        fetchQuarters()
    }, [])

    // Fetch watchlist when quarter changes
    useEffect(() => {
        if (selectedQuarter) {
            fetchWatchlist(selectedQuarter.quarter, selectedQuarter.year)
        }
    }, [selectedQuarter])

    // Auto-refresh watchlist periodically to reflect backend progress
    useEffect(() => {
        const interval = setInterval(() => {
            if (selectedQuarter) {
                fetchWatchlist(selectedQuarter.quarter, selectedQuarter.year)
            }
        }, 10000) // 10 seconds
        return () => clearInterval(interval)
    }, [selectedQuarter])

    // Refresh as soon as a poll cycle starts to surface fetching state
    useEffect(() => {
        const interval = setInterval(async () => {
            try {
                const response = await fetch(`${API_URL}/poll/status`)
                if (!response.ok) return
                const data = await response.json()
                const isPolling = Boolean(data?.is_polling)
                if (isPolling && !pollingActiveRef.current) {
                    if (selectedQuarter) {
                        fetchWatchlist(selectedQuarter.quarter, selectedQuarter.year)
                    } else {
                        fetchWatchlist()
                    }
                }
                pollingActiveRef.current = isPolling
            } catch (error) {
                // Ignore poll status errors to avoid breaking watchlist updates
            }
        }, 1000)
        return () => clearInterval(interval)
    }, [selectedQuarter])

    // Search stocks when query changes
    useEffect(() => {
        const delayDebounceFn = setTimeout(() => {
            if (searchQuery) {
                searchStocks(searchQuery)
            } else {
                setSearchResults([])
            }
        }, 300)

        return () => clearTimeout(delayDebounceFn)
    }, [searchQuery])

    // Close search dropdown when clicking outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (searchContainerRef.current && !searchContainerRef.current.contains(event.target as Node)) {
                setSearchResults([])
                setSearchQuery('')
            }
        }

        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const fetchWatchlist = async (quarter?: string, year?: number) => {
        try {
            let url = `${API_URL}/watchlist`
            if (quarter && year) {
                url += `?quarter=${quarter}&year=${year}`
            }
            const response = await fetch(url)
            if (response.ok) {
                const data = await response.json()
                setStocks(data)
            }
        } catch (error) {
            console.error('Error fetching watchlist:', error)
        } finally {
            setIsLoading(false)
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

    const addToWatchlist = async (stock: Stock) => {
        try {
            const response = await fetch(`${API_URL}/watchlist`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ symbol: stock.symbol }),
            })

            if (response.ok) {
                // Optimistically show fetching state while backend polls/analyzes
                setStocks(prev => {
                    const exists = prev.some(s => s.symbol === stock.symbol)
                    if (exists) return prev
                    return [
                        ...prev,
                        {
                            ...stock,
                            status: 'fetching',
                            status_message: 'Fetching transcript...',
                            status_details: null,
                            added_at: new Date().toISOString()
                        }
                    ]
                })
                // Fetch after a short delay to pick up new transcript/analysis
                setTimeout(() => {
                    if (selectedQuarter) {
                        fetchWatchlist(selectedQuarter.quarter, selectedQuarter.year)
                    } else {
                        fetchWatchlist()
                    }
                }, 3000)
                // Keep search results visible for adding multiple stocks
            }
        } catch (error) {
            console.error('Error adding to watchlist:', error)
        }
    }

    const handleDeleteStock = async (symbol: string) => {
        try {
            const response = await fetch(`${API_URL}/watchlist/${symbol}`, {
                method: 'DELETE',
            })

            if (response.ok) {
                setStocks(stocks.filter(stock => stock.symbol !== symbol))
            }
        } catch (error) {
            console.error('Error deleting stock:', error)
        }
    }

    const handleReanalyze = async (stock: Stock) => {
        if (!stock.id) return
        setReanalyzingId(stock.id)

        // Get the current analyzed_at timestamp before triggering reanalysis
        const previousAnalyzedAt = stock.status_details?.analyzed_at

        // Optimistically update status to 'analyzing'
        setStocks(prev => prev.map(s =>
            s.id === stock.id
                ? { ...s, status: 'analyzing' as const, status_message: 'Analyzing transcript...' }
                : s
        ))

        try {
            const payload = {
                force: true,
                ...(selectedQuarter ? { quarter: selectedQuarter.quarter, year: selectedQuarter.year } : {})
            }

            const response = await fetch(`${API_URL}/analyze/${stock.id}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            })
            if (!response.ok) {
                console.error('Failed to start analysis', await response.text())
                // Revert status on error
                if (selectedQuarter) {
                    fetchWatchlist(selectedQuarter.quarter, selectedQuarter.year)
                } else {
                    fetchWatchlist()
                }
            } else {
                // Start polling more frequently to catch when analysis completes
                const pollInterval = setInterval(async () => {
                    // Include quarter params to maintain current view
                    let pollUrl = `${API_URL}/watchlist`
                    if (selectedQuarter) {
                        pollUrl += `?quarter=${selectedQuarter.quarter}&year=${selectedQuarter.year}`
                    }
                    const watchlistResponse = await fetch(pollUrl)
                    if (watchlistResponse.ok) {
                        const data = await watchlistResponse.json()
                        const updatedStock = data.find((s: Stock) => s.id === stock.id)
                        // Check if there's a NEW analysis by comparing timestamps
                        if (updatedStock?.status === 'analysis_failed') {
                            clearInterval(pollInterval)
                            reanalyzeIntervalRef.current = null
                            setStocks(data)
                        } else if (updatedStock?.status === 'analyzed') {
                            const newAnalyzedAt = updatedStock.status_details?.analyzed_at
                            // Only stop polling if the timestamp changed (new analysis completed)
                            if (!previousAnalyzedAt || (newAnalyzedAt && newAnalyzedAt !== previousAnalyzedAt)) {
                                clearInterval(pollInterval)
                                reanalyzeIntervalRef.current = null
                                setStocks(data)
                            }
                        }
                    }
                }, 2000) // Poll every 2 seconds

                // Track interval for cleanup on unmount
                if (reanalyzeIntervalRef.current) {
                    clearInterval(reanalyzeIntervalRef.current)
                }
                reanalyzeIntervalRef.current = pollInterval

                // Stop polling after 2 minutes max and refresh to show current state
                const timeoutId = setTimeout(() => {
                    clearInterval(pollInterval)
                    reanalyzeIntervalRef.current = null
                    if (selectedQuarter) {
                        fetchWatchlist(selectedQuarter.quarter, selectedQuarter.year)
                    } else {
                        fetchWatchlist() // Final refresh to show whatever state we're in
                    }
                }, 120000)
                // Track timeout for cleanup on unmount
                if (reanalyzeTimeoutRef.current) {
                    clearTimeout(reanalyzeTimeoutRef.current)
                }
                reanalyzeTimeoutRef.current = timeoutId
            }
        } catch (error) {
            console.error('Error starting analysis:', error)
            // Revert status on error
            if (selectedQuarter) {
                fetchWatchlist(selectedQuarter.quarter, selectedQuarter.year)
            } else {
                fetchWatchlist()
            }
        } finally {
            setReanalyzingId(null)
        }
    }

    // Cleanup any in-flight reanalyze polling if component unmounts
    useEffect(() => {
        return () => {
            if (reanalyzeIntervalRef.current) {
                clearInterval(reanalyzeIntervalRef.current)
                reanalyzeIntervalRef.current = null
            }
            if (reanalyzeTimeoutRef.current) {
                clearTimeout(reanalyzeTimeoutRef.current)
                reanalyzeTimeoutRef.current = null
            }
        }
    }, [])

    const handleDownloadAnalysis = async (stock: Stock) => {
        if (stock.status !== 'analyzed') {
            alert('Analysis is not ready to download yet.')
            return
        }
        if (!stock.id) {
            console.warn('Cannot download analysis without a stock id')
            return
        }

        setDownloadingId(stock.id)
        try {
            const query = selectedQuarter ? `?quarter=${selectedQuarter.quarter}&year=${selectedQuarter.year}` : ''
            const response = await fetch(`${API_URL}/analyses/${stock.id}/download${query}`)
            if (!response.ok) {
                console.error('Failed to download analysis', await response.text())
                alert('Unable to download analysis. Please try again.')
                return
            }

            const blob = await response.blob()
            const disposition = response.headers.get('Content-Disposition') || ''
            const match = disposition.match(/filename=\"?([^\";]+)\"?/i)
            const fallbackName = `${(stock.symbol || 'analysis')}-${stock.status_details?.quarter || 'latest'}-${stock.status_details?.year || ''}-analysis`.replace(/[^a-zA-Z0-9._-]+/g, '_')
            const fileName = match && match[1] ? match[1] : `${fallbackName}.pdf`

            const url = window.URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.download = fileName
            document.body.appendChild(link)
            link.click()
            link.remove()
            window.URL.revokeObjectURL(url)
        } catch (error) {
            console.error('Error downloading analysis:', error)
            alert('Unable to download analysis. Please try again.')
        } finally {
            setDownloadingId(null)
        }
    }

    const getStatusBadge = (stock: Stock) => {
        const { status, status_details } = stock

        switch (status) {
            case 'analyzed':
                return (
                    <Badge className="bg-green-500/20 text-green-400 border-green-500/50 hover:bg-green-500/40 hover:text-white hover:shadow-[0_0_15px_rgba(34,197,94,0.5)] transition-all duration-200 cursor-pointer">
                        ✓ Analyzed
                    </Badge>
                )
            case 'transcript_ready':
                return (
                    <Badge className="bg-orange-500/20 text-orange-400 border-orange-500/50 hover:bg-orange-500/40 hover:text-white hover:shadow-[0_0_15px_rgba(249,115,22,0.5)] transition-all duration-200 cursor-pointer">
                        📄 Transcript Ready
                    </Badge>
                )
            case 'upcoming':
                return (
                    <div className="flex flex-col gap-1">
                        <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/50 hover:bg-blue-500/40 hover:text-white hover:shadow-[0_0_15px_rgba(59,130,246,0.5)] transition-all duration-200 cursor-pointer">
                            📅 Upcoming
                        </Badge>
                        {status_details?.event_date && (
                            <span className="text-xs text-muted-foreground">
                                {new Date(status_details.event_date).toLocaleDateString('en-IN', {
                                    month: 'short',
                                    day: 'numeric',
                                    hour: '2-digit',
                                    minute: '2-digit'
                                })}
                            </span>
                        )}
                    </div>
                )
            case 'no_transcript':
                return (
                    <Badge className="bg-gray-500/20 text-gray-400 border-gray-500/50 hover:bg-gray-500/40 hover:text-white hover:shadow-[0_0_15px_rgba(156,163,175,0.5)] transition-all duration-200 cursor-pointer">
                        ⏳ No Transcript
                    </Badge>
                )
            case 'fetching':
                return (
                    <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/50 hover:bg-blue-500/40 hover:text-white hover:shadow-[0_0_15px_rgba(59,130,246,0.5)] transition-all duration-200 cursor-pointer">
                        <Loader2 className="h-3 w-3 animate-spin mr-1" />
                        Fetching Transcript...
                    </Badge>
                )
            case 'analyzing':
                return (
                    <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/50 hover:bg-purple-500/40 hover:text-white hover:shadow-[0_0_15px_rgba(168,85,247,0.5)] transition-all duration-200 cursor-pointer">
                        <Loader2 className="h-3 w-3 animate-spin mr-1" />
                        Analyzing...
                    </Badge>
                )
            case 'analysis_failed':
                return (
                    <Badge className="bg-red-500/20 text-red-400 border-red-500/50 hover:bg-red-500/40 hover:text-white hover:shadow-[0_0_15px_rgba(239,68,68,0.5)] transition-all duration-200 cursor-pointer">
                        ✕ Analysis Failed
                    </Badge>
                )
            default:
                return <Badge variant="outline" className="text-muted-foreground">Unknown</Badge>
        }
    }

    return (
        <div className="bg-background text-foreground p-8 transition-colors duration-300 min-h-screen">
            <div className="max-w-7xl mx-auto">
                {/* Search Bar and Quarter Selection */}
                <div className="flex items-center justify-between mb-6 relative z-50">
                    <div className="relative w-full max-w-md" ref={searchContainerRef}>
                        <Input
                            type="text"
                            placeholder="Search stocks..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className="w-full bg-secondary/50 border-border text-foreground placeholder:text-muted-foreground"
                        />

                        {/* Search Results Dropdown */}
                        {(searchResults.length > 0 || isSearching) && searchQuery && (
                            <div className="absolute top-full left-0 right-0 mt-2 bg-popover border border-border rounded-md shadow-lg overflow-hidden max-h-96 overflow-y-auto z-50">
                                {isSearching ? (
                                    <div className="p-4 text-center text-muted-foreground flex items-center justify-center gap-2">
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        Searching...
                                    </div>
                                ) : (
                                    searchResults.map((stock) => (
                                        <div
                                            key={stock.symbol}
                                            className="flex items-center justify-between p-3 hover:bg-accent transition-colors cursor-pointer group"
                                            onClick={() => addToWatchlist(stock)}
                                        >
                                            <div className="flex items-center gap-3">
                                                <Button size="icon" variant="ghost" className="h-8 w-8 text-muted-foreground group-hover:text-primary">
                                                    <Plus className="h-4 w-4" />
                                                </Button>
                                                <div>
                                                    <div className="font-medium text-foreground">{stock.symbol}</div>
                                                    <div className="text-sm text-muted-foreground">{stock.name}</div>
                                                </div>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        )}
                    </div>

                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="outline" className="min-w-[200px] justify-between border-border bg-secondary/50">
                                {selectedQuarter?.label || 'Select Quarter'}
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent className="bg-popover border-border max-h-80 overflow-y-auto">
                            {quarters.map((q) => (
                                <DropdownMenuItem
                                    key={`${q.quarter}-${q.year}`}
                                    onClick={() => setSelectedQuarter(q)}
                                    className="text-popover-foreground hover:bg-accent focus:bg-accent focus:text-accent-foreground"
                                >
                                    {q.label}
                                </DropdownMenuItem>
                            ))}
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>

                {/* Table */}
                <div className="relative">
                    <div className="border border-border rounded-lg overflow-hidden bg-card">
                        <Table>
                            <TableHeader>
                                <TableRow className="border-border hover:bg-transparent">
                                    <TableHead className="text-muted-foreground">Symbol</TableHead>
                                    <TableHead className="text-muted-foreground">Name</TableHead>
                                    <TableHead className="text-muted-foreground">Status</TableHead>
                                    <TableHead className="text-muted-foreground text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {isLoading ? (
                                    <TableRow>
                                        <TableCell colSpan={4} className="text-center py-8 text-muted-foreground">
                                            <div className="flex items-center justify-center gap-2">
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                                Loading watchlist...
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ) : stocks.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={4} className="text-center py-8 text-muted-foreground">
                                            No stocks in watchlist. Search to add some!
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    stocks.map((stock) => (
                                        <TableRow
                                            key={stock.symbol}
                                            className="border-border hover:bg-accent/50 transition-colors"
                                        >
                                            <TableCell className="font-medium text-foreground">{stock.symbol}</TableCell>
                                            <TableCell className="text-foreground">{stock.name}</TableCell>
                                            <TableCell>{getStatusBadge(stock)}</TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex items-center justify-end gap-2">
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        onClick={() => handleReanalyze(stock)}
                                                        className="rounded-full h-9 w-9 hover:bg-accent hover:scale-110 transition-all duration-200"
                                                    >
                                                        {reanalyzingId === stock.id ? (
                                                            <Loader2 className="h-4 w-4 animate-spin" />
                                                        ) : (
                                                            <Sparkles className="h-4 w-4" />
                                                        )}
                                                    </Button>
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        onClick={() => handleDownloadAnalysis(stock)}
                                                        disabled={stock.status !== 'analyzed' || !stock.id || downloadingId === stock.id}
                                                        className="rounded-full h-9 w-9 hover:bg-accent hover:scale-110 transition-all duration-200 disabled:opacity-50 disabled:hover:scale-100"
                                                    >
                                                        {downloadingId === stock.id ? (
                                                            <Loader2 className="h-4 w-4 animate-spin" />
                                                        ) : (
                                                            <Download className="h-4 w-4" />
                                                        )}
                                                    </Button>
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        onClick={() => handleDeleteStock(stock.symbol)}
                                                        className="rounded-full h-9 w-9 hover:bg-red-500/20 hover:text-red-400 hover:scale-110 transition-all duration-200"
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    </div>
                </div>
            </div>
        </div>
    )
}
