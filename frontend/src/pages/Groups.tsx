import { useState, useEffect, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Plus, Settings, FileText, List, Trash2, X, Loader2, Search, Pencil } from 'lucide-react'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'


const API_URL = 'http://localhost:5000/api'

type Group = {
    id: number
    name: string
    stock_count: number
    is_active: boolean
    deep_research_prompt?: string
    stock_summary_prompt?: string
}

type GroupDetail = Group & {
    stocks: {
        id: number
        symbol: string
        name: string
        added_at: string
        quarter?: string
        year?: number
        transcript_status?: string
        transcript_created_at?: string
    }[]
    transcripts_ready?: number
    transcripts_total?: number
}

type GroupArticle = {
    id: number
    quarter: string
    year: number
    status: string
    model_provider?: string
    model_id?: string
    created_at: string
    updated_at: string
    rendered_html?: string
}

type Quarter = {
    quarter: string
    year: number
    label: string
}

export default function Groups() {
    const [groups, setGroups] = useState<Group[]>([])
    const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null)
    const [selectedGroup, setSelectedGroup] = useState<GroupDetail | null>(null)
    const [activeTab, setActiveTab] = useState<'stocks' | 'articles' | 'settings'>('stocks')
    const [isLoading, setIsLoading] = useState(true)
    const [isCreating, setIsCreating] = useState(false)
    const [newGroupName, setNewGroupName] = useState('')
    const [stockSearchQuery, setStockSearchQuery] = useState('')
    const [searchResults, setSearchResults] = useState<any[]>([])
    const [isSearching, setIsSearching] = useState(false)
    const [deletingGroupId, setDeletingGroupId] = useState<number | null>(null)
    const [renamingGroupId, setRenamingGroupId] = useState<number | null>(null)
    const [renameValue, setRenameValue] = useState('')
    const [articles, setArticles] = useState<GroupArticle[]>([])
    const [articlesLoading, setArticlesLoading] = useState(false)
    const [articleContentLoading, setArticleContentLoading] = useState(false)
    const [openArticleId, setOpenArticleId] = useState<number | null>(null)
    const [openArticleContent, setOpenArticleContent] = useState<string | null>(null)
    const [forcingRun, setForcingRun] = useState(false)
    const [quarters, setQuarters] = useState<Quarter[]>([])
    const [selectedQuarter, setSelectedQuarter] = useState<Quarter | null>(null)
    const [settingsSaveStatus, setSettingsSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
    const stockSearchContainerRef = useRef<HTMLDivElement>(null)
    const pollingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

    // Fetch groups and quarters on mount
    useEffect(() => {
        fetchGroups()
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

    // Fetch group details when selected or quarter changes
    useEffect(() => {
        if (selectedGroupId && selectedQuarter) {
            fetchGroupDetails(selectedGroupId, selectedQuarter.quarter, selectedQuarter.year)
            fetchArticles(selectedGroupId)
        } else if (!selectedGroupId) {
            setSelectedGroup(null)
            setArticles([])
        }
        setOpenArticleId(null)
        setOpenArticleContent(null)
    }, [selectedGroupId, selectedQuarter])

    // Search stocks for adding to group
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
            if (stockSearchContainerRef.current && !stockSearchContainerRef.current.contains(event.target as Node)) {
                setSearchResults([])
                setStockSearchQuery('')
            }
        }

        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const fetchGroups = async () => {
        try {
            const response = await fetch(`${API_URL}/groups`)
            if (response.ok) {
                const data = await response.json()
                setGroups(data)
                // Select first group by default if none selected and groups exist
                if (!selectedGroupId && data.length > 0) {
                    setSelectedGroupId(data[0].id)
                }
            }
        } catch (error) {
            console.error('Error fetching groups:', error)
        } finally {
            setIsLoading(false)
        }
    }

    const fetchGroupDetails = async (id: number, quarter?: string, year?: number) => {
        try {
            let url = `${API_URL}/groups/${id}`
            if (quarter && year) {
                url += `?quarter=${quarter}&year=${year}`
            }
            const response = await fetch(url)
            if (response.ok) {
                const data = await response.json()
                setSelectedGroup(data)
            }
        } catch (error) {
            console.error('Error fetching group details:', error)
        }
    }

    const createGroup = async () => {
        if (!newGroupName.trim()) return

        try {
            const response = await fetch(`${API_URL}/groups`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newGroupName })
            })

            if (response.ok) {
                setNewGroupName('')
                setIsCreating(false)
                fetchGroups()
            }
        } catch (error) {
            console.error('Error creating group:', error)
        }
    }

    const fetchArticles = async (groupId: number) => {
        setArticlesLoading(true)
        try {
            const response = await fetch(`${API_URL}/groups/${groupId}/articles`)
            if (response.ok) {
                const data = await response.json()
                setArticles(data)
            } else {
                setArticles([])
            }
        } catch (error) {
            console.error('Error fetching group articles:', error)
            setArticles([])
        } finally {
            setArticlesLoading(false)
        }
    }

    const fetchArticleContent = async (groupId: number, runId: number) => {
        setArticleContentLoading(true)
        try {
            const response = await fetch(`${API_URL}/groups/${groupId}/articles/${runId}`)
            if (response.ok) {
                const data = await response.json()
                setOpenArticleId(runId)
                setOpenArticleContent(data.rendered_html || data.llm_output || '')
            }
        } catch (error) {
            console.error('Error fetching article content:', error)
        } finally {
            setArticleContentLoading(false)
        }
    }

    const deleteGroup = (id: number, e: React.MouseEvent) => {
        e.stopPropagation() // Prevent selection when deleting
        setDeletingGroupId(id)
    }

    const confirmDelete = async (id: number) => {
        try {
            const response = await fetch(`${API_URL}/groups/${id}`, {
                method: 'DELETE'
            })

            if (response.ok) {
                if (selectedGroupId === id) setSelectedGroupId(null)
                fetchGroups()
            } else {
                alert('Failed to delete group. Please try again.')
            }
        } catch (error) {
            console.error('Error deleting group:', error)
            alert('Failed to delete group. Please try again.')
        } finally {
            setDeletingGroupId(null)
        }
    }

    const cancelDelete = (e: React.MouseEvent) => {
        e.stopPropagation()
        setDeletingGroupId(null)
    }

    const startRename = (id: number, currentName: string, e: React.MouseEvent) => {
        e.stopPropagation()
        setRenamingGroupId(id)
        setRenameValue(currentName)
    }

    const saveRename = async (id: number) => {
        if (!renameValue.trim()) return

        try {
            const response = await fetch(`${API_URL}/groups/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: renameValue })
            })

            if (response.ok) {
                setRenamingGroupId(null)
                setRenameValue('')
                fetchGroups()
                if (selectedGroupId === id && selectedQuarter) {
                    fetchGroupDetails(id, selectedQuarter.quarter, selectedQuarter.year)
                }
            }
        } catch (error) {
            console.error('Error renaming group:', error)
        }
    }

    const cancelRename = (e: React.MouseEvent) => {
        e.stopPropagation()
        setRenamingGroupId(null)
        setRenameValue('')
    }

    const forceGenerate = async () => {
        if (!selectedGroupId) return
        if (!selectedQuarter) {
            alert('Please select a quarter first.')
            return
        }
        setForcingRun(true)
        try {
            const response = await fetch(`${API_URL}/groups/${selectedGroupId}/articles`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ quarter: selectedQuarter.quarter, year: selectedQuarter.year })
            })
            if (response.ok) {
                // Refresh articles list and start polling
                fetchArticles(selectedGroupId)
                startArticlePolling(selectedGroupId)
            } else {
                const errorText = await response.text()
                console.error('Force generate failed', errorText)
                alert(`Failed to generate article: ${errorText || 'Unknown error'}`)
            }
        } catch (e: any) {
            console.error('Force generate failed', e)
            alert(`Failed to generate article: ${e.message || 'Network error'}`)
        } finally {
            setForcingRun(false)
        }
    }

    // Issue #6: Polling for article status updates
    const startArticlePolling = (groupId: number) => {
        // Clear any existing polling
        if (pollingIntervalRef.current) {
            clearInterval(pollingIntervalRef.current)
        }

        pollingIntervalRef.current = setInterval(async () => {
            try {
                const response = await fetch(`${API_URL}/groups/${groupId}/articles`)
                if (response.ok) {
                    const data = await response.json()
                    setArticles(data)

                    // Stop polling if no articles are in progress
                    const hasInProgress = data.some((a: GroupArticle) =>
                        a.status === 'pending' || a.status === 'in_progress'
                    )
                    if (!hasInProgress && pollingIntervalRef.current) {
                        clearInterval(pollingIntervalRef.current)
                        pollingIntervalRef.current = null
                    }
                }
            } catch (error) {
                console.error('Error polling articles:', error)
            }
        }, 5000) // Poll every 5 seconds
    }

    // Cleanup polling on unmount or group change
    useEffect(() => {
        return () => {
            if (pollingIntervalRef.current) {
                clearInterval(pollingIntervalRef.current)
            }
        }
    }, [selectedGroupId])

    const updateGroup = async (updates: Partial<Group>, showFeedback = false) => {
        if (!selectedGroupId) return

        if (showFeedback) setSettingsSaveStatus('saving')

        try {
            const response = await fetch(`${API_URL}/groups/${selectedGroupId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            })

            if (response.ok) {
                fetchGroups() // Update list for active status etc
                fetchGroupDetails(selectedGroupId)
                if (showFeedback) {
                    setSettingsSaveStatus('saved')
                    setTimeout(() => setSettingsSaveStatus('idle'), 2000)
                }
            } else {
                if (showFeedback) {
                    setSettingsSaveStatus('error')
                    setTimeout(() => setSettingsSaveStatus('idle'), 3000)
                }
            }
        } catch (error) {
            console.error('Error updating group:', error)
            if (showFeedback) {
                setSettingsSaveStatus('error')
                setTimeout(() => setSettingsSaveStatus('idle'), 3000)
            }
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

    const addStockToGroup = async (symbol: string) => {
        if (!selectedGroupId) return

        try {
            const response = await fetch(`${API_URL}/groups/${selectedGroupId}/stocks`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol })
            })

            if (response.ok) {
                // Keep search results visible for adding multiple stocks
                fetchGroupDetails(selectedGroupId)
                fetchGroups() // Update count
            }
        } catch (error) {
            console.error('Error adding stock to group:', error)
        }
    }

    const removeStockFromGroup = async (symbol: string) => {
        if (!selectedGroupId) return

        try {
            const response = await fetch(`${API_URL}/groups/${selectedGroupId}/stocks/${symbol}`, {
                method: 'DELETE'
            })

            if (response.ok) {
                fetchGroupDetails(selectedGroupId)
                fetchGroups() // Update count
            }
        } catch (error) {
            console.error('Error removing stock from group:', error)
        }
    }

    return (
        <div className="flex h-[calc(100vh-8rem)] max-w-7xl mx-auto p-6 gap-6">
            {/* Sidebar - Group List */}
            <div className="w-1/4 bg-card border border-border rounded-lg flex flex-col overflow-hidden">
                <div className="p-4 border-b border-border flex items-center justify-between bg-muted/30">
                    <h2 className="font-semibold text-lg">List of groups</h2>
                    <Button size="icon" variant="ghost" onClick={() => setIsCreating(true)}>
                        <Plus className="h-5 w-5" />
                    </Button>
                </div>

                {isCreating && (
                    <div className="p-3 border-b border-border bg-accent/20">
                        <div className="flex gap-2">
                            <Input
                                value={newGroupName}
                                onChange={(e) => setNewGroupName(e.target.value)}
                                placeholder="Group Name"
                                className="h-8 text-sm"
                                autoFocus
                                onKeyDown={(e) => e.key === 'Enter' && createGroup()}
                            />
                            <Button size="sm" onClick={createGroup} disabled={!newGroupName.trim()}>Add</Button>
                            <Button size="sm" variant="ghost" onClick={() => setIsCreating(false)}><X className="h-4 w-4" /></Button>
                        </div>
                    </div>
                )}

                <div className="flex-1 overflow-y-auto p-2 space-y-1">
                    {isLoading ? (
                        <div className="flex justify-center p-4"><Loader2 className="animate-spin h-5 w-5 text-muted-foreground" /></div>
                    ) : groups.length === 0 ? (
                        <div className="text-center p-4 text-muted-foreground text-sm">No groups yet</div>
                    ) : (
                        groups.map(group => (
                            <div
                                key={group.id}
                                onClick={() => deletingGroupId !== group.id && setSelectedGroupId(group.id)}
                                className={`flex items-center justify-between p-3 rounded-md cursor-pointer transition-colors group ${selectedGroupId === group.id ? 'bg-primary/10 text-primary border border-primary/20' : 'hover:bg-accent text-muted-foreground hover:text-foreground'
                                    }`}
                            >
                                {deletingGroupId === group.id ? (
                                    <div className="flex items-center justify-between w-full gap-2" onClick={(e) => e.stopPropagation()}>
                                        <span className="text-xs font-medium">Delete?</span>
                                        <div className="flex gap-1">
                                            <Button
                                                size="sm"
                                                variant="destructive"
                                                className="h-6 px-2 text-xs"
                                                onClick={() => confirmDelete(group.id)}
                                            >
                                                Yes
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                className="h-6 px-2 text-xs"
                                                onClick={cancelDelete}
                                            >
                                                No
                                            </Button>
                                        </div>
                                    </div>
                                ) : renamingGroupId === group.id ? (
                                    <div className="flex items-center justify-between w-full gap-2" onClick={(e) => e.stopPropagation()}>
                                        <Input
                                            value={renameValue}
                                            onChange={(e) => setRenameValue(e.target.value)}
                                            className="h-7 text-sm flex-1"
                                            autoFocus
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter') saveRename(group.id)
                                                if (e.key === 'Escape') {
                                                    setRenamingGroupId(null)
                                                    setRenameValue('')
                                                }
                                            }}
                                        />
                                        <div className="flex gap-1">
                                            <Button
                                                size="sm"
                                                className="h-6 px-2 text-xs"
                                                onClick={() => saveRename(group.id)}
                                                disabled={!renameValue.trim()}
                                            >
                                                Save
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                className="h-6 px-2 text-xs"
                                                onClick={cancelRename}
                                            >
                                                Cancel
                                            </Button>
                                        </div>
                                    </div>
                                ) : (
                                    <>
                                        <div className="flex flex-col">
                                            <span className="font-medium">{group.name}</span>
                                            <span className="text-xs opacity-70">{group.stock_count} stocks</span>
                                        </div>
                                        <div className="flex gap-1">
                                            <Button
                                                size="icon"
                                                variant="ghost"
                                                className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-blue-500/20 hover:text-blue-400"
                                                onClick={(e) => startRename(group.id, group.name, e)}
                                            >
                                                <Pencil className="h-3 w-3" />
                                            </Button>
                                            <Button
                                                size="icon"
                                                variant="ghost"
                                                className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500/20 hover:text-red-400"
                                                onClick={(e) => deleteGroup(group.id, e)}
                                            >
                                                <Trash2 className="h-3 w-3" />
                                            </Button>
                                        </div>
                                    </>
                                )}
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Main Area */}
            <div className="flex-1 bg-card border border-border rounded-lg flex flex-col overflow-hidden">
                {selectedGroup ? (
                    <>
                        {/* Header */}
                        <div className="p-6 border-b border-border flex items-center justify-between bg-muted/10">
                            <h1 className="text-2xl font-bold">{selectedGroup.name}</h1>
                            <div className="flex items-center gap-2">
                                <span className="text-sm text-muted-foreground">Active</span>
                                <div
                                    className={`w-10 h-5 rounded-full p-1 cursor-pointer transition-colors ${selectedGroup.is_active ? 'bg-green-500' : 'bg-muted'}`}
                                    onClick={() => updateGroup({ is_active: !selectedGroup.is_active })}
                                >
                                    <div className={`w-3 h-3 rounded-full bg-white shadow-sm transition-transform ${selectedGroup.is_active ? 'translate-x-5' : 'translate-x-0'}`} />
                                </div>
                            </div>
                        </div>

                        {/* Tabs */}
                        <div className="flex border-b border-border bg-muted/30 px-6">
                            <button
                                onClick={() => setActiveTab('stocks')}
                                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${activeTab === 'stocks' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
                                    }`}
                            >
                                <List className="h-4 w-4" /> List of Stocks
                            </button>
                            <button
                                onClick={() => setActiveTab('articles')}
                                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${activeTab === 'articles' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
                                    }`}
                            >
                                <FileText className="h-4 w-4" /> Articles
                            </button>
                            <button
                                onClick={() => setActiveTab('settings')}
                                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${activeTab === 'settings' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
                                    }`}
                            >
                                <Settings className="h-4 w-4" /> Settings
                            </button>
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-y-auto p-6">
                            {activeTab === 'stocks' && (
                                <div className="space-y-6">
                                    {/* Add Stock Search */}
                                    <div className="relative max-w-md" ref={stockSearchContainerRef}>
                                        <div className="relative">
                                            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                                            <Input
                                                placeholder="Search stocks to add..."
                                                value={stockSearchQuery}
                                                onChange={(e) => setStockSearchQuery(e.target.value)}
                                                className="pl-9"
                                            />
                                        </div>
                                        {(searchResults.length > 0 || isSearching) && stockSearchQuery && (
                                            <div className="absolute top-full left-0 right-0 mt-2 bg-popover border border-border rounded-md shadow-lg overflow-hidden z-50">
                                                {isSearching ? (
                                                    <div className="p-3 text-center text-sm text-muted-foreground">Searching...</div>
                                                ) : (
                                                    searchResults.map(stock => (
                                                        <div
                                                            key={stock.symbol}
                                                            className="flex items-center justify-between p-2 hover:bg-accent cursor-pointer text-sm"
                                                            onClick={() => addStockToGroup(stock.symbol)}
                                                        >
                                                            <span>{stock.symbol} - {stock.name}</span>
                                                            <Plus className="h-4 w-4 text-muted-foreground" />
                                                        </div>
                                                    ))
                                                )}
                                            </div>
                                        )}
                                    </div>

                                    <div className="flex items-center justify-between">
                                        <div className="text-sm text-muted-foreground">
                                            Transcripts ready: <span className="font-semibold text-foreground">{selectedGroup.transcripts_ready ?? 0}</span> / {selectedGroup.transcripts_total ?? 0}
                                        </div>
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="outline" className="min-w-[180px] justify-between border-border bg-secondary/50">
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

                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Name</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead>Quarter</TableHead>
                                                <TableHead className="text-right">Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {selectedGroup.stocks?.length === 0 ? (
                                                <TableRow>
                                                    <TableCell colSpan={4} className="text-center py-8 text-muted-foreground">No stocks in this group</TableCell>
                                                </TableRow>
                                            ) : (
                                                selectedGroup.stocks?.map(stock => (
                                                    <TableRow key={stock.symbol}>
                                                        <TableCell className="font-medium">{stock.name}</TableCell>
                                                        <TableCell>
                                                            <Badge
                                                                variant={stock.transcript_status === 'available' ? 'default' : 'secondary'}
                                                                className={cn(
                                                                    stock.transcript_status === 'available' ? 'bg-green-600 text-white' : 'bg-amber-500/20 text-amber-600'
                                                                )}
                                                            >
                                                                {stock.transcript_status ? stock.transcript_status : 'no transcript'}
                                                            </Badge>
                                                        </TableCell>
                                                        <TableCell className="text-muted-foreground text-xs">
                                                            {stock.quarter && stock.year ? `${stock.quarter} ${stock.year}` : '—'}
                                                        </TableCell>
                                                        <TableCell className="text-right">
                                                            <Button
                                                                size="icon"
                                                                variant="ghost"
                                                                className="h-8 w-8 text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
                                                                onClick={() => removeStockFromGroup(stock.symbol)}
                                                            >
                                                                <Trash2 className="h-4 w-4" />
                                                            </Button>
                                                        </TableCell>
                                                    </TableRow>
                                                ))
                                            )}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}

                            {activeTab === 'articles' && (
                                <div className="space-y-4">
                                    <div className="flex items-center gap-3">
                                        <Button variant="outline" size="sm" disabled={forcingRun} onClick={forceGenerate}>
                                            {forcingRun ? 'Starting...' : 'Force Generate'}
                                        </Button>
                                        <div className="text-xs text-muted-foreground">
                                            Uses whatever transcripts are available for the latest quarter. Missing stocks will be skipped.
                                        </div>
                                    </div>
                                    {articlesLoading ? (
                                        <div className="flex items-center justify-center py-10 text-muted-foreground">
                                            <Loader2 className="h-5 w-5 animate-spin mr-2" />
                                            Loading articles...
                                        </div>
                                    ) : articles.length === 0 ? (
                                        <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                                            <FileText className="h-12 w-12 mb-4 opacity-20" />
                                            <p>No articles associated with this group yet.</p>
                                        </div>
                                    ) : (
                                        articles.map(article => (
                                            <div key={article.id} className="border border-border rounded-lg p-4 bg-muted/20">
                                                <div className="flex items-start justify-between gap-4">
                                                    <div>
                                                        <div className="text-sm text-muted-foreground">Quarter</div>
                                                        <div className="text-lg font-semibold">{article.quarter} {article.year}</div>
                                                        <div className="text-xs text-muted-foreground mt-1">
                                                            Updated {new Date(article.updated_at).toLocaleString()}
                                                        </div>
                                                        <div className="text-xs text-muted-foreground">
                                                            Status: <span className="font-medium">{article.status}</span>
                                                        </div>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            disabled={article.status !== 'done' || articleContentLoading}
                                                            onClick={() => fetchArticleContent(selectedGroupId!, article.id)}
                                                        >
                                                            {article.status === 'done' ? (
                                                                articleContentLoading && openArticleId === article.id ? 'Loading...' : 'View'
                                                            ) : (
                                                                article.status
                                                            )}
                                                        </Button>
                                                    </div>
                                                </div>
                                                {openArticleId === article.id && openArticleContent && (
                                                    <div
                                                        className="mt-3 p-3 rounded-md bg-background border border-border max-h-96 overflow-auto text-sm prose prose-sm dark:prose-invert"
                                                        dangerouslySetInnerHTML={{ __html: openArticleContent }}
                                                    />
                                                )}
                                            </div>
                                        ))
                                    )}
                                </div>
                            )}

                            {activeTab === 'settings' && (
                                <div className="space-y-6 max-w-2xl">
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Deep Research Prompt</label>
                                        <textarea
                                            className="w-full min-h-[150px] p-3 rounded-md border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                                            value={selectedGroup.deep_research_prompt || ''}
                                            onChange={(e) => setSelectedGroup({ ...selectedGroup, deep_research_prompt: e.target.value })}
                                            placeholder="Enter prompt for deep research..."
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Stock Summary Prompt</label>
                                        <textarea
                                            className="w-full min-h-[150px] p-3 rounded-md border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                                            value={selectedGroup.stock_summary_prompt || ''}
                                            onChange={(e) => setSelectedGroup({ ...selectedGroup, stock_summary_prompt: e.target.value })}
                                            placeholder="Enter prompt for stock summaries..."
                                        />
                                    </div>
                                    <div className="flex items-center gap-3">
                                        <Button
                                            onClick={() => updateGroup({
                                                deep_research_prompt: selectedGroup.deep_research_prompt,
                                                stock_summary_prompt: selectedGroup.stock_summary_prompt
                                            }, true)}
                                            disabled={settingsSaveStatus === 'saving'}
                                        >
                                            {settingsSaveStatus === 'saving' ? 'Saving...' : 'Save Changes'}
                                        </Button>
                                        {settingsSaveStatus === 'saved' && (
                                            <span className="text-sm text-green-500">✓ Saved successfully</span>
                                        )}
                                        {settingsSaveStatus === 'error' && (
                                            <span className="text-sm text-red-500">✗ Failed to save</span>
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>
                    </>
                ) : (
                    <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                        <List className="h-12 w-12 mb-4 opacity-20" />
                        <p>Select a group from the sidebar to view details</p>
                    </div>
                )}
            </div>
        </div>
    )
}
