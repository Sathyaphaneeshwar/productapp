import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
    CheckCircle2,
    Download,
    Loader2,
    Pencil,
    Plus,
    Search,
    Trash2,
    Upload,
    XCircle,
} from 'lucide-react'

const API_URL = 'http://127.0.0.1:5001/api'

type Feedback = {
    type: 'success' | 'error'
    message: string
}

type ImportRow = {
    row: number
    isin: string
    company_name: string
    field_changes?: Record<string, [unknown, unknown]>
}

type ImportIssue = {
    row: number
    reason: string
    data?: Record<string, string>
}

type ImportPreview = {
    batch_id: number
    filename: string
    rows_total: number
    new: number
    updated: number
    unchanged: number
    invalid_count: number
    invalid: ImportIssue[]
    warnings: ImportIssue[]
    sample_new: ImportRow[]
    sample_updated: ImportRow[]
}

type ManagedStock = {
    id: number
    symbol: string
    stock_symbol?: string | null
    bse_code?: string | null
    isin: string
    company_name: string
    source: 'master' | 'import' | 'manual'
    is_active: boolean
    in_watchlist: boolean
    group_count: number
}

const normalizeIsin = (value: string) => value.trim().toUpperCase()

const isValidIsin = (value: string) => {
    const isin = normalizeIsin(value)
    if (!/^[A-Z]{2}[A-Z0-9]{9}[0-9]$/.test(isin)) return false
    const expanded = [...isin]
        .map(character => /[A-Z]/.test(character) ? String(character.charCodeAt(0) - 55) : character)
        .join('')
    let total = 0
    let shouldDouble = false
    for (let index = expanded.length - 1; index >= 0; index -= 1) {
        let digit = Number(expanded[index])
        if (shouldDouble) {
            digit *= 2
            if (digit > 9) digit -= 9
        }
        total += digit
        shouldDouble = !shouldDouble
    }
    return total % 10 === 0
}

export default function StockManagement() {
    const [manualIsin, setManualIsin] = useState('')
    const [manualName, setManualName] = useState('')
    const [manualSaving, setManualSaving] = useState(false)
    const [feedback, setFeedback] = useState<Feedback | null>(null)

    const [preview, setPreview] = useState<ImportPreview | null>(null)
    const [previewing, setPreviewing] = useState(false)
    const [committing, setCommitting] = useState(false)
    const fileInputRef = useRef<HTMLInputElement>(null)

    const [stocks, setStocks] = useState<ManagedStock[]>([])
    const [stocksLoading, setStocksLoading] = useState(true)
    const [stockQuery, setStockQuery] = useState('')
    const [showAll, setShowAll] = useState(false)
    const [editingId, setEditingId] = useState<number | null>(null)
    const [editingName, setEditingName] = useState('')
    const [savingId, setSavingId] = useState<number | null>(null)

    const manualIsinValid = useMemo(
        () => !manualIsin || isValidIsin(manualIsin),
        [manualIsin],
    )

    useEffect(() => {
        if (!feedback) return
        const timeout = window.setTimeout(() => setFeedback(null), 5000)
        return () => window.clearTimeout(timeout)
    }, [feedback])

    const loadStocks = useCallback(async (query = stockQuery, all = showAll) => {
        setStocksLoading(true)
        try {
            const params = new URLSearchParams({
                source: all ? 'all' : 'user',
                per_page: '100',
            })
            if (query.trim()) params.set('q', query.trim())
            const response = await fetch(`${API_URL}/stocks/admin?${params}`)
            const data = await response.json()
            if (!response.ok) throw new Error(data.error || 'Could not load stocks')
            setStocks(data.stocks || [])
        } catch (error) {
            setFeedback({
                type: 'error',
                message: error instanceof Error ? error.message : 'Could not load stocks',
            })
        } finally {
            setStocksLoading(false)
        }
    }, [stockQuery, showAll])

    useEffect(() => {
        const timeout = window.setTimeout(() => {
            void loadStocks()
        }, 250)
        return () => window.clearTimeout(timeout)
    }, [loadStocks])

    const addManualStock = async () => {
        if (!isValidIsin(manualIsin) || !manualName.trim()) {
            setFeedback({
                type: 'error',
                message: 'Enter a valid ISIN and CompanyName.',
            })
            return
        }
        setManualSaving(true)
        try {
            const response = await fetch(`${API_URL}/stocks/manual`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    isin: normalizeIsin(manualIsin),
                    company_name: manualName.trim(),
                }),
            })
            const data = await response.json()
            if (response.status === 409 && data.existing?.id) {
                const shouldUpdate = window.confirm(
                    `${data.existing.company_name} already uses this ISIN. Update its company name?`,
                )
                if (!shouldUpdate) return
                const updateResponse = await fetch(`${API_URL}/stocks/${data.existing.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        company_name: manualName.trim(),
                        is_active: true,
                    }),
                })
                const updateData = await updateResponse.json()
                if (!updateResponse.ok) {
                    throw new Error(updateData.error || 'Could not update stock')
                }
                setFeedback({ type: 'success', message: 'Existing stock updated.' })
            } else if (!response.ok) {
                throw new Error(data.error || 'Could not add stock')
            } else {
                setFeedback({ type: 'success', message: 'Stock added successfully.' })
            }
            setManualIsin('')
            setManualName('')
            await loadStocks()
        } catch (error) {
            setFeedback({
                type: 'error',
                message: error instanceof Error ? error.message : 'Could not add stock',
            })
        } finally {
            setManualSaving(false)
        }
    }

    const previewFile = async (file: File) => {
        setPreviewing(true)
        setPreview(null)
        try {
            const formData = new FormData()
            formData.append('file', file)
            const response = await fetch(`${API_URL}/stocks/import/preview`, {
                method: 'POST',
                body: formData,
            })
            const data = await response.json()
            if (!response.ok) throw new Error(data.error || 'Could not preview file')
            setPreview(data)
        } catch (error) {
            setFeedback({
                type: 'error',
                message: error instanceof Error ? error.message : 'Could not preview file',
            })
        } finally {
            setPreviewing(false)
            if (fileInputRef.current) fileInputRef.current.value = ''
        }
    }

    const commitImport = async () => {
        if (!preview) return
        setCommitting(true)
        try {
            const response = await fetch(`${API_URL}/stocks/import/commit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ batch_id: preview.batch_id }),
            })
            const data = await response.json()
            if (!response.ok) throw new Error(data.error || 'Could not import stocks')
            setFeedback({
                type: 'success',
                message: `Imported ${data.new} new and updated ${data.updated} stocks.`,
            })
            setPreview(null)
            await loadStocks()
        } catch (error) {
            setFeedback({
                type: 'error',
                message: error instanceof Error ? error.message : 'Could not import stocks',
            })
        } finally {
            setCommitting(false)
        }
    }

    const saveStockName = async (stock: ManagedStock) => {
        if (!editingName.trim()) return
        setSavingId(stock.id)
        try {
            const response = await fetch(`${API_URL}/stocks/${stock.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ company_name: editingName.trim() }),
            })
            const data = await response.json()
            if (!response.ok) throw new Error(data.error || 'Could not update stock')
            setEditingId(null)
            await loadStocks()
        } catch (error) {
            setFeedback({
                type: 'error',
                message: error instanceof Error ? error.message : 'Could not update stock',
            })
        } finally {
            setSavingId(null)
        }
    }

    const setStockActive = async (stock: ManagedStock, isActive: boolean) => {
        setSavingId(stock.id)
        try {
            const response = await fetch(`${API_URL}/stocks/${stock.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_active: isActive }),
            })
            const data = await response.json()
            if (!response.ok) throw new Error(data.error || 'Could not update stock')
            await loadStocks()
        } catch (error) {
            setFeedback({
                type: 'error',
                message: error instanceof Error ? error.message : 'Could not update stock',
            })
        } finally {
            setSavingId(null)
        }
    }

    const deleteStock = async (stock: ManagedStock) => {
        if (!window.confirm(`Delete ${stock.company_name}?`)) return
        setSavingId(stock.id)
        try {
            const response = await fetch(`${API_URL}/stocks/${stock.id}`, {
                method: 'DELETE',
            })
            const data = await response.json()
            if (response.status === 409 && (data.reason === 'in_use' || data.reason === 'master')) {
                const shouldDeactivate = window.confirm(`${data.error}\n\nDeactivate it now?`)
                if (shouldDeactivate) await setStockActive(stock, false)
                return
            }
            if (!response.ok) throw new Error(data.error || 'Could not delete stock')
            setFeedback({ type: 'success', message: 'Stock deleted.' })
            await loadStocks()
        } catch (error) {
            setFeedback({
                type: 'error',
                message: error instanceof Error ? error.message : 'Could not delete stock',
            })
        } finally {
            setSavingId(null)
        }
    }

    return (
        <div className="space-y-8">
            {feedback && (
                <div
                    className={`flex items-center gap-2 rounded-lg border px-4 py-3 ${
                        feedback.type === 'success'
                            ? 'border-green-500/40 bg-green-500/10 text-green-400'
                            : 'border-red-500/40 bg-red-500/10 text-red-400'
                    }`}
                >
                    {feedback.type === 'success'
                        ? <CheckCircle2 className="h-4 w-4" />
                        : <XCircle className="h-4 w-4" />}
                    <span className="text-sm">{feedback.message}</span>
                </div>
            )}

            <section className="rounded-lg border border-border bg-card p-5">
                <div className="mb-4">
                    <h3 className="text-xl font-semibold">Add a stock</h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                        ISIN identifies the stock. Add it to a watchlist or group after saving to start monitoring.
                    </p>
                </div>
                <div className="grid gap-3 md:grid-cols-[220px_1fr_auto]">
                    <div>
                        <Input
                            value={manualIsin}
                            onChange={event => setManualIsin(event.target.value.toUpperCase())}
                            placeholder="ISIN"
                            maxLength={12}
                            className={!manualIsinValid ? 'border-red-500' : ''}
                        />
                        {!manualIsinValid && (
                            <p className="mt-1 text-xs text-red-400">Invalid ISIN or check digit.</p>
                        )}
                    </div>
                    <Input
                        value={manualName}
                        onChange={event => setManualName(event.target.value)}
                        placeholder="CompanyName"
                        onKeyDown={event => {
                            if (event.key === 'Enter') void addManualStock()
                        }}
                    />
                    <Button
                        onClick={() => void addManualStock()}
                        disabled={manualSaving || !manualIsin || !manualName.trim() || !manualIsinValid}
                    >
                        {manualSaving
                            ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            : <Plus className="mr-2 h-4 w-4" />}
                        Add stock
                    </Button>
                </div>
            </section>

            <section className="rounded-lg border border-border bg-card p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-xl font-semibold">Import from file</h3>
                        <p className="mt-1 text-sm text-muted-foreground">
                            Required header: <code className="rounded bg-muted px-1.5 py-0.5">ISIN,CompanyName</code>.
                            CSV, TSV, and XLSX files up to 5 MB are supported.
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <Button variant="outline" asChild>
                            <a href={`${API_URL}/stocks/template.csv`} download>
                                <Download className="mr-2 h-4 w-4" />
                                Template
                            </a>
                        </Button>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept=".csv,.tsv,.xlsx"
                            className="hidden"
                            onChange={event => {
                                const file = event.target.files?.[0]
                                if (file) void previewFile(file)
                            }}
                        />
                        <Button
                            onClick={() => fileInputRef.current?.click()}
                            disabled={previewing}
                        >
                            {previewing
                                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                : <Upload className="mr-2 h-4 w-4" />}
                            Choose file
                        </Button>
                    </div>
                </div>

                {preview && (
                    <div className="mt-5 space-y-4">
                        <div className="grid gap-3 sm:grid-cols-4">
                            {[
                                ['New', preview.new, 'text-green-400'],
                                ['Updated', preview.updated, 'text-blue-400'],
                                ['Unchanged', preview.unchanged, 'text-muted-foreground'],
                                ['Invalid', preview.invalid_count, 'text-red-400'],
                            ].map(([label, value, color]) => (
                                <div key={String(label)} className="rounded-md border border-border bg-muted/20 p-3">
                                    <p className="text-xs text-muted-foreground">{label}</p>
                                    <p className={`text-2xl font-semibold ${color}`}>{value}</p>
                                </div>
                            ))}
                        </div>

                        {(preview.sample_new.length > 0 || preview.sample_updated.length > 0) && (
                            <div className="max-h-60 overflow-auto rounded-md border border-border">
                                <table className="w-full text-sm">
                                    <thead className="sticky top-0 bg-muted text-left">
                                        <tr>
                                            <th className="px-3 py-2">Result</th>
                                            <th className="px-3 py-2">ISIN</th>
                                            <th className="px-3 py-2">CompanyName</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {preview.sample_new.map(row => (
                                            <tr key={`new-${row.isin}`} className="border-t border-border">
                                                <td className="px-3 py-2 text-green-400">New</td>
                                                <td className="px-3 py-2 font-mono">{row.isin}</td>
                                                <td className="px-3 py-2">{row.company_name}</td>
                                            </tr>
                                        ))}
                                        {preview.sample_updated.map(row => (
                                            <tr key={`updated-${row.isin}`} className="border-t border-border">
                                                <td className="px-3 py-2 text-blue-400">Update</td>
                                                <td className="px-3 py-2 font-mono">{row.isin}</td>
                                                <td className="px-3 py-2">{row.company_name}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}

                        {(preview.invalid.length > 0 || preview.warnings.length > 0) && (
                            <div className="max-h-52 overflow-auto rounded-md border border-red-500/30 bg-red-500/5 p-3">
                                {[...preview.invalid, ...preview.warnings].map((issue, index) => (
                                    <p key={`${issue.row}-${index}`} className="text-sm text-red-300">
                                        Row {issue.row}: {issue.reason}
                                    </p>
                                ))}
                            </div>
                        )}

                        <div className="flex items-center justify-between">
                            <p className="text-sm text-muted-foreground">
                                Preview only — no database changes have been made.
                            </p>
                            <Button
                                onClick={() => void commitImport()}
                                disabled={
                                    committing
                                    || preview.new + preview.updated + preview.unchanged === 0
                                }
                            >
                                {committing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Import {preview.new + preview.updated} changes
                            </Button>
                        </div>
                    </div>
                )}
            </section>

            <section className="rounded-lg border border-border bg-card p-5">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h3 className="text-xl font-semibold">Your stocks</h3>
                        <p className="mt-1 text-sm text-muted-foreground">
                            Imported and manually added stocks are shown by default.
                        </p>
                    </div>
                    <div className="flex items-center gap-3">
                        <label className="flex items-center gap-2 text-sm text-muted-foreground">
                            <input
                                type="checkbox"
                                checked={showAll}
                                onChange={event => setShowAll(event.target.checked)}
                            />
                            Show master list
                        </label>
                        <div className="relative">
                            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                value={stockQuery}
                                onChange={event => setStockQuery(event.target.value)}
                                placeholder="Search name or ISIN"
                                className="w-64 pl-9"
                            />
                        </div>
                    </div>
                </div>

                <div className="overflow-x-auto rounded-md border border-border">
                    <table className="w-full text-sm">
                        <thead className="bg-muted/50 text-left text-muted-foreground">
                            <tr>
                                <th className="px-3 py-2">ISIN</th>
                                <th className="px-3 py-2">CompanyName</th>
                                <th className="px-3 py-2">Source</th>
                                <th className="px-3 py-2">Usage</th>
                                <th className="px-3 py-2 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {stocksLoading ? (
                                <tr>
                                    <td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">
                                        <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                                    </td>
                                </tr>
                            ) : stocks.length === 0 ? (
                                <tr>
                                    <td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">
                                        No user-added stocks yet.
                                    </td>
                                </tr>
                            ) : stocks.map(stock => (
                                <tr key={stock.id} className="border-t border-border">
                                    <td className="px-3 py-2 font-mono">{stock.isin}</td>
                                    <td className="px-3 py-2">
                                        {editingId === stock.id ? (
                                            <Input
                                                value={editingName}
                                                onChange={event => setEditingName(event.target.value)}
                                                className="h-8"
                                                autoFocus
                                                onKeyDown={event => {
                                                    if (event.key === 'Enter') void saveStockName(stock)
                                                    if (event.key === 'Escape') setEditingId(null)
                                                }}
                                            />
                                        ) : stock.company_name}
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex gap-1">
                                            <Badge variant="outline">
                                                {stock.source === 'master'
                                                    ? 'Master'
                                                    : stock.source === 'manual'
                                                        ? 'Manual'
                                                        : 'Imported'}
                                            </Badge>
                                            {!stock.is_active && (
                                                <Badge className="border-amber-500/40 bg-amber-500/10 text-amber-400">
                                                    Inactive
                                                </Badge>
                                            )}
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-muted-foreground">
                                        {stock.in_watchlist ? 'Watchlist' : ''}
                                        {stock.in_watchlist && stock.group_count > 0 ? ' · ' : ''}
                                        {stock.group_count > 0 ? `${stock.group_count} group(s)` : ''}
                                        {!stock.in_watchlist && stock.group_count === 0 ? 'Unused' : ''}
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex justify-end gap-1">
                                            {editingId === stock.id ? (
                                                <Button
                                                    size="sm"
                                                    onClick={() => void saveStockName(stock)}
                                                    disabled={savingId === stock.id}
                                                >
                                                    Save
                                                </Button>
                                            ) : (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => {
                                                        setEditingId(stock.id)
                                                        setEditingName(stock.company_name)
                                                    }}
                                                    aria-label="Edit company name"
                                                >
                                                    <Pencil className="h-4 w-4" />
                                                </Button>
                                            )}
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => void setStockActive(stock, !stock.is_active)}
                                                disabled={savingId === stock.id}
                                            >
                                                {stock.is_active ? 'Deactivate' : 'Activate'}
                                            </Button>
                                            {stock.source !== 'master' && (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => void deleteStock(stock)}
                                                    disabled={savingId === stock.id}
                                                    className="text-red-400 hover:bg-red-500/10 hover:text-red-300"
                                                    aria-label="Delete stock"
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>
        </div>
    )
}
