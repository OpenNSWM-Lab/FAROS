export type EvidenceGateTone = 'success' | 'warning' | 'danger' | 'neutral'

export interface TraceStepLike {
  name: string
  status: string
  error?: string
  inputs?: Record<string, unknown>
  outputs?: Record<string, unknown>
}

export interface EvidenceGateSummary {
  title: string
  description: string
  tone: EvidenceGateTone
  stats: Array<{ label: string; value: string }>
  coverageDimensions: Array<{
    key: string
    label: string
    status: string
    score?: string
    paperCount: number
  }>
  scientistJudgment?: string
  issues: string[]
  warnings: string[]
  repairQueries: string[]
  reviewerScore?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function asStringList(value: unknown, limit = 3): string[] {
  if (!Array.isArray(value)) return []
  const items: string[] = []
  value.forEach((item) => {
    const text = String(item ?? '').trim()
    if (text && !items.includes(text)) items.push(text)
  })
  return items.slice(0, limit)
}

function cleanIssue(text: string): string {
  return text.replace(/^ideaBrainstorm\.preflight(?:\.repaired)?:\s*/i, '').trim()
}

function stat(label: string, value: unknown): { label: string; value: string } {
  const num = asNumber(value)
  return { label, value: num == null ? '-' : String(num) }
}

function summarizeCoverageDimensions(value: unknown): EvidenceGateSummary['coverageDimensions'] {
  if (!Array.isArray(value)) return []
  return value
    .filter(isRecord)
    .map((item, index) => {
      const key = String(item.key || `dimension_${index + 1}`)
      const label = String(item.label || key.replace(/_/g, ' '))
      const status = String(item.status || 'unknown')
      const score = asNumber(item.score)
      const paperIds = Array.isArray(item.supportingPaperIds) ? item.supportingPaperIds : []
      return {
        key,
        label,
        status,
        score: score == null ? undefined : `${Math.round(Math.min(1, Math.max(0, score)) * 100)}%`,
        paperCount: paperIds.length,
      }
    })
    .slice(0, 6)
}

export function summarizeEvidenceGate(steps: TraceStepLike[] | null | undefined): EvidenceGateSummary | null {
  const step = (steps || []).find((item) => item.name === 'evidenceGate')
  if (!step) return null

  const outputs = isRecord(step.outputs) ? step.outputs : {}
  const gate = isRecord(outputs.evidenceGate) ? outputs.evidenceGate : {}
  const repairReport = isRecord(outputs.repairReport) ? outputs.repairReport : {}
  const reviewer = isRecord(gate.llmReviewer) ? gate.llmReviewer : {}
  const coverageReport = isRecord(gate.coverageReport) ? gate.coverageReport : {}
  const passed = gate.passed === true && outputs.allowedToBrainstorm !== false && step.status !== 'failed'
  const repairAttempted = outputs.repairAttempted === true

  const tone: EvidenceGateTone = passed ? (repairAttempted ? 'warning' : 'success') : 'danger'
  const title = passed
    ? (repairAttempted ? 'Evidence repaired' : 'Evidence ready')
    : 'Evidence not ready'
  const description = passed
    ? (repairAttempted
      ? 'The system repaired the evidence pool before generating ideas.'
      : 'The literature evidence is strong enough to generate ideas.')
    : 'The system needs stronger, more relevant literature evidence before idea generation.'

  const stats = [
    stat('External papers', gate.externalPaperCount),
    stat('Topic aligned', gate.alignedPaperCount),
    stat('Gap signals', gate.gapSignalCount),
    { label: 'Reviewer', value: String(gate.reviewMode || 'rule') },
  ]

  const issues = [
    ...asStringList(gate.errors, 3).map(cleanIssue),
    ...asStringList(reviewer.blockingIssues, 3).map(cleanIssue),
    ...(step.error ? [cleanIssue(step.error)] : []),
  ].filter((item, index, all) => item && all.indexOf(item) === index).slice(0, 3)

  const warnings = [
    ...asStringList(gate.warnings, 2).map(cleanIssue),
    ...asStringList(reviewer.warnings, 2).map(cleanIssue),
  ].filter((item, index, all) => item && all.indexOf(item) === index).slice(0, 3)

  const repairQueries = [
    ...asStringList(repairReport.queries, 3),
    ...asStringList(reviewer.repairQueries, 3),
  ].filter((item, index, all) => item && all.indexOf(item) === index).slice(0, 3)

  const score = asNumber(reviewer.score)
  const reviewerScore = score == null ? undefined : `${Math.round(Math.min(1, Math.max(0, score)) * 100)}%`
  const scientistJudgment = typeof coverageReport.scientistJudgment === 'string'
    ? coverageReport.scientistJudgment.trim()
    : undefined

  return {
    title,
    description,
    tone,
    stats,
    coverageDimensions: summarizeCoverageDimensions(coverageReport.dimensions),
    scientistJudgment,
    issues,
    warnings,
    repairQueries,
    reviewerScore,
  }
}
