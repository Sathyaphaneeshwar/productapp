import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Download, RefreshCw, AlertCircle, Loader2 } from 'lucide-react'

type UpdateStatus = 'idle' | 'checking' | 'downloading' | 'ready' | 'error'

interface UpdateInfo {
    percent?: number
    message?: string
    version?: string
}

// Type declaration for electron updater exposed via preload
declare global {
    interface Window {
        electronUpdater?: {
            checkForUpdates: () => Promise<{ status: string }>
            installUpdate: () => Promise<{ status: string }>
            onUpdateStatus: (callback: (data: { status: UpdateStatus; info?: UpdateInfo }) => void) => void
            getVersion: () => Promise<string>
            isElectron: boolean
        }
    }
}

export default function UpdateButton() {
    const [status, setStatus] = useState<UpdateStatus>('idle')
    const [info, setInfo] = useState<UpdateInfo | null>(null)
    const [version, setVersion] = useState<string>('')
    const [isElectron, setIsElectron] = useState(false)

    useEffect(() => {
        // Check if running in Electron
        if (window.electronUpdater?.isElectron) {
            setIsElectron(true)

            // Get app version
            window.electronUpdater.getVersion().then((v) => setVersion(v))

            // Listen for update status changes
            window.electronUpdater.onUpdateStatus((data) => {
                setStatus(data.status)
                setInfo(data.info || null)
            })
        }
    }, [])

    // Don't render in web mode
    if (!isElectron) {
        return null
    }

    const handleClick = async () => {
        if (!window.electronUpdater) return

        if (status === 'ready') {
            // Install the update
            await window.electronUpdater.installUpdate()
        } else if (status === 'idle' || status === 'error') {
            // Check for updates
            setStatus('checking')
            await window.electronUpdater.checkForUpdates()
        }
    }

    const getIcon = () => {
        switch (status) {
            case 'checking':
                return <Loader2 className="h-5 w-5 animate-spin" />
            case 'downloading':
                return <Loader2 className="h-5 w-5 animate-spin" />
            case 'ready':
                return <Download className="h-5 w-5" />
            case 'error':
                return <AlertCircle className="h-5 w-5" />
            default:
                return <RefreshCw className="h-5 w-5" />
        }
    }

    const getTooltip = () => {
        switch (status) {
            case 'checking':
                return 'Checking for updates...'
            case 'downloading':
                return info?.percent
                    ? `Downloading update: ${Math.round(info.percent)}%`
                    : 'Downloading update...'
            case 'ready':
                return 'Update ready - Click to install'
            case 'error':
                return info?.message || 'Update error - Click to retry'
            default:
                return version ? `v${version} - Click to check for updates` : 'Check for updates'
        }
    }

    return (
        <div className="relative">
            <Button
                variant="ghost"
                size="icon"
                onClick={handleClick}
                disabled={status === 'checking' || status === 'downloading'}
                className={`rounded-full hover:bg-accent transition-all duration-200 ${status === 'ready' ? 'text-green-500 hover:text-green-400' : ''
                    } ${status === 'error' ? 'text-red-500 hover:text-red-400' : ''}`}
                title={getTooltip()}
            >
                {getIcon()}
            </Button>

            {/* Notification dot when update is ready */}
            {status === 'ready' && (
                <span className="absolute -top-0.5 -right-0.5 h-3 w-3 rounded-full bg-green-500 animate-pulse" />
            )}
        </div>
    )
}
