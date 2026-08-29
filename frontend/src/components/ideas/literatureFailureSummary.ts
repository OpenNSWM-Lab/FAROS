import type { TraceStepLike } from './evidenceGateSummary'

export type LiteratureFailureCode =
  | 'no_search_results'
  | 'seed_too_broad'
  | 'eligible_pool_too_small'
  | 'weak_topic_alignment'
  | 'missing_evidence_roles'
  | 'evidence_quality_failed'

export interface LiteratureFailureSummary {
  code: LiteratureFailureCode
  seedQuery: string
  rawResultCount: number
  uniqueResultCount: number
  eligiblePaperCount: number
  alignedPaperCount: number
  rejectedPaperCount: number
  minPaperCount: number
  minAlignedPaperCount: number
  minAlignmentScore: number
  dominantRejectionReason?: string
  dominantRejectionCount: number
  seedAnchors: string[]
  roleIssues: string[]
  repairQueries: string[]
  actionCodes: string[]
  queryTemplate: string
  resumeRecommended: boolean
}

const DEFAULT_TEMPLATE = '[specific method] for [specific task] in [research domain], evaluated by [dataset or metric]'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? Math.max(0, parsed) : fallback
}

function asStringList(value: unknown, limit = 6): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => String(item ?? '').trim())
    .filter((item, index, all) => item && all.indexOf(item) === index)
    .slice(0, limit)
}

function inferCode(
  rawResultCount: number,
  eligiblePaperCount: number,
  alignedPaperCount: number,
  minPaperCount: number,
  minAlignedPaperCount: number,
  dominantRejectionReason?: string,
  roleIssues: string[] = [],
): LiteratureFailureCode {
  if (rawResultCount === 0) return 'no_search_results'
  if (eligiblePaperCount === 0 && dominantRejectionReason === 'generic_overlap_only') return 'seed_too_broad'
  if (eligiblePaperCount < minPaperCount) return 'eligible_pool_too_small'
  if (alignedPaperCount < minAlignedPaperCount) return 'weak_topic_alignment'
  if (roleIssues.length > 0) return 'missing_evidence_roles'
  return 'evidence_quality_failed'
}

function defaultActionCodes(code: LiteratureFailureCode): string[] {
  if (code === 'no_search_results') {
    return ['use_english_academic_terms', 'wait_for_search_cooldown', 'resume_after_retry']
  }
  if (code === 'eligible_pool_too_small') {
    return ['broaden_niche_terms', 'keep_core_task_and_method', 'create_new_session']
  }
  if (code === 'weak_topic_alignment') {
    return ['add_domain_and_task_anchors', 'name_method_or_evaluation_target', 'create_new_session']
  }
  if (code === 'missing_evidence_roles') {
    return ['cover_missing_evidence_roles', 'name_method_or_evaluation_target', 'create_new_session']
  }
  return ['add_task_method_evaluation', 'use_multiple_discriminative_terms', 'create_new_session']
}

export function summarizeLiteratureFailure(
  steps: TraceStepLike[] | null | undefined,
): LiteratureFailureSummary | null {
  const step = [...(steps || [])]
    .reverse()
    .find((item) => item.name === 'literatureSearch' && item.status === 'failed')
  if (!step) return null

  const inputs = isRecord(step.inputs) ? step.inputs : {}
  const outputs = isRecord(step.outputs) ? step.outputs : {}
  const gate = isRecord(outputs.paperQualityGate) ? outputs.paperQualityGate : {}
  const diagnosis = isRecord(outputs.failureDiagnosis) ? outputs.failureDiagnosis : {}
  const requirements = isRecord(diagnosis.requirements)
    ? diagnosis.requirements
    : isRecord(gate.requirements)
      ? gate.requirements
      : {}
  const tierCounts = isRecord(outputs.evidenceTierCounts) ? outputs.evidenceTierCounts : {}
  const rejectionCounts = isRecord(outputs.rejectionReasonCounts) ? outputs.rejectionReasonCounts : {}
  const roleCoverage = isRecord(gate.roleCoverage) ? gate.roleCoverage : {}

  const rawResultCount = asNumber(diagnosis.rawResultCount, asNumber(outputs.resultCountBeforeDedup))
  const uniqueResultCount = asNumber(diagnosis.uniqueResultCount, asNumber(outputs.uniqueResultCount))
  const eligiblePaperCount = asNumber(diagnosis.eligiblePaperCount, asNumber(gate.paperCount))
  const alignedPaperCount = asNumber(diagnosis.alignedPaperCount, asNumber(gate.alignedPaperCount))
  const rejectedPaperCount = asNumber(
    diagnosis.rejectedPaperCount,
    asNumber(tierCounts.rejected, asNumber(outputs.filteredOutCount)),
  )
  const minPaperCount = asNumber(requirements.minPaperCount, 4)
  const minAlignedPaperCount = asNumber(requirements.minAlignedPaperCount, 3)
  const minAlignmentScore = asNumber(requirements.minAlignmentScore, 0.32)

  let dominantRejectionReason = typeof diagnosis.dominantRejectionReason === 'string'
    ? diagnosis.dominantRejectionReason
    : undefined
  let dominantRejectionCount = asNumber(diagnosis.dominantRejectionCount)
  if (!dominantRejectionReason) {
    Object.entries(rejectionCounts).forEach(([reason, value]) => {
      const count = asNumber(value)
      if (count > dominantRejectionCount) {
        dominantRejectionReason = reason
        dominantRejectionCount = count
      }
    })
  }

  const roleIssues = asStringList(diagnosis.roleIssues, 4).length
    ? asStringList(diagnosis.roleIssues, 4)
    : asStringList(roleCoverage.issues, 4)
  const inferredCode = inferCode(
    rawResultCount,
    eligiblePaperCount,
    alignedPaperCount,
    minPaperCount,
    minAlignedPaperCount,
    dominantRejectionReason,
    roleIssues,
  )
  const rawCode = String(diagnosis.code || inferredCode) as LiteratureFailureCode
  const validCodes: LiteratureFailureCode[] = [
    'no_search_results',
    'seed_too_broad',
    'eligible_pool_too_small',
    'weak_topic_alignment',
    'missing_evidence_roles',
    'evidence_quality_failed',
  ]
  const code = validCodes.includes(rawCode) ? rawCode : inferredCode
  const actionCodes = asStringList(diagnosis.actionCodes, 5)

  return {
    code,
    seedQuery: String(diagnosis.seedQuery || inputs.seedQuery || '').trim(),
    rawResultCount,
    uniqueResultCount,
    eligiblePaperCount,
    alignedPaperCount,
    rejectedPaperCount,
    minPaperCount,
    minAlignedPaperCount,
    minAlignmentScore,
    dominantRejectionReason,
    dominantRejectionCount,
    seedAnchors: asStringList(diagnosis.seedAnchors, 12),
    roleIssues,
    repairQueries: asStringList(diagnosis.repairQueries || outputs.repairQueries, 6),
    actionCodes: actionCodes.length ? actionCodes : defaultActionCodes(code),
    queryTemplate: String(diagnosis.queryTemplate || DEFAULT_TEMPLATE),
    resumeRecommended: typeof diagnosis.resumeRecommended === 'boolean'
      ? diagnosis.resumeRecommended
      : code === 'no_search_results',
  }
}
