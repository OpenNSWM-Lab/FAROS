export type PaperDisplayStatus =
  | 'created'
  | 'generating'
  | 'loop_revising'
  | 'compile_failed'
  | 'review_issues'
  | 'review_passed'

export interface PaperStatusInput {
  status?: string
  compileStatus?: string | null
  simpleReviewPassed?: boolean | null
  logs?: { message?: string }[]
}

export const PAPER_STATUS_LABELS: Record<PaperDisplayStatus, string> = {
  created: 'Created',
  generating: 'Generating',
  loop_revising: 'Loop revising',
  compile_failed: 'Compile failed',
  review_issues: 'Review issues',
  review_passed: 'Review passed',
}

export const PAPER_STATUS_CLASSES: Record<PaperDisplayStatus, string> = {
  created: 'border-slate-300 bg-slate-50 text-slate-700',
  generating: 'border-blue-300 bg-blue-50 text-blue-700',
  loop_revising: 'border-violet-300 bg-violet-50 text-violet-700',
  compile_failed: 'border-red-300 bg-red-50 text-red-700',
  review_issues: 'border-amber-300 bg-amber-50 text-amber-800',
  review_passed: 'border-emerald-300 bg-emerald-50 text-emerald-700',
}

const hasLoopActivity = (logs: { message?: string }[] = []) => logs.some(log => {
  const message = log.message || ''
  return (
    message.includes('/compile_feedback:')
    || message.includes('/review_feedback:')
    || message.includes('requesting feedback round')
    || message.includes('writing_feedback_rewrite')
  )
})

export function getPaperDisplayStatus(paper: PaperStatusInput): PaperDisplayStatus {
  if (paper.status === 'created') return 'created'
  if (paper.status === 'generating') {
    return hasLoopActivity(paper.logs) ? 'loop_revising' : 'generating'
  }
  if (paper.status === 'completed' || paper.simpleReviewPassed) return 'review_passed'
  if (paper.status === 'failed') {
    if (paper.compileStatus !== 'latexmk') return 'compile_failed'
    return 'review_issues'
  }
  return 'created'
}

export function paperDisplayStatusLabel(paper: PaperStatusInput): string {
  return PAPER_STATUS_LABELS[getPaperDisplayStatus(paper)]
}

export function paperDisplayStatusClass(paper: PaperStatusInput): string {
  return PAPER_STATUS_CLASSES[getPaperDisplayStatus(paper)]
}
