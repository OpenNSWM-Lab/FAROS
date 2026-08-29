import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { REVIEW_LOCALE_STORAGE_KEY } from '@/lib/reviewLocale'
import { SeedSuggestionList } from './SeedSuggestionList'

const suggestions = [
  {
    titleZh: '可验证的多智能体科研规划',
    titleEn: 'Verifiable multi-agent research planning',
    query: 'Evidence-grounded multi-agent planning for scientific hypothesis generation evaluated by citation faithfulness and expert preference',
    rationaleZh: '主题包含任务、方法和评估指标。',
    rationaleEn: 'The topic includes a task, method, and evaluation targets.',
  },
  {
    titleZh: '可信检索增强生成',
    titleEn: 'Trustworthy retrieval-augmented generation',
    query: 'Citation-aware retrieval augmented generation for scientific question answering evaluated on attribution precision and answer correctness',
  },
]

describe('SeedSuggestionList', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, 'zh-CN')
  })

  it('lets a novice apply one Qwen recommendation directly', () => {
    const onSelect = vi.fn()
    render(<SeedSuggestionList suggestions={suggestions} model="qwen-plus" onSelect={onSelect} />)

    expect(screen.getByText('千问推荐的可用研究主题')).toBeInTheDocument()
    expect(screen.getByText(/可验证的多智能体科研规划/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '采用主题 1' }))
    expect(onSelect).toHaveBeenCalledWith(suggestions[0].query)
  })
})
