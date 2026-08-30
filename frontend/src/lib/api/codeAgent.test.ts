import { afterEach, describe, expect, it, vi } from 'vitest'

import { parseCartRunIssue, streamCartRun } from './codeAgent'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('parseCartRunIssue', () => {
  it('preserves a scientific execution gate as an actionable blocker', () => {
    const issue = parseCartRunIssue(409, JSON.stringify({
      detail: {
        code: 'SCIENTIFIC_EXECUTION_BLOCKED',
        message: 'A versioned dataset is required.',
        missingInputs: ['versioned dataset or corpus'],
        suggestedActions: ['Add data/manifest.json.'],
        assessment: { executionClass: 'data_required' },
      },
    }))

    expect(issue).toEqual({
      kind: 'blocked',
      status: 409,
      code: 'SCIENTIFIC_EXECUTION_BLOCKED',
      message: 'A versioned dataset is required.',
      executionClass: 'data_required',
      missingInputs: ['versioned dataset or corpus'],
      suggestedActions: ['Add data/manifest.json.'],
    })
  })

  it('keeps non-JSON proxy failures displayable without calling them blockers', () => {
    const issue = parseCartRunIssue(502, '<html>Bad Gateway</html>')

    expect(issue.kind).toBe('api')
    expect(issue.status).toBe(502)
    expect(issue.message).toContain('Bad Gateway')
    expect(issue.missingInputs).toEqual([])
  })

  it('reports an HTTP blocker once instead of treating it as a dropped stream', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 409,
      text: async () => JSON.stringify({
        detail: {
          code: 'SCIENTIFIC_EXECUTION_BLOCKED',
          message: 'Dataset missing.',
          missingInputs: ['dataset'],
        },
      }),
    } as Response)

    const issue = await new Promise<Parameters<Parameters<typeof streamCartRun>[2]>[0]>((resolve) => {
      streamCartRun({ projectId: 'project-1' }, vi.fn(), resolve)
    })

    expect(issue?.kind).toBe('blocked')
    expect(issue?.message).toBe('Dataset missing.')
  })
})
