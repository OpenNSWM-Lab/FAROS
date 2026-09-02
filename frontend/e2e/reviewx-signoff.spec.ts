import { expect, test, type Page, type Route } from '@playwright/test'

type Stage = 'plan' | 'repair' | 'conclusion'

const acknowledgementIds: Record<Stage, string[]> = {
  plan: [
    'reviewed_scientific_question_and_hypothesis',
    'reviewed_data_split_and_holdout',
    'reviewed_metrics_budget_and_stop_conditions',
  ],
  repair: [
    'reviewed_reviewx_findings',
    'confirmed_repairs_applied',
    'reviewed_rerun_scope_and_residual_risk',
  ],
  conclusion: [
    'reviewed_baseline_current_and_interval',
    'reviewed_side_effects_and_limitations',
    'accepted_claim_scope',
  ],
}

const pendingSignoff = (stage: Stage, required = true) => ({
  stage,
  status: 'pending',
  storedStatus: 'pending',
  required,
  artifactHash: `sha256:${stage.padEnd(64, '0')}`,
  reviewerRole: null,
  reviewerId: null,
  reviewerName: null,
  actorAccountId: null,
  actorRole: null,
  authAssurance: null,
  acknowledgements: [],
  rationale: '',
  conditions: [],
  decidedAt: null,
  stale: false,
  history: [],
})

async function installEvidenceApi(
  page: Page,
  options: { role?: 'reviewer' | 'judge'; stale?: boolean } = {},
) {
  const role = options.role || 'reviewer'
  const signoffs: Record<Stage, ReturnType<typeof pendingSignoff>> = {
    plan: pendingSignoff('plan'),
    repair: pendingSignoff('repair', false),
    conclusion: pendingSignoff('conclusion'),
  }
  if (options.stale) {
    signoffs.plan = {
      ...signoffs.plan,
      status: 'pending',
      storedStatus: 'approved',
      stale: true,
      reviewerId: '王子嘉',
      reviewerName: '王子嘉',
      actorAccountId: 'faros-signer-wzj',
      actorRole: 'reviewer',
      authAssurance: 'trusted_proxy_basic_auth',
      acknowledgements: acknowledgementIds.plan,
      decidedAt: '2026-09-02T01:00:00Z',
    }
  }

  const feedbackState = () => ({
    feedbackId: 'feedback-e2e',
    createdAt: '2026-09-02T00:00:00Z',
    runId: 'run-e2e',
    runKind: 'platform',
    researchSeriesId: 'series-e2e',
    iterationNumber: 2,
    sourceArtifacts: {
      'research_dossier.json': 'artifact-plan',
      'experiment_evidence.json': 'artifact-evidence',
    },
    metricSnapshot: [
      { name: 'method:F1', value: 0.7764, unit: 'ratio', split: 'final_holdout' },
      { name: 'method:Accuracy', value: 0.744, unit: 'ratio', split: 'final_holdout' },
    ],
    qualityAssessment: {
      gateStatus: 'pass',
      overallScore: 0.94,
      dimensionScores: { reproducibility: 0.96, evidenceGrounding: 0.93 },
      findings: [],
      uncertainty: 'The primary CI crosses zero.',
      llmTrace: [],
    },
    iterationDecision: {
      decision: 'accept_results',
      rationale: 'The result is reproducible; the claim remains bounded by the interval.',
      targetSections: [],
      metricDeltas: [{ name: 'F1', previous: 0.7725, current: 0.7764, delta: 0.0039 }],
      nextActions: ['Record the bounded conclusion.'],
    },
    planFeedback: { requested: false, applied: false, targetSections: [], reason: 'No write requested.' },
    humanSignoffs: signoffs,
    humanFeedback: {
      feedbackHash: 'sha256:feedback', items: [], targetSections: [], requiredActions: [],
      requiresApplication: false, applied: true, staleApplication: false, application: null,
    },
    humanConditionVerifications: {
      required: false, allResolved: true, total: 0, passed: 0, waived: 0, unresolved: 0, conditions: [],
    },
    sourceArtifactUrls: {},
    publicationReady: signoffs.plan.status === 'approved'
      && signoffs.conclusion.status === 'approved'
      && !signoffs.plan.stale
      && !signoffs.conclusion.stale,
  })

  const dossierState = () => {
    const feedback = feedbackState()
    return {
      schemaVersion: 'reviewx-signoff-dossier/v1',
      release: 'draft',
      watermark: 'DRAFT_NOT_HUMAN_APPROVED',
      generatedAt: '2026-09-02T00:00:00Z',
      contentHash: 'sha256:dossier-e2e',
      subject: {
        feedbackId: feedback.feedbackId,
        runId: feedback.runId,
        researchSeriesId: feedback.researchSeriesId,
        scientificQuestion: '一次证据驱动修订能否改善未见集表现？',
        planPackageId: 'plan-e2e',
        iterationNumber: 2,
        artifactHash: signoffs.conclusion.artifactHash,
      },
      executiveDecision: {
        iterationDecision: 'accept_results',
        qualityGate: 'pass',
        publicationReady: feedback.publicationReady,
        blockingReasons: feedback.publicationReady ? [] : [{
          code: options.stale ? 'PLAN_SIGNOFF_STALE' : 'CONCLUSION_SIGNOFF_REQUIRED',
          message: options.stale ? 'plan 签核因证据变化已失效' : 'conclusion 阶段尚未批准',
          nextStep: options.stale ? '重新核对当前证据并签核' : '完成结论确认',
        }],
      },
      plan: {
        hypothesis: '修订提高证据可辨识性。', baseline: 'Round 1', intervention: 'Round 2',
        primaryMetric: 'F1', guardrails: ['Accuracy'], stopConditions: ['CI gate'],
        delta: {
          changedSections: ['sampling'],
          parameterChanges: [{ field: 'sampling', oldValue: 'uniform', newValue: 'adaptive', rationale: 'ReviewX diagnosis', targetNode: 'experiment.design' }],
          evidenceReferences: ['artifact-evidence'],
        },
      },
      evidence: {
        dataSource: ['frozen fixture'], dataSplitPolicy: 'development/calibration/final holdout',
        metrics: [
          { name: 'F1', direction: 'maximize', baseline: 0.7725, current: 0.7764, delta: 0.0039, ciLower: -0.0275, ciUpper: 0.0354, decision: 'BOUNDARY', interpretation: '方向一致但统计不确定 / Directionally consistent but statistically uncertain', role: 'primary', split: 'final_holdout', sourceArtifactId: 'artifact-evidence', source: 'record.metricSnapshot' },
          { name: 'Accuracy', direction: 'maximize', baseline: 0.75, current: 0.744, delta: -0.006, ciLower: -0.02, ciUpper: 0.01, decision: 'BOUNDARY', role: 'guardrail', split: 'final_holdout', sourceArtifactId: 'artifact-evidence', source: 'record.metricSnapshot' },
        ],
      },
      review: { findingCounts: {}, findings: [], humanFeedback: {}, acceptanceConditions: {} },
      limitations: ['不宣称显著提升。'],
      provenance: { sourceArtifacts: feedback.sourceArtifacts, benchmarkFingerprint: 'sha256:benchmark', qwenCalls: [], auditIntegrity: { valid: true, eventCount: 2 } },
      signoffs,
    }
  }

  const fulfillJson = (route: Route, body: unknown, status = 200) => route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/system/session') {
      return fulfillJson(route, { userId: role === 'judge' ? 'faros-judge' : 'faros-signer-wzj', role, credentialScope: role })
    }
    if (path === '/api/v1/papers') return fulfillJson(route, { papers: [], total: 0 })
    if (path === '/api/v1/runs') return fulfillJson(route, { runs: [] })
    if (path === '/api/faros/runs') return fulfillJson(route, { runs: [] })
    if (path.endsWith('/experiment-feedback/history')) return fulfillJson(route, { records: [], total: 0 })
    if (path.endsWith('/signoff-dossier')) return fulfillJson(route, dossierState())
    if (path.endsWith('/evidence-bundle')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'Content-Disposition': 'attachment; filename="reviewx-evidence.json"' },
        body: JSON.stringify({ schemaVersion: 'reviewx-human-approved-evidence/v1' }),
      })
    }
    if (path.endsWith('/signoff-dossier.html')) {
      return route.fulfill({ status: feedbackState().publicationReady || url.searchParams.get('release') === 'draft' ? 200 : 409, contentType: 'text/html', body: '<h1>ReviewX dossier</h1>' })
    }
    const signoffMatch = path.match(/\/experiment-feedback\/feedback-e2e\/signoffs\/(plan|repair|conclusion)$/)
    if (signoffMatch && request.method() === 'PUT') {
      if (role === 'judge') return fulfillJson(route, { detail: 'Judge accounts are read-only' }, 403)
      const stage = signoffMatch[1] as Stage
      const body = request.postDataJSON() as { acknowledgements: string[]; reviewerName: string; rationale: string }
      signoffs[stage] = {
        ...signoffs[stage],
        status: 'approved',
        storedStatus: 'approved',
        stale: false,
        reviewerId: body.reviewerName,
        reviewerName: body.reviewerName,
        actorAccountId: 'faros-signer-wzj',
        actorRole: 'reviewer',
        authAssurance: 'trusted_proxy_basic_auth',
        acknowledgements: body.acknowledgements,
        rationale: body.rationale,
        decidedAt: '2026-09-02T02:00:00Z',
      }
      const feedback = feedbackState()
      return fulfillJson(route, {
        feedbackId: feedback.feedbackId,
        humanSignoffs: signoffs,
        humanFeedback: feedback.humanFeedback,
        humanConditionVerifications: feedback.humanConditionVerifications,
        publicationReady: feedback.publicationReady,
      })
    }
    if (path.endsWith('/experiment-feedback/feedback-e2e')) return fulfillJson(route, feedbackState())
    return fulfillJson(route, {})
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('faros.review.locale', 'zh-CN'))
})

test('signer reads the summary, signs each stage, and unlocks the official dossier', async ({ page }, testInfo) => {
  await installEvidenceApi(page)
  await page.goto('/review/consistency?feedbackId=feedback-e2e&focus=signoff')

  await expect(page.getByText('人类可读签核摘要')).toBeVisible()
  await expect(page.getByText('方向一致但统计不确定 / Directionally consistent but statistically uncertain')).toBeVisible()
  const rawLink = page.getByRole('link', { name: '下载 JSON 原始证据' })
  await expect(rawLink).toHaveAttribute('href', /evidence-bundle\?release=draft$/)
  const approve = page.getByRole('button', { name: '批准', exact: true })
  await expect(approve).toBeDisabled()

  await page.getByLabel('签核人真实姓名').fill('王子嘉')
  await page.getByLabel('决策理由').fill('已核对冻结方案、证据边界与责任范围。')
  for (const checkbox of await page.getByRole('checkbox').all()) {
    if (await checkbox.isVisible() && !(await checkbox.isChecked())) await checkbox.check()
  }
  await expect(approve).toBeEnabled()
  await approve.click()

  await page.getByRole('button', { name: /结论发布/ }).click()
  await page.getByLabel('决策理由').fill('已核对基线、区间、副作用和允许发布的结论范围。')
  const visibleChecks = page.locator('fieldset input[type="checkbox"]')
  for (let index = 0; index < await visibleChecks.count(); index += 1) await visibleChecks.nth(index).check()
  await approve.click()

  await expect(page.getByRole('link', { name: '正式签核档案' })).toBeVisible()
  await expect(page.getByText(/王子嘉 · faros-signer-wzj · trusted_proxy_basic_auth/).first()).toBeVisible()

  for (const viewport of [
    { width: 390, height: 844, name: '390x844' },
    { width: 768, height: 1024, name: '768x1024' },
    { width: 1366, height: 768, name: '1366x768' },
    { width: 1440, height: 900, name: '1440x900' },
  ]) {
    await page.setViewportSize(viewport)
    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme })
      await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-${scheme}.png`) })
    }
  }
})

test('judge sees evidence but no signoff or shared-feedback controls', async ({ page }) => {
  await installEvidenceApi(page, { role: 'judge' })
  await page.goto('/review/consistency?feedbackId=feedback-e2e&focus=signoff')
  await expect(page.getByText('评委账号为只读证据观察者，不显示签核或共享反馈修改控件。')).toBeVisible()
  await expect(page.getByRole('button', { name: '批准', exact: true })).toHaveCount(0)
  await expect(page.getByText('人类可读签核摘要')).toBeVisible()
})

test('artifact change marks the prior approval stale and relocks official output', async ({ page }) => {
  await installEvidenceApi(page, { stale: true })
  await page.goto('/review/consistency?feedbackId=feedback-e2e&focus=signoff')
  await expect(page.getByText('plan 签核因证据变化已失效')).toBeVisible()
  await expect(page.getByText('证据已变化，请重新审核')).toBeVisible()
  await expect(page.getByText('正式签核档案')).toHaveAttribute('aria-disabled', 'true')
})
