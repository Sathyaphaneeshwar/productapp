import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Loader2, CheckCircle2, XCircle, Mail, Settings as SettingsIcon } from 'lucide-react'

type SettingsTab = 'email' | 'llm' | 'api'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5001/api'

export default function Settings() {
    const [activeTab, setActiveTab] = useState<SettingsTab>('email')

    // Email Settings State
    const [email, setEmail] = useState('')
    const [appPassword, setAppPassword] = useState('')
    const [smtpServer, setSmtpServer] = useState('smtp.gmail.com')
    const [smtpPort, setSmtpPort] = useState(587)
    const [isEditing, setIsEditing] = useState(false)
    const [isSaving, setIsSaving] = useState(false)
    const [isCheckingStatus, setIsCheckingStatus] = useState(false)
    const [smtpStatus, setSmtpStatus] = useState<'success' | 'error' | null>(null)
    const [testResult, setTestResult] = useState<{ status: 'success' | 'error', message: string } | null>(null)
    const [saveResult, setSaveResult] = useState<{ status: 'success' | 'error', message: string } | null>(null)

    // Email List State
    type EmailListItem = { id: number; name: string; email: string; is_active: boolean }
    const [emailList, setEmailList] = useState<EmailListItem[]>([])
    const [newEmailName, setNewEmailName] = useState('')
    const [newEmailAddress, setNewEmailAddress] = useState('')

    // API Settings State
    const [tijoriKey, setTijoriKey] = useState('')
    const [hasTijoriKey, setHasTijoriKey] = useState(false)
    const [isSavingKey, setIsSavingKey] = useState(false)
    const [isTestingKey, setIsTestingKey] = useState(false)
    const [defaultPrompt, setDefaultPrompt] = useState('')
    const [isLoadingDefaultPrompt, setIsLoadingDefaultPrompt] = useState(false)
    const [isSavingDefaultPrompt, setIsSavingDefaultPrompt] = useState(false)

    // Load existing SMTP settings and check status on mount
    useEffect(() => {
        fetchSmtpSettings()
        fetchEmailList()
        checkTijoriKeyStatus()
        fetchDefaultPrompt()
    }, [])

    // Auto-check SMTP status when email and password are loaded
    useEffect(() => {
        if (email && appPassword && !isEditing) {
            checkSmtpStatus()
        }
    }, [email, appPassword, isEditing])

    // Auto-dismiss notifications after 3 seconds
    useEffect(() => {
        if (testResult) {
            const timer = setTimeout(() => setTestResult(null), 3000)
            return () => clearTimeout(timer)
        }
    }, [testResult])

    useEffect(() => {
        if (saveResult) {
            const timer = setTimeout(() => setSaveResult(null), 3000)
            return () => clearTimeout(timer)
        }
    }, [saveResult])

    const fetchSmtpSettings = async () => {
        try {
            const response = await fetch(`${API_URL}/smtp-settings?active=true`)
            if (response.ok) {
                const settings = await response.json()
                if (settings.length > 0) {
                    const activeSetting = settings[0]
                    setEmail(activeSetting.email || '')
                    setAppPassword(activeSetting.app_password || '')
                    setSmtpServer(activeSetting.smtp_server || 'smtp.gmail.com')
                    setSmtpPort(activeSetting.smtp_port || 587)
                }
            }
        } catch (error) {
            console.error('Error fetching SMTP settings:', error)
        }
    }

    const checkSmtpStatus = async () => {
        if (!email || !appPassword) return

        setIsCheckingStatus(true)
        setSmtpStatus(null)

        try {
            const response = await fetch(`${API_URL}/smtp/test`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    email,
                    app_password: appPassword,
                    smtp_server: smtpServer,
                    smtp_port: smtpPort,
                }),
            })

            if (response.ok) {
                setSmtpStatus('success')
            } else {
                setSmtpStatus('error')
            }
        } catch (error) {
            setSmtpStatus('error')
        } finally {
            setIsCheckingStatus(false)
        }
    }

    const handleSaveSettings = async () => {
        if (!email || !appPassword) {
            setSaveResult({ status: 'error', message: 'Please enter both email and app password' })
            return
        }

        setIsSaving(true)
        setSaveResult(null)

        try {
            const response = await fetch(`${API_URL}/smtp-settings`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    email,
                    app_password: appPassword,
                    smtp_server: smtpServer,
                    smtp_port: smtpPort,
                    is_active: true,
                }),
            })

            const data = await response.json()

            if (response.ok || response.status === 409) {
                setSaveResult({ status: 'success', message: 'Settings saved successfully!' })
                setIsEditing(false)
                checkSmtpStatus()
            } else {
                setSaveResult({ status: 'error', message: data.message || 'Failed to save settings' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setIsSaving(false)
        }
    }

    // Email List Functions
    const fetchEmailList = async () => {
        try {
            const response = await fetch(`${API_URL}/emails`)
            if (response.ok) {
                const emails = await response.json()
                setEmailList(emails)
            }
        } catch (error) {
            console.error('Error fetching email list:', error)
        }
    }

    const handleAddEmail = async () => {
        if (!newEmailName || !newEmailAddress) {
            setSaveResult({ status: 'error', message: 'Please enter both name and email' })
            return
        }

        try {
            const response = await fetch(`${API_URL}/emails`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: newEmailName,
                    email: newEmailAddress,
                    is_active: true,
                }),
            })

            if (response.ok) {
                setSaveResult({ status: 'success', message: 'Email added successfully!' })
                setNewEmailName('')
                setNewEmailAddress('')
                fetchEmailList()
            } else {
                const data = await response.json()
                setSaveResult({ status: 'error', message: data.error || 'Failed to add email' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        }
    }

    const handleToggleActive = async (emailId: number, currentStatus: boolean) => {
        try {
            const response = await fetch(`${API_URL}/emails/${emailId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    is_active: !currentStatus,
                }),
            })

            if (response.ok) {
                fetchEmailList()
            }
        } catch (error) {
            console.error('Error toggling email status:', error)
        }
    }

    const handleDeleteEmail = async (emailId: number) => {
        try {
            const response = await fetch(`${API_URL}/emails/${emailId}`, {
                method: 'DELETE',
            })

            if (response.ok) {
                setSaveResult({ status: 'success', message: 'Email deleted successfully!' })
                fetchEmailList()
            }
        } catch (error) {
            console.error('Error deleting email:', error)
        }
    }

    // API Settings Functions
    const checkTijoriKeyStatus = async () => {
        try {
            const response = await fetch(`${API_URL}/keys/tijori`)
            if (response.ok) {
                const data = await response.json()
                setHasTijoriKey(data.has_key)
            }
        } catch (error) {
            console.error('Error checking Tijori key status:', error)
        }
    }

    const handleSaveTijoriKey = async () => {
        if (!tijoriKey) return

        setIsSavingKey(true)
        setSaveResult(null)

        try {
            const response = await fetch(`${API_URL}/keys`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    provider: 'tijori',
                    key: tijoriKey
                }),
            })

            if (response.ok) {
                setSaveResult({ status: 'success', message: 'API Key saved successfully!' })
                setTijoriKey('') // Clear input for security
                setHasTijoriKey(true)
            } else {
                const data = await response.json()
                setSaveResult({ status: 'error', message: data.error || 'Failed to save API key' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setIsSavingKey(false)
        }
    }

    const handleTestTijoriConnection = async () => {
        setIsTestingKey(true)
        setTestResult(null)

        try {
            const response = await fetch(`${API_URL}/keys/tijori/validate`, {
                method: 'POST',
            })

            const data = await response.json()

            if (response.ok) {
                setTestResult({ status: 'success', message: 'Connection successful! API Key is valid.' })
            } else {
                setTestResult({ status: 'error', message: data.message || 'Connection failed. Invalid API Key.' })
            }
        } catch (error) {
            setTestResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setIsTestingKey(false)
        }
    }

    const fetchDefaultPrompt = async () => {
        setIsLoadingDefaultPrompt(true)
        try {
            const response = await fetch(`${API_URL}/prompts/default`)
            if (response.ok) {
                const data = await response.json()
                setDefaultPrompt(data.prompt || '')
            }
        } catch (error) {
            console.error('Error fetching default prompt:', error)
        } finally {
            setIsLoadingDefaultPrompt(false)
        }
    }

    const handleSaveDefaultPrompt = async () => {
        if (!defaultPrompt.trim()) {
            setSaveResult({ status: 'error', message: 'Prompt cannot be empty' })
            return
        }
        setIsSavingDefaultPrompt(true)
        setSaveResult(null)
        try {
            const response = await fetch(`${API_URL}/prompts/default`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: defaultPrompt })
            })
            if (response.ok) {
                setSaveResult({ status: 'success', message: 'Default prompt updated' })
            } else {
                const data = await response.json()
                setSaveResult({ status: 'error', message: data.error || 'Failed to update prompt' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setIsSavingDefaultPrompt(false)
        }
    }

    // LLM Settings State
    const [llmProviders, setLlmProviders] = useState<any[]>([])
    const [llmSettings, setLlmSettings] = useState<any>({})
    const [llmModels, setLlmModels] = useState<any[]>([])
    const [isSyncing, setIsSyncing] = useState<string | null>(null)
    const [savingKeyProvider, setSavingKeyProvider] = useState<string | null>(null)
    const [providerKeys, setProviderKeys] = useState<{ [key: string]: string }>({})
    const [isSavingDefaultModel, setIsSavingDefaultModel] = useState(false)

    // Model Config State
    const [configuringModelId, setConfiguringModelId] = useState<number | null>(null)
    const [configMaxTokens, setConfigMaxTokens] = useState<number>(0)
    const [configThinkingEnabled, setConfigThinkingEnabled] = useState<boolean>(false)
    const [configThinkingBudget, setConfigThinkingBudget] = useState<number>(0)
    const [isSavingConfig, setIsSavingConfig] = useState(false)

    // Load LLM data on mount
    useEffect(() => {
        fetchLlmProviders()
        fetchLlmSettings()
        fetchLlmModels()
    }, [])

    const fetchLlmProviders = async () => {
        try {
            const response = await fetch(`${API_URL}/llm/providers`)
            if (response.ok) {
                const data = await response.json()
                setLlmProviders(data)
            }
        } catch (error) {
            console.error('Error fetching LLM providers:', error)
        }
    }

    const fetchLlmSettings = async () => {
        try {
            const response = await fetch(`${API_URL}/llm/settings`)
            if (response.ok) {
                const data = await response.json()
                setLlmSettings(data)
            }
        } catch (error) {
            console.error('Error fetching LLM settings:', error)
        }
    }

    const fetchLlmModels = async () => {
        try {
            const response = await fetch(`${API_URL}/llm/models`)
            if (response.ok) {
                const data = await response.json()
                setLlmModels(data)
            }
        } catch (error) {
            console.error('Error fetching LLM models:', error)
        }
    }

    const handleSaveProviderKey = async (providerName: string) => {
        const key = providerKeys[providerName]
        if (!key) return

        setSavingKeyProvider(providerName)
        setSaveResult(null)

        try {
            const response = await fetch(`${API_URL}/llm/providers/${providerName}/key`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: key })
            })

            if (response.ok) {
                setSaveResult({ status: 'success', message: 'API Key saved successfully!' })
                setProviderKeys(prev => ({ ...prev, [providerName]: '' })) // Clear input
                fetchLlmProviders() // Refresh status
            } else {
                const data = await response.json()
                setSaveResult({ status: 'error', message: data.error || 'Failed to save API key' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setSavingKeyProvider(null)
        }
    }

    const handleSyncModels = async (providerName: string) => {
        setIsSyncing(providerName)
        setSaveResult(null)

        try {
            const response = await fetch(`${API_URL}/llm/providers/${providerName}/sync`, {
                method: 'POST'
            })

            if (response.ok) {
                const data = await response.json()
                setSaveResult({ status: 'success', message: data.message })
                fetchLlmModels() // Refresh models list
            } else {
                const data = await response.json()
                setSaveResult({ status: 'error', message: data.error || 'Failed to sync models' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setIsSyncing(null)
        }
    }

    const handleSaveDefaultModel = async (modelId: string) => {
        setIsSavingDefaultModel(true)
        setSaveResult(null)

        try {
            const response = await fetch(`${API_URL}/llm/settings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ default_model_id: modelId })
            })

            if (response.ok) {
                setSaveResult({ status: 'success', message: 'Default model updated!' })
                setLlmSettings((prev: any) => ({ ...prev, default_model_id: modelId }))
            } else {
                const data = await response.json()
                setSaveResult({ status: 'error', message: data.error || 'Failed to update default model' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setIsSavingDefaultModel(false)
        }
    }

    const handleOpenConfig = (model: any) => {
        setConfiguringModelId(model.id)
        setConfigMaxTokens(model.user_max_tokens || model.max_output_tokens || 4096)
        setConfigThinkingEnabled(model.user_thinking_enabled || false)
        // If budget is 0 or null, treat as 0 (Auto)
        setConfigThinkingBudget(model.user_thinking_budget || 0)
    }

    const handleSaveConfig = async () => {
        if (!configuringModelId) return

        setIsSavingConfig(true)
        try {
            const response = await fetch(`${API_URL}/llm/models/${configuringModelId}/config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_max_tokens: configMaxTokens,
                    user_thinking_enabled: configThinkingEnabled,
                    user_thinking_budget: configThinkingBudget
                })
            })

            if (response.ok) {
                setSaveResult({ status: 'success', message: 'Model configuration saved!' })
                setConfiguringModelId(null)
                fetchLlmModels() // Refresh to get updated values
            } else {
                const data = await response.json()
                setSaveResult({ status: 'error', message: data.error || 'Failed to save config' })
            }
        } catch (error) {
            setSaveResult({ status: 'error', message: 'Failed to connect to server' })
        } finally {
            setIsSavingConfig(false)
        }
    }

    return (
        <div className="min-h-screen bg-background text-foreground transition-colors duration-300">
            {/* Sub-navigation for Settings */}
            <div className="border-b border-border">
                <div className="max-w-7xl mx-auto px-8">
                    <div className="flex justify-center gap-8 pt-4">
                        <button
                            onClick={() => setActiveTab('email')}
                            className={`text-lg font-semibold pb-4 border-b-2 transition-colors ${activeTab === 'email'
                                ? 'border-primary text-foreground'
                                : 'border-transparent text-muted-foreground hover:text-foreground'
                                }`}
                        >
                            Email Settings
                        </button>
                        <button
                            onClick={() => setActiveTab('llm')}
                            className={`text-lg font-semibold pb-4 border-b-2 transition-colors ${activeTab === 'llm'
                                ? 'border-primary text-foreground'
                                : 'border-transparent text-muted-foreground hover:text-foreground'
                                }`}
                        >
                            LLM Settings
                        </button>
                        <button
                            onClick={() => setActiveTab('api')}
                            className={`text-lg font-semibold pb-4 border-b-2 transition-colors ${activeTab === 'api'
                                ? 'border-primary text-foreground'
                                : 'border-transparent text-muted-foreground hover:text-foreground'
                                }`}
                        >
                            API Settings
                        </button>
                    </div>
                </div>
            </div>

            {/* Floating Toast Notifications */}
            <div className="fixed top-4 right-4 z-50 space-y-2">
                {testResult && (
                    <div
                        className={`flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg border animate-in slide-in-from-top-2 ${testResult.status === 'success'
                            ? 'bg-green-500/90 text-white border-green-600'
                            : 'bg-red-500/90 text-white border-red-600'
                            }`}
                    >
                        {testResult.status === 'success' ? (
                            <CheckCircle2 className="h-4 w-4" />
                        ) : (
                            <XCircle className="h-4 w-4" />
                        )}
                        <span className="text-sm font-medium">{testResult.message}</span>
                    </div>
                )}

                {saveResult && (
                    <div
                        className={`flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg border animate-in slide-in-from-top-2 ${saveResult.status === 'success'
                            ? 'bg-green-500/90 text-white border-green-600'
                            : 'bg-red-500/90 text-white border-red-600'
                            }`}
                    >
                        {saveResult.status === 'success' ? (
                            <CheckCircle2 className="h-4 w-4" />
                        ) : (
                            <XCircle className="h-4 w-4" />
                        )}
                        <span className="text-sm font-medium">{saveResult.message}</span>
                    </div>
                )}
            </div>

            {/* Tab Content */}
            <div className="max-w-7xl mx-auto px-8 py-8">
                {activeTab === 'email' && (
                    <div>
                        {/* Horizontal Form Layout with Heading */}
                        <div className="flex items-center gap-4 p-4 bg-card border border-border rounded-lg">
                            {/* Email Settings Label */}
                            <h3 className="text-lg font-semibold whitespace-nowrap">Email Settings</h3>

                            {/* Email Field */}
                            <div className="flex-1">
                                <Input
                                    id="email"
                                    type="email"
                                    placeholder="your.email@gmail.com"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    disabled={!isEditing}
                                    className="bg-secondary/50 border-border"
                                />
                            </div>

                            {/* App Password Field */}
                            <div className="flex-1">
                                <Input
                                    id="appPassword"
                                    type="password"
                                    placeholder="App password"
                                    value={appPassword}
                                    onChange={(e) => setAppPassword(e.target.value)}
                                    disabled={!isEditing}
                                    className="bg-secondary/50 border-border"
                                />
                            </div>

                            {/* Status Indicator */}
                            <div className="flex items-center gap-2">
                                {isCheckingStatus ? (
                                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                                ) : smtpStatus === 'success' ? (
                                    <CheckCircle2 className="h-5 w-5 text-green-400" />
                                ) : smtpStatus === 'error' ? (
                                    <XCircle className="h-5 w-5 text-red-400" />
                                ) : (
                                    <div className="h-5 w-5 rounded-full border-2 border-muted-foreground" />
                                )}
                            </div>

                            {/* Edit/Save Button */}
                            <Button
                                onClick={isEditing ? handleSaveSettings : () => setIsEditing(true)}
                                disabled={isSaving}
                                variant={isEditing ? "default" : "outline"}
                                className={isEditing ? "bg-primary text-primary-foreground hover:bg-primary/90" : "border-border hover:bg-accent"}
                            >
                                {isSaving ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        Saving...
                                    </>
                                ) : isEditing ? (
                                    'Save'
                                ) : (
                                    'Edit'
                                )}
                            </Button>
                        </div>

                        {/* Helper Text */}
                        <p className="text-xs text-muted-foreground mt-4">
                            For Gmail, generate an app password from your Google Account settings.
                        </p>

                        {/* Email List Section */}
                        <div className="mt-8">
                            <h4 className="text-xl font-semibold mb-4">Email List</h4>

                            {/* Add Email Form */}
                            <div className="flex items-center gap-4 p-4 bg-card border border-border rounded-lg mb-4">
                                <div className="flex-1">
                                    <Input
                                        type="text"
                                        placeholder="Name"
                                        value={newEmailName}
                                        onChange={(e) => setNewEmailName(e.target.value)}
                                        className="bg-secondary/50 border-border"
                                    />
                                </div>
                                <div className="flex-1">
                                    <Input
                                        type="email"
                                        placeholder="Email address"
                                        value={newEmailAddress}
                                        onChange={(e) => setNewEmailAddress(e.target.value)}
                                        className="bg-secondary/50 border-border"
                                    />
                                </div>
                                <Button
                                    onClick={handleAddEmail}
                                    className="bg-primary text-primary-foreground hover:bg-primary/90"
                                >
                                    Add Email
                                </Button>
                            </div>

                            {/* Email List */}
                            <div className="space-y-2">
                                {emailList.map((emailItem) => (
                                    <div
                                        key={emailItem.id}
                                        className="flex items-center gap-4 p-4 bg-card border border-border rounded-lg hover:bg-accent/50 transition-colors"
                                    >
                                        <div className="flex-1">
                                            <p className="font-medium text-foreground">{emailItem.name}</p>
                                            <p className="text-sm text-muted-foreground">{emailItem.email}</p>
                                        </div>

                                        {/* Active/Inactive Toggle */}
                                        <button
                                            onClick={() => handleToggleActive(emailItem.id, emailItem.is_active)}
                                            className="flex items-center gap-2 transition-all"
                                        >
                                            {emailItem.is_active ? (
                                                <div className="relative">
                                                    <Mail className="h-5 w-5 text-green-400 hover:scale-110 transition-transform" />
                                                    <div className="absolute -top-1 -right-1 h-2 w-2 bg-green-400 rounded-full animate-pulse" />
                                                </div>
                                            ) : (
                                                <Mail className="h-5 w-5 text-red-400 hover:scale-110 transition-transform opacity-70" />
                                            )}
                                        </button>

                                        {/* Delete Button */}
                                        <Button
                                            onClick={() => handleDeleteEmail(emailItem.id)}
                                            variant="outline"
                                            size="sm"
                                            className="border-border hover:bg-red-500/20 hover:border-red-500"
                                        >
                                            Delete
                                        </Button>
                                    </div>
                                ))}

                                {emailList.length === 0 && (
                                    <div className="text-center py-8 text-muted-foreground">
                                        No emails added yet. Add your first email above.
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
                {activeTab === 'llm' && (
                    <div className="space-y-6">
                        <div className="flex justify-between items-center">
                            <h3 className="text-2xl font-semibold">LLM Settings</h3>
                        </div>



                        <div className="grid gap-6">
                            {llmProviders.filter(p => p.provider_name !== 'anthropic').map((provider) => {
                                const providerModels = llmModels.filter(m => m.provider_name === provider.provider_name)
                                const currentDefaultModelId = llmSettings.default_model_id
                                // Check if any model from this provider is the current default
                                const isProviderActive = providerModels.some(m => m.id == currentDefaultModelId)

                                const handleToggleProvider = () => {
                                    if (isProviderActive) return // Already active

                                    if (providerModels.length > 0) {
                                        // Select the first model as default
                                        handleSaveDefaultModel(providerModels[0].id)
                                    } else {
                                        setSaveResult({ status: 'error', message: `Please sync models for ${provider.display_name} first` })
                                    }
                                }

                                return (
                                    <div key={provider.id} className={`p-6 bg-card border rounded-lg transition-all duration-300 ${isProviderActive ? 'border-green-500 shadow-[0_0_30px_rgba(34,197,94,0.15)]' : 'border-border'}`}>
                                        <div className="flex items-center justify-between mb-6">
                                            <div className="flex items-center gap-4">
                                                {/* Provider Toggle */}
                                                <button
                                                    onClick={handleToggleProvider}
                                                    className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2 ${isProviderActive ? 'bg-green-500' : 'bg-input'}`}
                                                >
                                                    <span
                                                        className={`${isProviderActive ? 'translate-x-6' : 'translate-x-1'
                                                            } inline-block h-4 w-4 transform rounded-full bg-white transition-transform duration-200`}
                                                    />
                                                </button>

                                                <h4 className="text-lg font-medium">{provider.display_name}</h4>

                                                {provider.has_key ? (
                                                    <span className="flex items-center gap-1 px-2 py-1 text-xs bg-green-500/10 text-green-500 rounded-full border border-green-500/20">
                                                        <CheckCircle2 className="h-3 w-3" />
                                                        Connected
                                                    </span>
                                                ) : (
                                                    <span className="px-2 py-1 text-xs bg-yellow-500/10 text-yellow-500 rounded-full border border-yellow-500/20">
                                                        Not Configured
                                                    </span>
                                                )}
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <Button
                                                    onClick={() => handleSyncModels(provider.provider_name)}
                                                    disabled={!provider.has_key || isSyncing === provider.provider_name}
                                                    variant="outline"
                                                    size="sm"
                                                >
                                                    {isSyncing === provider.provider_name ? (
                                                        <>
                                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                            Syncing...
                                                        </>
                                                    ) : (
                                                        'Sync Models'
                                                    )}
                                                </Button>
                                            </div>
                                        </div>

                                        <div className="space-y-4">
                                            {/* Model Selection */}
                                            <div className="flex gap-4 items-end">
                                                <div className="flex-1">
                                                    <label className="text-sm font-medium mb-2 block text-muted-foreground">Select Model</label>
                                                    <select
                                                        className={`w-full h-10 px-3 rounded-md border bg-background text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50 ${isProviderActive ? 'border-green-500/50 focus-visible:ring-green-500' : 'border-input'}`}
                                                        value={isProviderActive ? currentDefaultModelId : ''}
                                                        onChange={(e) => handleSaveDefaultModel(e.target.value)}
                                                        disabled={isSavingDefaultModel || providerModels.length === 0}
                                                    >
                                                        <option value="" disabled>Select a model...</option>
                                                        {providerModels.map((model) => (
                                                            <option key={model.id} value={model.id}>
                                                                {model.display_name}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                            </div>

                                            {/* Model Config Panel */}
                                            {isProviderActive && (
                                                <div className="mt-4 p-4 bg-secondary/30 rounded-lg border border-border">
                                                    <div className="flex items-center justify-between mb-4">
                                                        <h5 className="text-sm font-medium flex items-center gap-2">
                                                            <SettingsIcon className="h-4 w-4" />
                                                            Advanced Configuration
                                                        </h5>
                                                    </div>

                                                    {providerModels.filter(m => m.id == currentDefaultModelId).map(model => (
                                                        <div key={model.id} className="space-y-4">
                                                            <div className="grid grid-cols-2 gap-4">
                                                                <div>
                                                                    <label className="text-xs font-medium mb-1.5 block text-muted-foreground">
                                                                        Max Output Tokens
                                                                    </label>
                                                                    <div className="relative">
                                                                        <Input
                                                                            type="number"
                                                                            value={configuringModelId === model.id ? configMaxTokens : (model.user_max_tokens || model.max_output_tokens)}
                                                                            onChange={(e) => setConfigMaxTokens(parseInt(e.target.value) || 0)}
                                                                            onFocus={() => handleOpenConfig(model)}
                                                                            className="h-9 bg-background"
                                                                        />
                                                                        <div className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground pointer-events-none">
                                                                            Max: {model.max_output_tokens}
                                                                        </div>
                                                                    </div>
                                                                </div>

                                                                {model.supports_thinking && (
                                                                    <div>
                                                                        <label className="text-xs font-medium mb-1.5 block text-muted-foreground">
                                                                            Thinking Budget
                                                                        </label>
                                                                        {model.model_id.toLowerCase().includes('gemini-3') ? (
                                                                            <div className="h-9 px-3 flex items-center text-sm bg-secondary/50 rounded-md border border-input text-muted-foreground">
                                                                                Managed by Model (High)
                                                                            </div>
                                                                        ) : (
                                                                            <div className="relative">
                                                                                <style>
                                                                                    {`
                                                                                        input[type=number]::-webkit-inner-spin-button, 
                                                                                        input[type=number]::-webkit-outer-spin-button { 
                                                                                            -webkit-appearance: none; 
                                                                                            margin: 0; 
                                                                                        }
                                                                                    `}
                                                                                </style>
                                                                                <Input
                                                                                    type="number"
                                                                                    value={configuringModelId === model.id ? (configThinkingBudget === 0 ? '' : configThinkingBudget) : (model.user_thinking_budget === 0 ? '' : model.user_thinking_budget)}
                                                                                    placeholder="Auto"
                                                                                    onChange={(e) => {
                                                                                        const val = e.target.value;
                                                                                        setConfigThinkingBudget(val === '' ? 0 : parseInt(val));
                                                                                    }}
                                                                                    onFocus={() => handleOpenConfig(model)}
                                                                                    disabled={configuringModelId === model.id ? !configThinkingEnabled : !model.user_thinking_enabled}
                                                                                    className="h-9 bg-background"
                                                                                />
                                                                                <div className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground pointer-events-none">
                                                                                    Max: {model.max_output_tokens}
                                                                                </div>
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                )}
                                                            </div>

                                                            {model.supports_thinking && (
                                                                <div className="flex items-center gap-2">
                                                                    <button
                                                                        onClick={() => {
                                                                            if (configuringModelId !== model.id) handleOpenConfig(model)
                                                                            setConfigThinkingEnabled(!configThinkingEnabled)
                                                                        }}
                                                                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${(configuringModelId === model.id ? configThinkingEnabled : model.user_thinking_enabled) ? 'bg-primary' : 'bg-input'}`}
                                                                    >
                                                                        <span
                                                                            className={`${(configuringModelId === model.id ? configThinkingEnabled : model.user_thinking_enabled) ? 'translate-x-5' : 'translate-x-1'
                                                                                } inline-block h-3 w-3 transform rounded-full bg-white transition-transform duration-200`}
                                                                        />
                                                                    </button>
                                                                    <span className="text-sm">Enable Thinking Mode</span>
                                                                </div>
                                                            )}

                                                            {configuringModelId === model.id && (
                                                                <div className="flex justify-end gap-2 mt-2">
                                                                    <Button
                                                                        size="sm"
                                                                        variant="ghost"
                                                                        onClick={() => setConfiguringModelId(null)}
                                                                    >
                                                                        Cancel
                                                                    </Button>
                                                                    <Button
                                                                        size="sm"
                                                                        onClick={handleSaveConfig}
                                                                        disabled={isSavingConfig}
                                                                    >
                                                                        {isSavingConfig ? (
                                                                            <Loader2 className="h-3 w-3 animate-spin" />
                                                                        ) : (
                                                                            'Save Changes'
                                                                        )}
                                                                    </Button>
                                                                </div>
                                                            )}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}

                                            {/* API Key Input */}
                                            <div>
                                                <label className="text-sm font-medium mb-2 block text-muted-foreground">API Key</label>
                                                <div className="flex gap-4">
                                                    <div className="relative flex-1">
                                                        <Input
                                                            type="password"
                                                            placeholder={provider.has_key ? "•••••••••••••••• (Saved)" : `Enter ${provider.display_name} API Key`}
                                                            value={providerKeys[provider.provider_name] || ''}
                                                            onChange={(e) => setProviderKeys(prev => ({
                                                                ...prev,
                                                                [provider.provider_name]: e.target.value
                                                            }))}
                                                            className={`bg-secondary/50 ${provider.has_key ? 'border-green-500/50 focus-visible:ring-green-500' : ''}`}
                                                        />
                                                        {provider.has_key && !providerKeys[provider.provider_name] && (
                                                            <div className="absolute right-3 top-1/2 -translate-y-1/2 text-green-500 pointer-events-none">
                                                                <CheckCircle2 className="h-4 w-4" />
                                                            </div>
                                                        )}
                                                    </div>
                                                    <Button
                                                        onClick={() => handleSaveProviderKey(provider.provider_name)}
                                                        disabled={!providerKeys[provider.provider_name] || savingKeyProvider === provider.provider_name}
                                                    >
                                                        {savingKeyProvider === provider.provider_name ? (
                                                            <Loader2 className="h-4 w-4 animate-spin" />
                                                        ) : (
                                                            'Save Key'
                                                        )}
                                                    </Button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )
                            })}
                        </div>
                    </div>
                )}
                {activeTab === 'api' && (
                    <div>
                        <div className="space-y-6">
                            <div className="p-4 bg-card border border-border rounded-lg flex flex-col gap-3">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <h4 className="font-medium mb-1">Default Analysis Prompt</h4>
                                        <p className="text-sm text-muted-foreground">
                                            Used when a stock is not in any group (or group prompt is empty).
                                        </p>
                                    </div>
                                    {isLoadingDefaultPrompt && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                                </div>
                                <textarea
                                    value={defaultPrompt}
                                    onChange={(e) => setDefaultPrompt(e.target.value)}
                                    className="w-full min-h-[180px] rounded-md bg-secondary/50 border border-border p-3 text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
                                    placeholder="Enter the default system prompt used for analysis..."
                                />
                                <div className="flex justify-end">
                                    <Button
                                        onClick={handleSaveDefaultPrompt}
                                        disabled={isSavingDefaultPrompt}
                                        className="bg-primary text-primary-foreground hover:bg-primary/90"
                                    >
                                        {isSavingDefaultPrompt ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Saving...
                                            </>
                                        ) : (
                                            'Save Prompt'
                                        )}
                                    </Button>
                                </div>
                            </div>
                            <div className="flex items-center gap-4 p-4 bg-card border border-border rounded-lg">
                                <div className="flex-1">
                                    <h4 className="font-medium mb-1">Tijori API Key</h4>
                                    <p className="text-sm text-muted-foreground mb-2">
                                        Required for fetching transcript data.
                                    </p>
                                    <Input
                                        type="password"
                                        placeholder={hasTijoriKey ? "••••••••••••••••" : "Enter your Tijori API Key"}
                                        value={tijoriKey}
                                        onChange={(e) => setTijoriKey(e.target.value)}
                                        className="bg-secondary/50 border-border"
                                    />
                                </div>
                                <div className="flex flex-col gap-2">
                                    <Button
                                        onClick={handleSaveTijoriKey}
                                        disabled={isSavingKey || !tijoriKey}
                                        className="bg-primary text-primary-foreground hover:bg-primary/90"
                                    >
                                        {isSavingKey ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Saving...
                                            </>
                                        ) : (
                                            'Save Key'
                                        )}
                                    </Button>
                                    <Button
                                        onClick={handleTestTijoriConnection}
                                        disabled={isTestingKey || (!hasTijoriKey && !tijoriKey)}
                                        variant="outline"
                                        className="border-border hover:bg-accent"
                                    >
                                        {isTestingKey ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Testing...
                                            </>
                                        ) : (
                                            'Test Connection'
                                        )}
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
