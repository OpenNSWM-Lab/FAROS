/**
 * LLM Provider and Model Configuration
 * 
 * Centralized configuration for all supported LLM providers and models.
 * Used by frontend UI for model selection dropdowns.
 */

export interface ModelInfo {
  id: string
  name: string
  provider: string
  contextWindow: number
  description?: string
}

export interface ProviderInfo {
  id: string
  name: string
  models: ModelInfo[]
}

/**
 * All supported LLM providers and their models
 */
export const LLM_PROVIDERS: ProviderInfo[] = [
  {
    id: 'moonshot',
    name: 'Moonshot (月之暗面)',
    models: [
      {
        id: 'moonshot-v1-8k',
        name: 'Moonshot v1 8K',
        provider: 'moonshot',
        contextWindow: 8000,
        description: '高性能中文模型，8K上下文'
      },
      {
        id: 'moonshot-v1-32k',
        name: 'Moonshot v1 32K',
        provider: 'moonshot',
        contextWindow: 32000,
        description: '高性能中文模型，32K上下文'
      },
      {
        id: 'moonshot-v1-128k',
        name: 'Moonshot v1 128K',
        provider: 'moonshot',
        contextWindow: 128000,
        description: '高性能中文模型，128K上下文'
      }
    ]
  },
  {
    id: 'openai',
    name: 'OpenAI',
    models: [
      {
        id: 'gpt-4o-2024-08-06',
        name: 'GPT-4o (2024-08-06)',
        provider: 'openai',
        contextWindow: 128000,
        description: 'Latest GPT-4o model with vision'
      },
      {
        id: 'gpt-4o',
        name: 'GPT-4o',
        provider: 'openai',
        contextWindow: 128000,
        description: 'GPT-4 optimized model'
      },
      {
        id: 'gpt-4o-mini',
        name: 'GPT-4o Mini',
        provider: 'openai',
        contextWindow: 128000,
        description: 'Faster, cheaper GPT-4o variant'
      },
      {
        id: 'gpt-4-turbo',
        name: 'GPT-4 Turbo',
        provider: 'openai',
        contextWindow: 128000,
        description: 'GPT-4 Turbo with vision'
      },
      {
        id: 'gpt-4',
        name: 'GPT-4',
        provider: 'openai',
        contextWindow: 8192,
        description: 'Original GPT-4 model'
      },
      {
        id: 'gpt-3.5-turbo',
        name: 'GPT-3.5 Turbo',
        provider: 'openai',
        contextWindow: 16385,
        description: 'Fast and efficient model'
      }
    ]
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    models: [
      {
        id: 'claude-3-5-sonnet-20241022',
        name: 'Claude 3.5 Sonnet (Latest)',
        provider: 'anthropic',
        contextWindow: 200000,
        description: 'Most capable Claude model'
      },
      {
        id: 'claude-3-opus-20240229',
        name: 'Claude 3 Opus',
        provider: 'anthropic',
        contextWindow: 200000,
        description: 'Most powerful Claude 3 model'
      },
      {
        id: 'claude-3-sonnet-20240229',
        name: 'Claude 3 Sonnet',
        provider: 'anthropic',
        contextWindow: 200000,
        description: 'Balanced performance and speed'
      },
      {
        id: 'claude-3-haiku-20240307',
        name: 'Claude 3 Haiku',
        provider: 'anthropic',
        contextWindow: 200000,
        description: 'Fastest Claude 3 model'
      }
    ]
  },
  {
    id: 'claude',
    name: 'Claude',
    models: [
      {
        id: 'claude-3-5-sonnet-20241022',
        name: 'Claude 3.5 Sonnet (Latest)',
        provider: 'claude',
        contextWindow: 200000,
        description: 'Most capable Claude model'
      },
      {
        id: 'claude-3-opus-20240229',
        name: 'Claude 3 Opus',
        provider: 'claude',
        contextWindow: 200000,
        description: 'Most powerful Claude 3 model'
      },
      {
        id: 'claude-3-sonnet-20240229',
        name: 'Claude 3 Sonnet',
        provider: 'claude',
        contextWindow: 200000,
        description: 'Balanced performance and speed'
      },
      {
        id: 'claude-3-haiku-20240307',
        name: 'Claude 3 Haiku',
        provider: 'claude',
        contextWindow: 200000,
        description: 'Fastest Claude 3 model'
      }
    ]
  },
  {
    id: 'minimax',
    name: 'MiniMax',
    models: [
      {
        id: 'MiniMax-M2.5',
        name: 'MiniMax-M2.5',
        provider: 'minimax',
        contextWindow: 204800,
        description: 'Anthropic-compatible MiniMax reasoning model'
      }
    ]
  },
  {
    id: 'deepseek',
    name: 'DeepSeek (深度求索)',
    models: [
      {
        id: 'deepseek-chat',
        name: 'DeepSeek Chat',
        provider: 'deepseek',
        contextWindow: 32000,
        description: '通用对话模型'
      },
      {
        id: 'deepseek-coder',
        name: 'DeepSeek Coder',
        provider: 'deepseek',
        contextWindow: 16000,
        description: '代码专用模型'
      }
    ]
  },
  {
    id: 'zhipu',
    name: 'Zhipu AI (智谱)',
    models: [
      {
        id: 'glm-4',
        name: 'GLM-4',
        provider: 'zhipu',
        contextWindow: 128000,
        description: '最新GLM-4模型'
      },
      {
        id: 'glm-4-air',
        name: 'GLM-4 Air',
        provider: 'zhipu',
        contextWindow: 128000,
        description: '轻量级GLM-4'
      },
      {
        id: 'glm-3-turbo',
        name: 'GLM-3 Turbo',
        provider: 'zhipu',
        contextWindow: 128000,
        description: 'GLM-3高速版本'
      }
    ]
  },
  {
    id: 'qwen',
    name: 'Qwen (通义千问)',
    models: [
      {
        id: 'qwen-max',
        name: 'Qwen Max',
        provider: 'qwen',
        contextWindow: 30000,
        description: '最强通义千问模型'
      },
      {
        id: 'qwen-plus',
        name: 'Qwen Plus',
        provider: 'qwen',
        contextWindow: 30000,
        description: '平衡性能模型'
      },
      {
        id: 'qwen-turbo',
        name: 'Qwen Turbo',
        provider: 'qwen',
        contextWindow: 8000,
        description: '快速响应模型'
      }
    ]
  },
  {
    id: 'mistral',
    name: 'Mistral AI',
    models: [
      {
        id: 'mistral-large-latest',
        name: 'Mistral Large',
        provider: 'mistral',
        contextWindow: 128000,
        description: 'Flagship Mistral model'
      },
      {
        id: 'mistral-medium-latest',
        name: 'Mistral Medium',
        provider: 'mistral',
        contextWindow: 32000,
        description: 'Balanced Mistral model'
      },
      {
        id: 'mistral-small-latest',
        name: 'Mistral Small',
        provider: 'mistral',
        contextWindow: 32000,
        description: 'Efficient Mistral model'
      }
    ]
  }
]

/**
 * Task levels configuration
 */
export const TASK_LEVELS = [
  { id: 'task1', name: 'Task 1 - Basic', description: '基础任务' },
  { id: 'task2', name: 'Task 2 - Intermediate', description: '中级任务' },
  { id: 'task3', name: 'Task 3 - Advanced', description: '高级任务' },
  { id: 'task4', name: 'Task 4 - Expert', description: '专家任务' },
  { id: 'custom', name: 'Custom', description: '自定义任务' }
]

/**
 * Get all models across all providers
 */
export function getAllModels(): ModelInfo[] {
  return LLM_PROVIDERS.flatMap(provider => provider.models)
}

/**
 * Get model by ID
 */
export function getModelById(modelId: string): ModelInfo | undefined {
  return getAllModels().find(model => model.id === modelId)
}

/**
 * Get provider by ID
 */
export function getProviderById(providerId: string): ProviderInfo | undefined {
  return LLM_PROVIDERS.find(provider => provider.id === providerId)
}

/**
 * Get models for a specific provider
 */
export function getModelsByProvider(providerId: string): ModelInfo[] {
  const provider = getProviderById(providerId)
  return provider ? provider.models : []
}

/**
 * Paper Type Configuration
 * Defines what kind of research paper the idea targets
 */
export interface PaperTypeInfo {
  id: string
  name: string
  description: string
  emphasis: string[]  // What to emphasize in literature search
}

export const PAPER_TYPES: PaperTypeInfo[] = [
  {
    id: 'algorithm',
    name: 'Algorithm/Method',
    description: 'New algorithm, method, or technique',
    emphasis: ['methodology', 'baselines', 'ablations']
  },
  {
    id: 'system',
    name: 'System/Engineering',
    description: 'Systems design, infrastructure, or engineering contribution',
    emphasis: ['architecture', 'scalability', 'deployment']
  },
  {
    id: 'application',
    name: 'Application',
    description: 'Applied research in a specific domain',
    emphasis: ['domain expertise', 'real-world impact', 'case studies']
  },
  {
    id: 'benchmark',
    name: 'Benchmark/Dataset',
    description: 'New tasks, datasets, evaluation protocols, metrics, baselines',
    emphasis: ['data collection', 'annotation', 'baseline results']
  },
  {
    id: 'survey',
    name: 'Survey/Overview',
    description: 'Taxonomy, synthesis of existing work, gaps, future directions',
    emphasis: ['comprehensive coverage', 'categorization', 'trends']
  },
  {
    id: 'position',
    name: 'Position/Opinion',
    description: 'Position paper, opinion piece, or perspective',
    emphasis: ['argumentation', 'vision', 'community impact']
  },
  {
    id: 'theory',
    name: 'Theoretical Analysis',
    description: 'Theoretical foundations, proofs, or formal analysis',
    emphasis: ['mathematical rigor', 'proofs', 'bounds']
  },
  {
    id: 'evaluation',
    name: 'Evaluation Methodology',
    description: 'New evaluation methods, metrics, or protocols',
    emphasis: ['validity', 'reliability', 'reproducibility']
  },
  {
    id: 'reproducibility',
    name: 'Reproducibility/Tooling',
    description: 'Reproducibility studies, tools, or frameworks',
    emphasis: ['replication', 'open source', 'documentation']
  },
  {
    id: 'safety',
    name: 'Safety/Alignment',
    description: 'AI safety, alignment, security, or ethics',
    emphasis: ['risk assessment', 'mitigation', 'responsible AI']
  }
]

/**
 * Get paper type by ID
 */
export function getPaperTypeById(paperTypeId: string): PaperTypeInfo | undefined {
  return PAPER_TYPES.find(pt => pt.id === paperTypeId)
}

/**
 * Paper Type V2 - for Plan Generation
 */
export interface PaperTypeV2Info {
  id: string
  label: string
  description: string
}

export const PAPER_TYPES_V2: PaperTypeV2Info[] = [
  { id: 'algorithmic_method', label: 'Algorithmic Method', description: 'New algorithm, method, or technique' },
  { id: 'systems_infrastructure', label: 'Systems / Infrastructure', description: 'Systems design, infrastructure, or engineering' },
  { id: 'application_domain', label: 'Application / Domain', description: 'Applied research in a specific domain' },
  { id: 'survey_tutorial', label: 'Survey / Tutorial', description: 'Taxonomy, synthesis, future directions' },
  { id: 'benchmark_dataset', label: 'Benchmark / Dataset', description: 'New tasks, datasets, evaluation protocols' },
  { id: 'evaluation_metrics', label: 'Evaluation / Metrics', description: 'New evaluation methods or metrics' },
  { id: 'security_robustness', label: 'Security / Robustness', description: 'AI safety, security, robustness' },
  { id: 'theory_analysis', label: 'Theory / Analysis', description: 'Theoretical foundations, proofs, formal analysis' },
  { id: 'multimodal_agent', label: 'Multimodal / Agent', description: 'Multimodal models, agent systems' },
]

export function getPaperTypeV2ById(id: string): PaperTypeV2Info | undefined {
  return PAPER_TYPES_V2.find(pt => pt.id === id)
}
