import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SignoffDossier } from './SignoffDossier'

const payload = {
  schemaVersion: 'reviewx-signoff-dossier/v1', release: 'draft', watermark: 'DRAFT_NOT_HUMAN_APPROVED', generatedAt: 'now', contentHash: 'sha256:1234567890',
  subject: { feedbackId: 'feedback-1', runId: 'run-1', researchSeriesId: 'series-1', scientificQuestion: '<script>question</script>', planPackageId: 'plan-1', iterationNumber: 2, artifactHash: 'sha256:artifact' },
  executiveDecision: { iterationDecision: 'accept_results', qualityGate: 'pass', publicationReady: false, blockingReasons: [{ code: 'CONCLUSION_SIGNOFF_REQUIRED', message: '结论尚未签核', nextStep: '完成结论确认' }] },
  plan: { hypothesis: 'h', baseline: 'old', intervention: 'new', primaryMetric: 'f1', guardrails: [], stopConditions: [], delta: { changedSections: ['sampling'], parameterChanges: [{ field: 'samples', oldValue: 80, newValue: 80, rationale: 'matched budget', targetNode: 'experiment' }], evidenceReferences: [] } },
  evidence: { dataSource: ['simulation'], dataSplitPolicy: 'frozen holdout', metrics: [
    { name: 'f1', direction: 'maximize', baseline: 0.7, current: 0.71, delta: 0.01, ciLower: -0.02, ciUpper: 0.03, decision: 'BOUNDARY', interpretation: '方向一致但统计不确定 / Directionally consistent but statistically uncertain', role: 'primary', split: 'holdout', sourceArtifactId: 'artifact-1', source: 'record.metricSnapshot' },
    { name: 'accuracy', direction: 'maximize', baseline: 0.8, current: 0.79, delta: -0.01, ciLower: -0.02, ciUpper: 0, decision: 'BOUNDARY', role: 'guardrail', split: 'holdout', sourceArtifactId: 'artifact-1', source: 'record.metricSnapshot' },
  ] },
  review: { findingCounts: {}, findings: [], humanFeedback: {}, acceptanceConditions: {} }, limitations: ["<img src=x onerror=alert('xss')>"],
  provenance: { sourceArtifacts: { evidence: 'artifact-1' }, benchmarkFingerprint: 'sha256:bench', qwenCalls: [], auditIntegrity: { valid: true, eventCount: 0 } },
  signoffs: { plan: { status: 'approved', reviewerName: '<b>Reviewer</b>', actorAccountId: 'signer', authAssurance: 'trusted_proxy_basic_auth', decidedAt: 'now', artifactHash: 'sha256:plan', stale: false } },
}

describe('SignoffDossier', () => {
  beforeEach(() => {
    window.localStorage.setItem('faros.review.locale', 'zh-CN')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => payload }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  it('renders human summary instead of raw JSON and labels the raw download', async () => {
    const { container } = render(<SignoffDossier feedbackId="feedback-1" />)
    await screen.findByText('<script>question</script>')
    expect(screen.getByText(/下载 JSON 原始证据|Download raw JSON evidence/)).toBeInTheDocument()
    expect(screen.getByText('方向一致但统计不确定 / Directionally consistent but statistically uncertain')).toBeInTheDocument()
    expect(screen.getByText(/Guardrails/)).toBeInTheDocument()
    expect(screen.getByText('samples')).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('正式签核档案')).toHaveAttribute('aria-disabled', 'true')
  })

  it('shows actionable blockers and signer assurance', async () => {
    render(<SignoffDossier feedbackId="feedback-1" />)
    await screen.findByText('结论尚未签核')
    expect(screen.getByText(/完成结论确认/)).toBeInTheDocument()
    expect(screen.getByText('trusted_proxy_basic_auth')).toBeInTheDocument()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
  })

  it('supports the complete English dossier navigation and actions', async () => {
    window.localStorage.setItem('faros.review.locale', 'en-US')
    render(<SignoffDossier feedbackId="feedback-1" />)
    await screen.findByText('Human-readable signoff summary')
    expect(screen.getByText('Review signoff summary')).toBeInTheDocument()
    expect(screen.getByText('Print / export dossier')).toBeInTheDocument()
    expect(screen.getByText('Download raw JSON evidence')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Dossier sections' })).toBeInTheDocument()
  })
})
