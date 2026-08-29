import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PlanGenerationPanel } from './PlanGenerationPanel'
import {
  addPlanPackageFeedback,
  approvePlanPackageWithMode,
  createPlanPackageFromIdeaSession,
  getPlanPackage,
  getPlanPackageByIdeaSession,
  getPlanPackagePresentation,
  getPlanPackagePresentationByIdeaSession,
  revisePlanPackage,
} from './planPackageApi'

const mockNavigate = vi.hoisted(() => vi.fn())

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('./planPackageApi', () => ({
  addPlanPackageFeedback: vi.fn(),
  approvePlanPackageWithMode: vi.fn(),
  createPlanPackageFromIdeaSession: vi.fn(),
  getPlanPackage: vi.fn(),
  getPlanPackageByIdeaSession: vi.fn(),
  getPlanPackagePresentation: vi.fn(),
  getPlanPackagePresentationByIdeaSession: vi.fn(),
  revisePlanPackage: vi.fn(),
}))

function makePlanPackage() {
  return {
    schemaVersion: 'plan-package/v4',
    packageId: 'ppkg_001',
    createdAt: '2026-07-13T08:00:00.000Z',
    status: 'needs_human_review',
    source: {
      ideaSessionId: 'idea_001',
      ideaCandidateId: 'cand_001',
      rankedOutputId: 'ro_001',
      searchTreeId: 'tree_001',
      searchNodeId: 'node_001',
      pathSeedId: 'seed_001',
      reasoningKgId: 'rkg_001',
      literatureMapId: 'lm_001',
      bftsHandoffId: 'bh_001',
    },
    idea: {
      id: 'cand_001',
      title: 'Evidence-aware citation faithfulness for RAG',
      problem: 'High-risk RAG systems still mis-handle citations under weak evidence.',
      hypothesisStatement: 'Adding evidence-aware refusal and verification improves citation fidelity.',
      keyInsight: 'Failure happens when retrieval confidence is conflated with answer confidence.',
      proposedMethod: 'Add a verifier that conditions generation on evidence coverage.',
      expectedOutcome: 'Higher citation faithfulness with fewer unsupported claims.',
      scores: { novelty: 8.4, feasibility: 7.9 },
      critiqueSummary: 'Strong direction with a clear validation path.',
      closestPriorWork: [
        {
          title: 'Evidence-grounded generation in RAG',
          description: 'Shows that retrieval coverage should govern answer emission.',
          category: 'prior_work',
          year: 2024,
        },
      ],
    },
    background: {
      summary: 'Current RAG systems lack a disciplined way to connect evidence coverage to answer generation.',
      motivation: 'That gap makes citation faithfulness hard to trust in high-risk domains.',
      currentLimitations: ['Retrieval scores do not reflect evidence sufficiency.'],
      domainContext: ['High-risk question answering', 'Evidence-grounded generation'],
      evidenceRefs: [{ type: 'paper', id: 'paper_structured_1', source: 'structured', note: 'Background anchor' }],
    },
    literatureSurvey: {
      summary: 'The survey highlights evidence-grounded generation, abstention, and citation evaluation.',
      coverage: {
        rawPaperCount: 4,
        selectedPaperCount: 2,
        structuredPaperCount: 2,
        probePaperCount: 1,
        clusterCount: 1,
      },
      clusters: [],
      papers: [
        {
          paperId: 'paper_structured_1',
          structuredPaperId: 'paper_structured_1',
          source: 'structured',
          title: 'Citation Faithfulness in Retrieval-Augmented Generation',
          authors: ['A. Researcher'],
          year: 2025,
          venue: 'ACL',
          url: 'https://example.com/paper-1',
          role: 'background',
          relevanceScore: 0.92,
          relevanceSignals: ['citation', 'faithfulness'],
          relevanceReason: 'Directly studies citation fidelity under weak evidence.',
          summary: 'Shows that evidence-aware generation reduces unsupported claims.',
          methods: [
            {
              methodId: 'mm_a5640b8e',
              description: 'Deep Think and its advanced variants',
              category: 'collaborative',
              relatedClaims: ['human-ai collaboration'],
            },
            {
              methodId: 'mm_refinement',
              description: 'Iterative refinement for improving result quality',
              category: 'methodological',
              relatedClaims: [],
            },
          ],
          findings: [
            {
              findingId: 'fn_be01206e',
              description: 'LLMs have successfully collaborated with advanced AI models to solve open problems and generate new proofs.',
              category: 'empirical',
              relatedClaims: ['proof generation'],
            },
            {
              findingId: 'fn_human_ai',
              description: 'Human-AI collaboration often benefits from iterative refinement and problem decomposition.',
              category: 'methodological',
              relatedClaims: ['cross-disciplinary transfer'],
            },
          ],
          limitations: ['Does not fully model refusal behavior.'],
          claims: [{ text: 'Evidence coverage matters.' }],
          usedByStageIds: ['stage_1'],
          usedByStepIds: ['step_1'],
          evidenceRefs: [{ type: 'paper', id: 'paper_structured_1', source: 'structured' }],
        },
        {
          paperId: 'paper_probe_1',
          structuredPaperId: null,
          source: 'probe',
          title: 'Abstention for High-Risk QA',
          authors: ['B. Scholar'],
          year: 2024,
          venue: 'NeurIPS',
          url: 'https://example.com/paper-2',
          role: 'supporting',
          relevanceScore: 0.71,
          relevanceSignals: ['abstention'],
          relevanceReason: 'Provides a complementary abstention signal.',
          summary: 'Shows abstention can prevent unsupported answers.',
          methods: [
            {
              methodId: 'mm_abstain',
              description: 'Abstain policy for high-risk answers',
              category: 'policy',
              relatedClaims: [],
            },
          ],
          findings: [
            {
              findingId: 'fn_hallucination',
              description: 'Lower hallucination rate under stronger refusal thresholds.',
              category: 'empirical',
              relatedClaims: [],
            },
          ],
          limitations: ['Limited explanation traceability.'],
          claims: [{ text: 'Refusal should be evidence-driven.' }],
          usedByStageIds: ['stage_1'],
          usedByStepIds: ['step_1'],
          evidenceRefs: [{ type: 'paper', id: 'paper_probe_1', source: 'probe' }],
        },
      ],
    },
    gap: {
      summary: 'The gap is not just retrieval quality but a missing bridge between coverage and generation.',
      items: [
        {
          id: 'gap_1',
          kind: 'selected',
          statement: 'Evidence coverage is not yet encoded into generation decisions.',
          severity: 'high',
          existingCoverage: 'Classic RAG uses retrieval scores but not explicit coverage gates.',
          unresolvedIssue: 'Unsupported claims still slip through when evidence is sparse.',
          proposedEntry: 'Add a verifier/refusal stage before answer emission.',
          boundary: 'Scoped to high-risk QA with citation requirements.',
          validationNeeds: ['Citation faithfulness', 'Refusal accuracy'],
          whyUnsolved: 'Most baselines only optimize recall or answer quality.',
          supportedByPaperIds: ['paper_structured_1'],
          supportedByClaimIds: ['claim_1'],
          linkedGraphSignalIds: ['signal_1'],
        },
      ],
      selectedGapId: 'gap_1',
    },
    principle: {
      summary: 'Condition generation on explicit evidence coverage and verification.',
      mechanism: 'Use evidence sufficiency as a gate before answer emission.',
      noveltyClaim: 'The plan makes coverage a first-class control signal, not a post-hoc check.',
      assumptions: ['Structured evidence is available.'],
      risks: ['Tighter gating may reduce answer coverage.'],
      reasoningPath: [],
      graphGrounding: {
        entityIds: ['entity_1'],
        relationIds: ['relation_1'],
        pathSeedIds: ['seed_001'],
        searchNodeIds: ['node_001'],
      },
      probeGrounding: {
        probeResultIds: ['probe_001'],
        graphPatchIds: ['patch_001'],
        probePaperIds: ['paper_probe_1'],
      },
    },
    contributionStatement: [
      {
        id: 'cs_1',
        type: 'method',
        statement: 'Introduce evidence-aware refusal before final generation.',
        noveltyBasis: 'Explicitly uses coverage as a gate.',
        validationStageIds: ['stage_1'],
        validationStepIds: ['step_1'],
        evidenceRefs: [{ type: 'paper', id: 'paper_structured_1', source: 'structured' }],
      },
    ],
    researchQuestion: 'How can high-risk RAG systems improve citation faithfulness under weak evidence?',
    hypothesis: 'If evidence coverage is explicitly gated, citation faithfulness improves.',
    constants: { temperature: 0.2, maxTokens: 2048 },
    stages: [
      {
        id: 'stage_1',
        order: 1,
        title: 'Build and verify the evidence gate',
        goal: 'Encode evidence coverage into generation decisions.',
        method: 'Add a verifier and refusal policy.',
        dependsOn: [],
        steps: [
          {
            id: 'step_1',
            order: 1,
            title: 'Collect evidence signals',
            desc: 'Aggregate citation and coverage signals before generation.',
            method: 'Compute evidence sufficiency from structured sources.',
            inputFrom: [],
            outputs: [
              { type: 'metrics', name: 'coverage_score', desc: 'Coverage score for each sample', requiredFor: ['analysis'] },
            ],
            expected: [
              { metric: 'citation_faithfulness', target: '>= 0.80', desc: 'Faithfulness should improve over baseline' },
            ],
            evidenceRefs: [{ type: 'paper', id: 'paper_structured_1', source: 'structured' }],
            codeHints: {},
          },
        ],
      },
      {
        id: 'stage_2',
        order: 2,
        title: 'Evaluate refusal behavior',
        goal: 'Verify the gate does not over-reject.',
        method: 'Run held-out evaluation with refusal metrics.',
        dependsOn: ['stage_1'],
        steps: [
          {
            id: 'step_2',
            order: 1,
            title: 'Measure tradeoffs',
            desc: 'Compare faithfulness and answer coverage.',
            method: 'Track refusal accuracy and unsupported claims.',
            inputFrom: ['step_1'],
            outputs: [
              { type: 'report', name: 'evaluation_report', desc: 'Summary of tradeoffs', requiredFor: ['review'] },
            ],
            expected: [
              { metric: 'refusal_accuracy', target: '>= 0.70', desc: 'Refusal should remain calibrated' },
            ],
            evidenceRefs: [{ type: 'paper', id: 'paper_probe_1', source: 'probe' }],
            codeHints: {},
          },
        ],
      },
    ],
    evidenceTrace: {
      ideaCandidateId: 'cand_001',
      searchNodeId: 'node_001',
      pathSeedId: 'seed_001',
      reasoningKgId: 'rkg_001',
      literatureMapId: 'lm_001',
      selectedPaperIds: ['paper_structured_1'],
      structuredPaperIds: ['paper_structured_1'],
      probeResultIds: ['probe_001'],
      graphPatchIds: ['patch_001'],
      probePaperIds: ['paper_probe_1'],
      candidateGraphEvidence: {},
      reasoningTrace: [],
    },
    downstreamContract: {
      implementation: { consume: ['researchQuestion', 'hypothesis', 'constants', 'stages'], requiredOutputs: ['metrics', 'table'] },
      code: { consume: ['stages.steps', 'constants'], requiredOutputs: ['code', 'log'] },
      paper: { consume: ['background', 'literatureSurvey', 'gap', 'principle'], requiredOutputs: ['report'] },
      review: { consume: ['idea', 'gap', 'qualityGate'], requiredOutputs: ['report'] },
    },
    qualityGate: {
      schemaValid: true,
      evidenceValid: true,
      topicRelevant: true,
      citationFaithful: true,
      planSpecific: true,
      downstreamReady: true,
      agentApproved: true,
      humanApproved: false,
      implementationReady: false,
      overallScore: 0.84,
      reviewDecision: 'revise',
      warnings: ['Need one more validation metric'],
      errors: [],
    },
    generation: {
      mode: 'hybrid',
      providerName: 'moonshot',
      model: 'moonshot-v1-8k',
      promptVersion: 'v1',
      blueprintVersion: 'v1',
      templateId: 'plan_v1',
      blueprintSummary: {},
      llmUsedSections: ['background', 'gap'],
      reviewerMode: 'hybrid',
      llmReviewerUsed: true,
      repairRounds: 1,
      schemaRepairRounds: 0,
      fallbackUsed: false,
      warnings: ['One reviewer warning'],
    },
    humanFeedback: [
      {
        id: 'fb_1',
        sectionPath: 'package',
        displayLabel: 'Top-level plan',
        sourceView: 'presentation',
        targetSections: ['summary'],
        feedbackType: 'correction',
        comment: 'Tighten the evaluation metric selection.',
        severity: 'medium',
        requestedAction: 'revise',
        createdAt: '2026-07-13T09:00:00.000Z',
        resolved: false,
        resolvedByRevisionId: null,
      },
    ],
    revisions: [
      {
        id: 'rev_1',
        parentPackageId: 'ppkg_001',
        createdAt: '2026-07-13T09:10:00.000Z',
        changedSections: ['gap', 'stages'],
        feedbackIds: ['fb_1'],
        summary: 'Refined the validation plan and tightened the stages.',
        generationMode: 'hybrid',
        repairRounds: 1,
        patchSummary: {},
      },
    ],
    reviewReports: [
      {
        reviewer: 'deterministic',
        score: 0.78,
        passed: false,
        blockingIssues: [
          {
            id: 'issue_1',
            severity: 'blocking',
            sectionPath: 'reviewSummary',
            message: 'Need one more evaluation metric tied to the hypothesis.',
            evidenceRefs: [{ type: 'paper', id: 'paper_structured_1', source: 'structured' }],
          },
        ],
        warnings: [
          {
            id: 'issue_2',
            severity: 'warning',
            sectionPath: 'implementationPlan',
            message: 'Step 2 should name the refusal threshold explicitly.',
            evidenceRefs: [{ type: 'paper', id: 'paper_probe_1', source: 'probe' }],
          },
        ],
        repairSuggestions: ['Add a refusal metric and name the evaluation threshold.'],
        evidenceRefs: [{ type: 'paper', id: 'paper_structured_1', source: 'structured' }],
        createdAt: '2026-07-13T09:20:00.000Z',
      },
    ],
    metaReview: {
      overallScore: 0.8,
      decision: 'revise',
      confidence: 0.77,
      blockingIssues: [],
      warnings: [],
      requiredRepairs: ['Clarify the refusal metric.'],
      reviewerScores: { deterministic: 0.78 },
      createdAt: '2026-07-13T09:25:00.000Z',
    },
    sourceFields: {
      idea: ['title', 'problem'],
      background: ['summary'],
      literatureSurvey: ['papers'],
      gap: ['items'],
      principle: ['mechanism'],
      contributionStatement: ['statement'],
      evidenceTrace: ['ideaCandidateId'],
      implementationPlan: ['stages'],
    },
    rawIdeaOutputs: {},
  }
}

function makePresentation() {
  return {
    schemaVersion: 'plan-package-presentation/v1',
    packageId: 'ppkg_001',
    packageStatus: 'approved',
    title: 'Evidence-aware citation faithfulness for RAG',
    executiveSummary: 'A compact plan that gates generation on evidence coverage and verifies citations before release.',
    researchQuestion: 'How can high-risk RAG systems improve citation faithfulness under weak evidence?',
    hypothesis: 'If evidence coverage is explicitly gated, citation faithfulness improves.',
    background: {
      summary: 'RAG systems still need a clearer bridge between evidence sufficiency and generation.',
      whyValuable: 'That bridge reduces unsupported claims in high-risk settings.',
      currentLimitations: ['Coverage is often implicit rather than explicit.'],
      scope: ['High-risk QA', 'Citation faithfulness'],
    },
    gap: {
      statement: 'The gap is a missing bridge between evidence coverage and generation.',
      existingCoverage: 'Existing work often measures retrieval quality but not coverage-driven refusal.',
      unresolvedIssue: 'Unsupported claims remain possible under sparse evidence.',
      proposedEntry: 'Insert a verifier before final answer emission.',
      boundary: 'Scoped to citation-faithful high-risk QA.',
      validationNeeds: ['Citation faithfulness', 'Refusal accuracy'],
    },
    method: {
      principle: 'Use evidence sufficiency as a first-class control signal.',
      mechanism: 'A verifier computes a coverage score and can trigger refusal.',
      noveltyClaim: 'The plan integrates coverage into generation instead of checking it after the fact.',
      contributions: ['Coverage gate', 'Refusal calibration'],
      assumptions: ['Structured evidence is available.'],
      risks: ['Over-refusal if the gate is too strict.'],
    },
    literature: {
      summary: 'The reading list centers on citation fidelity, abstention, and evidence-driven evaluation.',
      keyPapers: [
        {
          paperId: 'paper_structured_1',
          title: 'Citation Faithfulness in Retrieval-Augmented Generation',
          source: 'structured',
          relevanceScore: 0.92,
          summary: 'Evidence-aware generation can reduce unsupported claims.',
          methods: ['Verifier'],
          findings: ['Improved fidelity'],
          limitations: ['Does not fully model refusal.'],
          supports: ['coverage gate'],
        },
      ],
      weakOrUnconfirmedPapers: [
        {
          paperId: 'paper_probe_1',
          title: 'Abstention for High-Risk QA',
          source: 'probe',
          relevanceScore: 0.71,
          summary: 'Abstention helps but needs a tighter traceability story.',
          methods: ['Abstain policy'],
          findings: ['Lower hallucination rate'],
          limitations: ['Limited explanation traceability.'],
          supports: ['refusal'],
        },
      ],
    },
    implementationPlan: [
      {
        id: 'stage_1',
        order: 1,
        title: 'Build and verify the evidence gate',
        goal: 'Encode evidence coverage into generation decisions.',
        method: 'Add a verifier and refusal policy.',
        dependsOn: [],
        steps: [
          {
            id: 'step_1',
            order: 1,
            title: 'Collect evidence signals',
            description: 'Aggregate citation and coverage signals before generation.',
            method: 'Compute evidence sufficiency from structured sources.',
            outputs: [
              { type: 'metrics', name: 'coverage_score' },
            ],
            expected: [
              { metric: 'citation_faithfulness', target: '>= 0.80' },
            ],
          },
        ],
      },
      {
        id: 'stage_2',
        order: 2,
        title: 'Evaluate refusal behavior',
        goal: 'Verify the gate does not over-reject.',
        method: 'Run held-out evaluation with refusal metrics.',
        dependsOn: ['stage_1'],
        steps: [
          {
            id: 'step_2',
            order: 1,
            title: 'Measure tradeoffs',
            description: 'Compare faithfulness and answer coverage.',
            method: 'Track refusal accuracy and unsupported claims.',
            outputs: [
              { type: 'report', name: 'evaluation_report' },
            ],
            expected: [
              { metric: 'refusal_accuracy', target: '>= 0.70' },
            ],
          },
        ],
      },
    ],
    evidenceSummary: {
      confidence: 'high',
      summary: 'The plan is grounded in structured evidence and a small probe set.',
      supportingPapers: [
        {
          paperId: 'paper_structured_1',
          title: 'Citation Faithfulness in Retrieval-Augmented Generation',
          source: 'structured',
          relevanceScore: 0.92,
          summary: 'Evidence-aware generation can reduce unsupported claims.',
          methods: ['Verifier'],
          findings: ['Improved fidelity'],
          limitations: ['Does not fully model refusal.'],
          supports: ['coverage gate'],
        },
      ],
      weakPoints: ['Need one more explicit refusal metric.'],
    },
    reviewSummary: {
      decision: 'revise',
      score: 0.78,
      mainConcerns: ['Need one more evaluation metric tied to the hypothesis.'],
      requiredFixes: ['Add a refusal metric and name the evaluation threshold.'],
      reviewerMode: 'hybrid',
      llmReviewerUsed: true,
    },
    nextActions: ['Add a refusal metric.', 'Name the evaluation threshold.'],
    debug: {
      fullPackageEndpoint: '/api/v1/plans/packages/ppkg_001',
      packageId: 'ppkg_001',
      ideaSessionId: 'idea_001',
      ideaCandidateId: 'cand_001',
    },
  }
}

async function waitForPanelLoaded() {
  await screen.findByText('Plan snapshot')
}

describe('PlanGenerationPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    window.localStorage.clear()
    mockNavigate.mockReset()
    vi.mocked(getPlanPackageByIdeaSession).mockResolvedValue(makePlanPackage() as never)
    vi.mocked(getPlanPackagePresentationByIdeaSession).mockResolvedValue(makePresentation() as never)
    vi.mocked(getPlanPackage).mockResolvedValue(makePlanPackage() as never)
    vi.mocked(getPlanPackagePresentation).mockResolvedValue(makePresentation() as never)
    vi.mocked(createPlanPackageFromIdeaSession).mockResolvedValue({ packageId: 'ppkg_001', schemaVersion: 'plan-package/v4', qualityGate: makePlanPackage().qualityGate, package: makePlanPackage() } as never)
    vi.mocked(addPlanPackageFeedback).mockResolvedValue(makePlanPackage() as never)
    vi.mocked(approvePlanPackageWithMode).mockResolvedValue(makePlanPackage() as never)
    vi.mocked(revisePlanPackage).mockResolvedValue(makePlanPackage() as never)
  })

  it('renders a compact workbench with four primary tabs and a single approve/open-code action', async () => {
    render(
      <MemoryRouter>
        <PlanGenerationPanel
          ideaSessionId="idea_001"
          ideaCandidateId="cand_001"
          ideaCandidateTitle="Evidence-aware citation faithfulness for RAG"
          ideaSeedQuery="How can high-risk RAG systems improve citation faithfulness under weak evidence?"
        />
      </MemoryRouter>,
    )

    expect(await screen.findByText('PlanPackage Workspace')).toBeInTheDocument()
    await waitForPanelLoaded()
    expect(screen.getAllByText('How can high-risk RAG systems improve citation faithfulness under weak evidence?').length).toBeGreaterThan(0)
    expect(screen.getAllByText('If evidence coverage is explicitly gated, citation faithfulness improves.').length).toBeGreaterThan(0)

    expect(screen.getByRole('button', { name: /approve.*open code/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /show advanced generation settings/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^code$/i })).not.toBeInTheDocument()
    expect(screen.queryByText('Errors')).not.toBeInTheDocument()
    expect(screen.queryByText('Warnings')).not.toBeInTheDocument()
    expect(screen.queryByText('Quality snapshot')).not.toBeInTheDocument()
    expect(screen.getByText('Plan snapshot')).toBeInTheDocument()
    expect(screen.getByText('Quality and iteration')).toBeInTheDocument()
    expect(screen.getByText('Feedback')).toBeInTheDocument()
    expect(screen.getByText('Next actions')).toBeInTheDocument()

    expect(screen.getByRole('tab', { name: /summary/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /narrative/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /implementation/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /evidence/i })).toBeInTheDocument()

    expect(screen.queryByRole('tab', { name: /overview/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /context/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /literature/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /review/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /json/i })).not.toBeInTheDocument()
  })

  it('expands advanced generation settings on demand', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <PlanGenerationPanel
          ideaSessionId="idea_001"
          ideaCandidateId="cand_001"
          ideaCandidateTitle="Evidence-aware citation faithfulness for RAG"
          ideaSeedQuery="How can high-risk RAG systems improve citation faithfulness under weak evidence?"
        />
      </MemoryRouter>,
    )

    await screen.findByText('PlanPackage Workspace')
    await waitForPanelLoaded()
    expect(screen.queryByLabelText('Generation')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /show advanced generation settings/i }))

    expect(screen.getByLabelText('Generation')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Optional planning constraints for this package')).toBeInTheDocument()
  })

  it('recovers a generated plan after the long request loses its connection', async () => {
    const user = userEvent.setup()
    vi.mocked(getPlanPackageByIdeaSession)
      .mockRejectedValueOnce(new Error('PlanPackage for idea session idea_001 not found'))
      .mockResolvedValue(makePlanPackage() as never)
    vi.mocked(getPlanPackagePresentationByIdeaSession)
      .mockRejectedValueOnce(new Error('PlanPackage for idea session idea_001 not found'))
      .mockResolvedValue(makePresentation() as never)
    vi.mocked(createPlanPackageFromIdeaSession).mockRejectedValueOnce(new TypeError('Failed to fetch'))

    render(
      <MemoryRouter>
        <PlanGenerationPanel
          ideaSessionId="idea_001"
          ideaCandidateId="cand_001"
          ideaCandidateTitle="Evidence-aware citation faithfulness for RAG"
          ideaSeedQuery="How can high-risk RAG systems improve citation faithfulness under weak evidence?"
        />
      </MemoryRouter>,
    )

    const generate = await screen.findByRole('button', { name: /generate planpackage/i })
    await user.click(generate)

    await waitFor(() => {
      expect(createPlanPackageFromIdeaSession).toHaveBeenCalledTimes(1)
      expect(getPlanPackageByIdeaSession).toHaveBeenCalledTimes(2)
      expect(screen.getByText('Plan snapshot')).toBeInTheDocument()
    })
    expect(screen.queryByText('Failed to fetch')).not.toBeInTheDocument()
  })

  it('keeps implementation details collapsed until a stage and step are opened', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <PlanGenerationPanel
          ideaSessionId="idea_001"
          ideaCandidateId="cand_001"
          ideaCandidateTitle="Evidence-aware citation faithfulness for RAG"
          ideaSeedQuery="How can high-risk RAG systems improve citation faithfulness under weak evidence?"
        />
      </MemoryRouter>,
    )

    await screen.findByText('PlanPackage Workspace')
    await waitForPanelLoaded()
    await user.click(screen.getByRole('tab', { name: /implementation/i }))

    expect(screen.getByText('Build and verify the evidence gate')).toBeInTheDocument()
    expect(screen.queryByText('coverage_score')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /stage 1/i }))
    expect(screen.getByText('Collect evidence signals')).toBeInTheDocument()
    expect(screen.queryByText('coverage_score')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /collect evidence signals/i }))
    expect(screen.getByText('coverage_score')).toBeInTheDocument()
    expect(screen.getByText('citation_faithfulness')).toBeInTheDocument()
  })

  it('shows review summary before reviewer committee details in the evidence tab', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <PlanGenerationPanel
          ideaSessionId="idea_001"
          ideaCandidateId="cand_001"
          ideaCandidateTitle="Evidence-aware citation faithfulness for RAG"
          ideaSeedQuery="How can high-risk RAG systems improve citation faithfulness under weak evidence?"
        />
      </MemoryRouter>,
    )

    await screen.findByText('PlanPackage Workspace')
    await waitForPanelLoaded()
    await user.click(screen.getByRole('tab', { name: /evidence/i }))

    expect(screen.getByText('Review snapshot')).toBeInTheDocument()
    expect(screen.getByText('Detailed iteration notes are grouped in the Summary tab so this review view stays focused on the decision and evidence chain.')).toBeInTheDocument()
    expect(screen.queryByText('Need one more evaluation metric tied to the hypothesis.')).not.toBeInTheDocument()
    expect(screen.queryByText('deterministic')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /reviewer committee details/i }))
    expect(screen.getByText('deterministic')).toBeInTheDocument()
  })

  it('keeps idea details and domain context collapsed until opened', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <PlanGenerationPanel
          ideaSessionId="idea_001"
          ideaCandidateId="cand_001"
          ideaCandidateTitle="Evidence-aware citation faithfulness for RAG"
          ideaSeedQuery="How can high-risk RAG systems improve citation faithfulness under weak evidence?"
        />
      </MemoryRouter>,
    )

    await screen.findByText('PlanPackage Workspace')
    await waitForPanelLoaded()
    await user.click(screen.getByRole('tab', { name: /narrative/i }))

    expect(screen.getByText('Deep Think and its advanced variants')).toBeInTheDocument()
    expect(screen.getByText('LLMs have successfully collaborated with advanced AI models to solve open problems and generate new proofs.')).toBeInTheDocument()
    expect(screen.queryByText(/"methodId"/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/"findingId"/i)).not.toBeInTheDocument()

    expect(screen.queryByText('Add a verifier that conditions generation on evidence coverage.')).not.toBeInTheDocument()
    expect(screen.queryByText('Evidence-grounded generation')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /idea details/i }))
    expect(screen.getByText('Add a verifier that conditions generation on evidence coverage.')).toBeInTheDocument()
    expect(screen.getByText('Higher citation faithfulness with fewer unsupported claims.')).toBeInTheDocument()
    expect(screen.getByText('Shows that retrieval coverage should govern answer emission.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /domain context/i }))
    expect(screen.getByText('Evidence-grounded generation')).toBeInTheDocument()
  })

  it('approves the handoff and opens the code workspace', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <PlanGenerationPanel
          ideaSessionId="idea_001"
          ideaCandidateId="cand_001"
          ideaCandidateTitle="Evidence-aware citation faithfulness for RAG"
          ideaSeedQuery="How can high-risk RAG systems improve citation faithfulness under weak evidence?"
        />
      </MemoryRouter>,
    )

    await screen.findByText('PlanPackage Workspace')
    await waitForPanelLoaded()
    await user.click(screen.getByRole('button', { name: /approve.*open code/i }))

    await waitFor(() => {
      expect(approvePlanPackageWithMode).toHaveBeenCalledWith('ppkg_001', 'hybrid')
      expect(mockNavigate).toHaveBeenCalledWith('/code/workspace?packageId=ppkg_001')
    })
  })
})
