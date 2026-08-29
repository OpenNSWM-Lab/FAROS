import { useState, useEffect, useCallback } from 'react'
import { SettingsLayout } from '@/components/layout/SettingsLayout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  CheckCircle2, XCircle, Eye, EyeOff, Zap, Loader2,
  Globe, Brain, Sparkles, Bot, Cloud, Cpu, Shield, Server, Trash2, UserRound,
} from 'lucide-react'
import { LLM_PROVIDERS, getModelsByProvider } from '@/lib/models/providers'
import { useReviewLocale } from '@/lib/reviewLocale'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''
const LEGACY_PROVIDER_ALIASES = new Set(['kimi', 'claude', 'bigmodel'])

/* ── Provider icon map ── */
const PROVIDER_ICONS: Record<string, typeof Brain> = {
  openai: Brain,
  anthropic: Shield,
  claude: Shield,
  bigmodel: Sparkles,
  moonshot: Globe,
  deepseek: Cpu,
  zhipu: Bot,
  qwen: Cloud,
  minimax: Zap,
  mistral: Server,
}

/* ── Types ── */
interface ProviderStatus {
  providerName: string
  model: string
  baseUrl: string
  configured: boolean
  apiKeySet: boolean
  apiKeyMasked: string | null
  timeout: number
}

interface TestResult {
  ok: boolean
  providerName: string
  model: string
  latencyMs: number
  text?: string
  error?: string
}

type TestState = 'idle' | 'testing' | 'success' | 'error'

export function LLMProviders() {
  const { text } = useReviewLocale()

  // Global state
  const [activeProvider, setActiveProvider] = useState('')
  const [activeModel, setActiveModel] = useState('')
  const [backendProviders, setBackendProviders] = useState<ProviderStatus[]>([])
  const [currentUser, setCurrentUser] = useState('local')
  const [currentRole, setCurrentRole] = useState('user')
  const [loading, setLoading] = useState(true)

  // Per-provider local state
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({})
  const [baseUrlInputs, setBaseUrlInputs] = useState<Record<string, string>>({})
  const [modelInputs, setModelInputs] = useState<Record<string, string>>({})
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({})
  const [testStates, setTestStates] = useState<Record<string, TestState>>({})
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({})
  const [saving, setSaving] = useState<Record<string, boolean>>({})

  // Toast
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)
  const showToast = (type: 'success' | 'error', msg: string) => {
    setToast({ type, msg })
    setTimeout(() => setToast(null), 3500)
  }

  // Load provider state from backend
  const loadProviders = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/providers`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      setActiveProvider(data.activeProvider || 'bigmodel')
      setBackendProviders(data.providers || [])
      setCurrentUser(data.userId || 'local')
      setCurrentRole(data.role || 'user')
      // Set active model from backend
      const activeProv = (data.providers || []).find(
        (p: ProviderStatus) => p.providerName === data.activeProvider
      )
      if (activeProv?.model) setActiveModel(activeProv.model)
    } catch (err) {
      console.error('Failed to load providers:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadProviders() }, [loadProviders])

  // Save provider config (api key / base url / model)
  const handleSaveConfig = useCallback(async (provName: string, opts?: { makeActive?: boolean }) => {
    const payload: Record<string, unknown> = { providerName: provName }
    const apiKey = keyInputs[provName]?.trim()
    const baseUrl = baseUrlInputs[provName]?.trim()
    const model = modelInputs[provName]?.trim()

    if (apiKey) payload.apiKey = apiKey
    if (baseUrl) payload.baseUrl = baseUrl
    if (model) payload.model = model
    if (opts?.makeActive) payload.makeActive = true

    if (Object.keys(payload).length === 1) return

    setSaving(s => ({ ...s, [provName]: true }))
    try {
      const r = await fetch(`${API_BASE}/api/v1/providers/set-config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const d = await r.json()
      if (!r.ok || !d.ok) {
        showToast('error', d?.detail || d?.message || 'Failed to save provider config')
        return
      }
      showToast('success', `Config saved for ${provName}`)
      setKeyInputs(s => ({ ...s, [provName]: '' }))
      await loadProviders()
    } catch {
      showToast('error', 'Network error saving provider config')
    } finally {
      setSaving(s => ({ ...s, [provName]: false }))
    }
  }, [baseUrlInputs, keyInputs, modelInputs, loadProviders])

  // Test a provider
  const handleTest = useCallback(async (provName: string) => {
    setTestStates(s => ({ ...s, [provName]: 'testing' }))
    // Save any pending config first
    await handleSaveConfig(provName)
    try {
      const model = provName === activeProvider ? activeModel : undefined
      const r = await fetch(`${API_BASE}/api/v1/providers/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ providerName: provName, model, prompt: 'Say OK', maxTokens: 32 }),
      })
      const result: TestResult = await r.json()
      setTestResults(s => ({ ...s, [provName]: result }))
      setTestStates(s => ({ ...s, [provName]: result.ok ? 'success' : 'error' }))
      if (result.ok) await loadProviders()
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Network error'
      setTestStates(s => ({ ...s, [provName]: 'error' }))
      setTestResults(s => ({
        ...s,
        [provName]: { ok: false, providerName: provName, model: '', latencyMs: 0, error: message },
      }))
    }
  }, [activeProvider, activeModel, handleSaveConfig, loadProviders])

  // Set active provider + model
  const handleSetActive = useCallback(async (provName: string, model?: string) => {
    try {
      await fetch(`${API_BASE}/api/v1/providers/set-active`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ providerName: provName, model }),
      })
      setActiveProvider(provName)
      if (model) setActiveModel(model)
      showToast('success', `Active provider: ${provName}`)
      await loadProviders()
    } catch {
      showToast('error', 'Failed to set active provider')
    }
  }, [loadProviders])

  const handleRemoveKey = useCallback(async (provName: string) => {
    const confirmed = window.confirm(
      text(
        `确认删除账号 ${currentUser} 的 ${provName} API Key？`,
        `Remove the ${provName} API key for account ${currentUser}?`,
      ),
    )
    if (!confirmed) return

    setSaving(s => ({ ...s, [provName]: true }))
    try {
      const r = await fetch(`${API_BASE}/api/v1/providers/${provName}/key`, {
        method: 'DELETE',
      })
      const data = await r.json()
      if (!r.ok || !data.ok) {
        showToast('error', data?.detail || data?.message || text('删除失败', 'Failed to remove key'))
        return
      }
      showToast('success', text(`已删除 ${provName} Key`, `${provName} key removed`))
      await loadProviders()
    } catch {
      showToast('error', text('删除 Key 时网络错误', 'Network error removing key'))
    } finally {
      setSaving(s => ({ ...s, [provName]: false }))
    }
  }, [currentUser, loadProviders, text])

  // Provider list from frontend registry (LLM_PROVIDERS) merged with backend status
  const providerCards = LLM_PROVIDERS.filter((provider) => {
    const backend = backendProviders.find(item => item.providerName === provider.id)
    if (!backend) return false
    return !LEGACY_PROVIDER_ALIASES.has(provider.id)
      || provider.id === activeProvider
      || backend.apiKeySet
  }).map(fp => {
    const bp = backendProviders.find(b => b.providerName === fp.id)
    return { ...fp, backend: bp }
  })

  // Also add any backend-only providers not in LLM_PROVIDERS
  backendProviders.forEach(bp => {
    if (LEGACY_PROVIDER_ALIASES.has(bp.providerName) && bp.providerName !== activeProvider && !bp.apiKeySet) return
    if (!providerCards.find(pc => pc.id === bp.providerName)) {
      providerCards.push({
        id: bp.providerName,
        name: bp.providerName,
        models: [{ id: bp.model, name: bp.model, provider: bp.providerName, contextWindow: 0 }],
        backend: bp,
      })
    }
  })
  providerCards.sort((left, right) => {
    const activeOrder = Number(right.id === activeProvider) - Number(left.id === activeProvider)
    if (activeOrder !== 0) return activeOrder
    return Number(Boolean(right.backend?.apiKeySet)) - Number(Boolean(left.backend?.apiKeySet))
  })

  const registeredActiveModels = getModelsByProvider(activeProvider)
  const activeModelOptions = activeModel && !registeredActiveModels.some(model => model.id === activeModel)
    ? [
        {
          id: activeModel,
          name: `${activeModel} (custom)`,
          provider: activeProvider,
          contextWindow: 0,
        },
        ...registeredActiveModels,
      ]
    : registeredActiveModels

  if (loading) {
    return (
      <SettingsLayout>
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </SettingsLayout>
    )
  }

  return (
    <SettingsLayout>
      <div className="space-y-6">
        {/* Toast */}
        {toast && (
          <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-md shadow-lg flex items-center gap-2 animate-in slide-in-from-top ${toast.type === 'success' ? 'bg-primary text-primary-foreground' : 'bg-destructive text-destructive-foreground'}`}>
            {toast.type === 'success' ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span className="text-sm font-medium">{toast.msg}</span>
          </div>
        )}

        {/* Header */}
        <div>
          <h1 className="text-3xl font-bold font-display">{text('LLM Provider 设置', 'LLM Providers')}</h1>
          <p className="text-muted-foreground mt-1">
            {text(
              '配置 API Key、选择模型并测试连接。常见模型与 API 名称保留英文。',
              'Configure API keys, select models, and test connections.',
            )}
          </p>
          <div className="mt-3 flex items-center gap-2 border-l-2 border-primary pl-3 text-sm">
            <UserRound className="h-4 w-4 text-primary" />
            <span className="font-medium">{currentUser}</span>
            <Badge variant="outline" className="text-xs">{currentRole}</Badge>
            <span className="text-muted-foreground">
              {text(
                '此页保存的模型设置和 API Key 仅属于当前账号。',
                'Provider settings and API keys on this page belong only to this account.',
              )}
            </span>
          </div>
        </div>

        {/* ── Global Active Provider Selector ── */}
        <Card className="border-primary/30 bg-primary/5">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2">
              <Zap className="h-5 w-5 text-primary" />
              {text('当前 Provider 与模型', 'Active Provider & Model')}
            </CardTitle>
            <CardDescription>{text('当前账号的所有 Agent 调用均使用此配置', 'Used by all agents for this account')}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Provider</label>
                <select
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={activeProvider}
                  onChange={e => {
                    const prov = e.target.value
                    const models = getModelsByProvider(prov)
                    const firstModel = models.length ? models[0].id : ''
                    handleSetActive(prov, firstModel)
                  }}
                >
                  {providerCards.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Model</label>
                <select
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={activeModel}
                  onChange={e => handleSetActive(activeProvider, e.target.value)}
                >
                  {activeModelOptions.map(m => (
                    <option key={m.id} value={m.id}>{m.name}</option>
                  ))}
                </select>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ── Provider Cards Grid ── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {providerCards.map(prov => {
            const Icon = PROVIDER_ICONS[prov.id] || Brain
            const bp = prov.backend
            const isActive = prov.id === activeProvider
            const tState = testStates[prov.id] || 'idle'
            const tResult = testResults[prov.id]
            const isSaving = saving[prov.id] || false
            const models = getModelsByProvider(prov.id)
            const currentModel = bp?.model || models[0]?.id || ''
            const modelOptions = models.some(m => m.id === currentModel)
              ? models
              : [{ id: currentModel, name: `${currentModel} (custom)`, provider: prov.id, contextWindow: 0 }, ...models]

            return (
              <Card
                key={prov.id}
                className={`transition-all ${isActive ? 'ring-2 ring-primary border-primary/40' : 'hover:border-primary/20'}`}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Icon className="h-5 w-5" />
                      {prov.name}
                    </CardTitle>
                    <div className="flex items-center gap-2">
                      {isActive && <Badge variant="default" className="text-xs">{text('当前使用', 'Active')}</Badge>}
                      {bp?.apiKeySet && <Badge variant="outline" className="text-xs text-green-600 border-green-300">{text('Key 已配置', 'Key Set')}</Badge>}
                    </div>
                  </div>
                  {bp?.apiKeyMasked && (
                    <p className="text-xs text-muted-foreground font-mono">Key: {bp.apiKeyMasked}</p>
                  )}
                </CardHeader>
                <CardContent className="space-y-3">
                  {/* API Key Input */}
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">API Key</label>
                    <div className="flex gap-2">
                      <div className="relative flex-1">
                        <input
                          type={showKeys[prov.id] ? 'text' : 'password'}
                          autoComplete="off"
                          placeholder={bp?.apiKeySet ? text('输入新 Key 以替换', 'Enter new key to replace') : text('输入 API Key', 'Enter API key')}
                          value={keyInputs[prov.id] || ''}
                          onChange={e => setKeyInputs(s => ({ ...s, [prov.id]: e.target.value }))}
                          className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm pr-8"
                        />
                        <button
                          type="button"
                          onClick={() => setShowKeys(s => ({ ...s, [prov.id]: !s[prov.id] }))}
                          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                        >
                          {showKeys[prov.id] ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                        </button>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleSaveConfig(prov.id)}
                        disabled={!keyInputs[prov.id]?.trim() || isSaving}
                      >
                        {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : text('保存', 'Save')}
                      </Button>
                      {bp?.apiKeySet && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleRemoveKey(prov.id)}
                          disabled={isSaving}
                          aria-label={text(`删除 ${prov.name} API Key`, `Remove ${prov.name} API key`)}
                          title={text('删除当前账号的 Key', 'Remove this account\'s key')}
                          className="h-8 w-8 text-muted-foreground hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  </div>

                  {/* Base URL (editable) */}
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">Base URL</label>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        placeholder={bp?.baseUrl || 'https://api.openai.com/v1'}
                        value={baseUrlInputs[prov.id] || ''}
                        onChange={e => setBaseUrlInputs(s => ({ ...s, [prov.id]: e.target.value }))}
                        className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-xs font-mono"
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleSaveConfig(prov.id)}
                        disabled={!baseUrlInputs[prov.id]?.trim() || isSaving}
                      >
                        {text('保存', 'Save')}
                      </Button>
                    </div>
                    {bp?.baseUrl && (
                      <p className="text-[11px] text-muted-foreground truncate">{text('当前', 'Current')}: <span className="font-mono">{bp.baseUrl}</span></p>
                    )}
                  </div>

                  {/* Default Model selector */}
                  {models.length > 0 && (
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted-foreground">Default Model</label>
                      <select
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs"
                        value={currentModel}
                        onChange={e => {
                          if (isActive) handleSetActive(prov.id, e.target.value)
                          setModelInputs(s => ({ ...s, [prov.id]: e.target.value }))
                        }}
                      >
                        {modelOptions.map(m => (
                          <option key={m.id} value={m.id}>{m.name}</option>
                        ))}
                      </select>
                      <div className="flex gap-2 mt-2">
                        <input
                          type="text"
                          placeholder="Custom model (e.g. gpt-4.1-mini)"
                          value={modelInputs[prov.id] || ''}
                          onChange={e => setModelInputs(s => ({ ...s, [prov.id]: e.target.value }))}
                          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs"
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleSaveConfig(prov.id, { makeActive: isActive })}
                          disabled={!modelInputs[prov.id]?.trim() || isSaving}
                        >
                          {text('保存', 'Save')}
                        </Button>
                      </div>
                    </div>
                  )}

                  {/* Test Provider */}
                  <div className="flex items-center gap-2 pt-1">
                    <Button
                      size="sm"
                      variant={tState === 'success' ? 'outline' : 'default'}
                      onClick={() => handleTest(prov.id)}
                      disabled={tState === 'testing' || (!bp?.apiKeySet && !keyInputs[prov.id]?.trim())}
                      className="text-xs"
                    >
                      {tState === 'testing' ? (
                        <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> {text('测试中...', 'Testing...')}</>
                      ) : (
                        text('测试 Provider', 'Test Provider')
                      )}
                    </Button>
                    {!isActive && bp?.apiKeySet && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleSetActive(prov.id)}
                        className="text-xs"
                      >
                        {text('设为当前 Provider', 'Set as Active')}
                      </Button>
                    )}
                  </div>

                  {/* Test Result Area */}
                  {tState !== 'idle' && tState !== 'testing' && tResult && (
                    <div className={`rounded-md p-2.5 text-xs space-y-1 ${tResult.ok ? 'bg-green-50 dark:bg-green-950/20 border border-green-200 dark:border-green-900' : 'bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-900'}`}>
                      <div className="flex items-center gap-1.5">
                        {tResult.ok ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5 text-red-600" />
                        )}
                        <span className={`font-semibold ${tResult.ok ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
                          {tResult.ok ? 'OK' : 'FAILED'}
                        </span>
                        {tResult.latencyMs > 0 && (
                          <span className="text-muted-foreground ml-auto">{tResult.latencyMs}ms</span>
                        )}
                      </div>
                      {tResult.model && (
                        <div className="text-muted-foreground">Model: <span className="font-mono">{tResult.model}</span></div>
                      )}
                      {tResult.error && (
                        <div className="text-red-600 dark:text-red-400 break-words">{tResult.error}</div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      </div>
    </SettingsLayout>
  )
}
