import { describe, expect, it } from 'vitest'
import { exampleKey, loadExampleSet, type CortexCall, type ExampleSet } from '../../server/utils/examples'

const body = { text_ja: 'Never issue an invoice without the registration number.', targets: ['invoice.registration_number'], condition: 'true' }
const set: ExampleSet = {
  key: 'en', label: 'English', tenant: 'en', scope: 'en/finance/q2c-invoicing', domain: 'custom_invoices',
  purpose: 'invoice_drafting', note: '',
  base_rules: [{ key: 'EN-G-NO-REGNO-OMIT', scope: 'en', enforcement: 'prohibition', authority: 30, by: 'EN-R-002', approver: 'EN-OWN-001', body }],
  case: { case_id: 'EX-INV-001', customer_id: 'CUST-0300', channel: 'coupa' },
  correction: { reviewer: 'EN-R-001', changes: [{ target: 'invoice.remarks.po_number' }], note_ja: 'PO number in the remarks.' },
  proposal: { by: 'EN-R-002', enforcement: 'default', authority: 10, body },
  import: { by: 'EN-R-001', source_system: 'legacy-rule-list', authority: 20, valid_to: '2027-01-31', body }
}
const ids = { node: (k: string) => `node:${k}`, person: (k: string) => `person:${k}` }

function api(answers: Record<string, { status: number, body: unknown }>) {
  const calls: Array<{ method: string, path: string, as: string, user?: string, body?: any }> = [] // eslint-disable-line @typescript-eslint/no-explicit-any
  let imports = 0
  const call: CortexCall = async (method, path, options) => {
    calls.push({ method, path, ...options })
    const key = path === '/imports' ? `/imports#${imports++}` : path.replace(/review-cases\/[^/]+/, 'review-cases/*')
    return answers[key] ?? { status: 500, body: { detail: `no answer for ${key}` } }
  }
  return { call, calls }
}

describe('example sets', () => {
  it('only an exact known key selects the English set', () => {
    expect(exampleKey('en')).toBe('en')
    expect(exampleKey('EN')).toBe('ja')
    expect(exampleKey(undefined)).toBe('ja')
    expect(exampleKey(['en'])).toBe('ja')
  })

  it('imports a group rule, has its owner approve it, then creates the three cases to review', async () => {
    const { call, calls } = api({
      '/imports#0': { status: 200, body: { items: [{ status: 'proposed', record_id: 'r1', review_case_id: 'c1' }] } },
      '/review-cases/*/approve': { status: 200, body: {} },
      '/context-packs': { status: 201, body: { pack_id: 'p1' } },
      '/corrections': { status: 202, body: { replayed: false, proposed_record_id: 'r2' } },
      '/records': { status: 201, body: { grouped: false, warnings: [{}, {}] } },
      '/imports#1': { status: 200, body: { items: [{ status: 'proposed' }] } }
    })

    const steps = await loadExampleSet(set, ids, call)

    expect(steps.map(s => [s.step, s.outcome])).toEqual([
      ['group rule EN-G-NO-REGNO-OMIT', 'created'],
      ['correction', 'created'],
      ['local rule that loosens a group rule', 'created'],
      ['import', 'created']
    ])
    const approve = calls.find(c => c.path.endsWith('/approve'))!
    expect(approve).toMatchObject({ as: 'desk', user: 'person:EN-OWN-001', body: { record_id: 'r1', authority: 30, enforcement: 'prohibition' } })
    expect(calls[0]).toMatchObject({ as: 'agent', user: 'person:EN-R-002' })
    expect(calls[0]!.body.items[0]).toMatchObject({ scope: 'node:en', enforcement: 'prohibition' })
    const correction = calls.find(c => c.path === '/corrections')!
    expect(correction.body).toMatchObject({ pack_id: 'p1', idempotency_key: 'examples-en-correction-1' })
    expect(calls.at(-1)!.body.items[0]).toMatchObject({ valid_to: '2027-01-31', scope: 'node:en/finance/q2c-invoicing' })
  })

  it('a second load creates nothing new and says so', async () => {
    const { call } = api({
      '/imports#0': { status: 200, body: { items: [{ status: 'duplicate' }] } },
      '/context-packs': { status: 201, body: { pack_id: 'p2' } },
      '/corrections': { status: 409, body: { error: 'conflict', detail: 'this idempotency key was already used for a different correction' } },
      '/records': { status: 201, body: { grouped: true, already_pending: true, warnings: [] } },
      '/imports#1': { status: 200, body: { items: [{ status: 'pending', review_case_id: 'c9' }] } }
    })

    const steps = await loadExampleSet(set, ids, call)

    expect(steps.every(s => s.outcome === 'already_there')).toBe(true)
  })

  it('reports a refusal instead of stopping', async () => {
    const { call } = api({
      '/imports#0': { status: 403, body: { error: 'access_denied', detail: 'no role grants memory.write' } },
      '/context-packs': { status: 201, body: { pack_id: 'p3' } },
      '/corrections': { status: 202, body: { replayed: false } },
      '/records': { status: 201, body: { grouped: false } },
      '/imports#1': { status: 200, body: { items: [{ status: 'proposed' }] } }
    })

    const steps = await loadExampleSet(set, ids, call)

    expect(steps[0]).toMatchObject({ outcome: 'failed', detail: '403: "no role grants memory.write"' })
    expect(steps.slice(1).every(s => s.outcome === 'created')).toBe(true)
  })
})
