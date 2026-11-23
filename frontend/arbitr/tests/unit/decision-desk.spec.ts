import { mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DecisionDesk from '~/pages/cortex/decision-desk.vue'
import { useDecisionDeskStore } from '~/stores/decisionDesk'

const OWNER = 'owner-uuid'
const CORRECTOR = 'corrector-uuid'
const SCOPE = 'scope-uuid'
const CASE = 'case-uuid'
const TEST_ID = /^cortex-decision-desk-[a-z0-9]+(?:-[a-z0-9]+)*-(button|select|input|textarea|link|checkbox)$/

const seed = {
  people: [
    { key: 'R-001', label: '請求書レビュアー 1', role: 'reviewer', id: CORRECTOR, tenantId: 't' },
    { key: 'OWN-001', label: 'ルール責任者', role: 'final_validator', id: OWNER, tenantId: 't' }
  ],
  scopes: [{ key: 'jp/finance/q2c-invoicing', name: 'Q2C 請求書チーム', id: SCOPE, type: 'team', tenantId: 't' }]
}

function imported(approve: { allowed: boolean, reason: string | null }) {
  const base = detail(approve)
  base.proposals[0] = {
    ...base.proposals[0]!,
    asserted_by: { type: 'import', id: 'legacy-rule-list', by: CORRECTOR },
    source: { import: 'legacy-rule-list', requested_authority: 20 },
    valid_from: '2026-10-31T15:00:00Z',
    valid_to: '2027-03-30T15:00:00Z',
    inherited_conflicts: [{
      record_id: 'ban-1', enforcement: 'prohibition', text_ja: '登録番号を記載しない請求書は発行しない。',
      targets: ['invoice.registration_number']
    }]
  } as typeof base.proposals[0]
  return base
}

function detail(approve: { allowed: boolean, reason: string | null }) {
  return {
    case_id: CASE, scope: SCOPE, title: 'CUST-0042 の coupa 請求書では、注文番号を備考に入れる', status: 'open',
    opened_at: '2026-12-04T06:00:00Z', proposal_count: 1, sensitivity_class: 'internal',
    named_owners: { owner: OWNER, backup: null },
    proposals: [{
      record_id: 'rec-1', version: 1, kind: 'rule', status: 'proposed', scope: SCOPE, enforcement: 'default',
      authority: 10, sensitivity_class: 'internal', outcome: 'pending', asserted_by: { type: 'policy', id: 'd17' },
      approved_by: null, body_hash: 'h', entities: ['customer:CUST-0042'], derived_from: [],
      valid_from: '1900-01-01T00:00:00+09:00', valid_to: null,
      body: { text_ja: '注文番号を備考に入れる', targets: ['invoice.remarks.po_number'], condition: 'invoice.customer_id == "CUST-0042"' },
      source: { case_refs: ['INV-0007'], contributors: [CORRECTOR] }
    }],
    can_decide: { approve: { ...approve, policy_revision: 'sha256:x' }, reject: { ...approve, policy_revision: 'sha256:x' } },
    affected_cases: ['INV-0007'], contributors: [CORRECTOR], decisions: [], policy_revision: 'sha256:x'
  }
}

let routes: Array<[RegExp, number, unknown]> = []
const calls: Array<{ url: string, init?: RequestInit }> = []

beforeEach(() => {
  useDecisionDeskStore().$reset()
  calls.length = 0
  routes = [
    [/^\/api\/dev\/seed$/, 200, seed],
    [/^\/api\/cortex\/review-cases\?/, 200, { items: [{ case_id: CASE, scope: SCOPE, title: '注文番号を備考に入れる', status: 'open', opened_at: '2026-12-04T06:00:00Z', proposal_count: 1, sensitivity_class: 'internal', named_owners: null }], next_cursor: null, policy_revision: null }],
    [/^\/api\/cortex\/review-cases\/case-uuid\?/, 200, detail({ allowed: true, reason: null })],
    [/^\/api\/cortex\/records\/expiring\?/, 200, { items: [{
      record_id: 'exp-1', version: 2, kind: 'rule', status: 'approved', scope: SCOPE, enforcement: 'default',
      authority: 20, sensitivity_class: 'internal', valid_to: '2027-03-30T15:00:00Z', expired: false,
      title: '備考に注文番号を記載する（全顧客）。'
    }], within_days: 90, policy_revision: null }]
  ]
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    const hit = routes.find(([re]) => re.test(url))
    if (!hit) return new Response(JSON.stringify({ error: 'not_found', detail: url }), { status: 404 })
    return new Response(JSON.stringify(hit[2]), { status: hit[1] })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function openCase() {
  const page = await mountSuspended(DecisionDesk)
  await vi.waitFor(() => expect(page.find('[data-test-id="cortex-decision-desk-case-item-button"]').exists()).toBe(true))
  const before = calls.filter(c => /review-cases\/case-uuid\?/.test(c.url)).length
  await page.get('[data-test-id="cortex-decision-desk-case-item-button"]').trigger('click')
  await vi.waitFor(() => expect(calls.filter(c => /review-cases\/case-uuid\?/.test(c.url)).length).toBe(before + 1))
  await flushPromises()
  await vi.waitFor(() => expect(page.find('[data-test-id="cortex-decision-desk-detail-panel"]').exists()).toBe(true))
  return page
}

describe('the Decision Desk', () => {
  it('shows the proposal with its evidence and its outcome', async () => {
    const page = await openCase()
    const panel = page.get('[data-test-id="cortex-decision-desk-detail-panel"]').text()

    expect(panel).toContain('invoice.customer_id == "CUST-0042"')
    expect(panel).toContain('customer:CUST-0042')
    expect(panel).toContain('INV-0007')
    expect(panel).toContain('請求書レビュアー 1') // the corrector, by label
    expect(page.get('[data-test-id="cortex-decision-desk-outcome-badge"]').text()).toBe('Pending')
  })

  it('lets the named owner approve with the final authority and enforcement', async () => {
    const page = await openCase()
    routes.unshift([/^\/api\/cortex\/review-cases\/case-uuid\/approve$/, 200, { decision_id: 'd', case_id: CASE, decision: 'approve', records: [], policy_revision: null }])

    await page.get('[data-test-id="cortex-decision-desk-enforcement-select"]').setValue('mandatory')
    await page.get('[data-test-id="cortex-decision-desk-authority-input"]').setValue('30')
    await page.get('[data-test-id="cortex-decision-desk-approve-button"]').trigger('click')
    await flushPromises()

    const post = calls.find(c => c.url.endsWith('/approve'))!
    expect(JSON.parse(String(post.init?.body))).toEqual({ authority: 30, enforcement: 'mandatory', reason: null })
    expect((post.init?.headers as Record<string, string>)['x-end-user-id']).toBe(OWNER)
    await vi.waitFor(() => expect(page.find('[data-test-id="cortex-decision-desk-notice-text"]').exists()).toBe(true))
  })

  it('shows the corrector a disabled approve button with the policy reason', async () => {
    routes[2] = [/^\/api\/cortex\/review-cases\/case-uuid\?/, 200, detail({
      allowed: false, reason: 'separation of duties: a contributor to this record cannot decide on it'
    })]
    const page = await openCase()
    const approve = page.get('[data-test-id="cortex-decision-desk-approve-button"]')

    expect(approve.attributes('disabled')).toBeDefined()
    expect(approve.attributes('aria-describedby')).toBe('cortex-desk-approve-denied')
    expect(page.get('[data-test-id="cortex-decision-desk-approve-denied-text"]').text())
      .toBe('You cannot approve: separation of duties: a contributor to this record cannot decide on it')
  })

  it('shows what Cortex refused, with the reason', async () => {
    const page = await openCase()
    routes.unshift([/\/approve$/, 403, { error: 'access_denied', detail: 'only the named owner or backup of this scope can decide', policy_revision: 'sha256:x' }])

    await page.get('[data-test-id="cortex-decision-desk-approve-button"]').trigger('click')
    await vi.waitFor(() => expect(page.find('[data-test-id="cortex-decision-desk-problem-alert"]').exists()).toBe(true))
    expect(page.get('[data-test-id="cortex-decision-desk-problem-alert"]').text())
      .toContain('only the named owner or backup of this scope can decide')
  })

  it('needs a reason to reject', async () => {
    const page = await openCase()
    const reject = page.get('[data-test-id="cortex-decision-desk-reject-button"]')
    expect(reject.attributes('disabled')).toBeDefined()

    await page.get('[data-test-id="cortex-decision-desk-reason-textarea"]').setValue('根拠不足')
    expect(page.get('[data-test-id="cortex-decision-desk-reject-button"]').attributes('disabled')).toBeUndefined()
  })

  it('says in words who proposed an import, what it asked for, and its validity (issue 38)', async () => {
    routes[2] = [/^\/api\/cortex\/review-cases\/case-uuid\?/, 200, imported({ allowed: true, reason: null })]
    const page = await openCase()

    expect(page.get('[data-test-id="cortex-decision-desk-provenance-text"]').text())
      .toContain('Imported from legacy-rule-list by 請求書レビュアー 1')
    expect(page.get('[data-test-id="cortex-decision-desk-provenance-text"]').text())
      .toContain('Asked for authority 20; an import is capped at 10')
    expect(page.get('[data-test-id="cortex-decision-desk-validity-text"]').text()).toBe('2026-11-01 → 2027-03-31')
    expect(page.get('[data-test-id="cortex-decision-desk-loosens-text"]').text())
      .toContain('inherited prohibition rule, which keeps winning when packs are built: 「登録番号を記載しない請求書は発行しない。」 (invoice.registration_number)')
  })

  it('names a learned rule\'s model and correctors, and shows no start date as always', async () => {
    const page = await openCase()

    expect(page.get('[data-test-id="cortex-decision-desk-provenance-text"]').text())
      .toBe('Drafted by the pack\'s rule from case(s) INV-0007, corrected by 請求書レビュアー 1')
    expect(page.get('[data-test-id="cortex-decision-desk-validity-text"]').text()).toBe('always → no end date')
  })

  it('sends validity only when the owner changes it', async () => {
    routes[2] = [/^\/api\/cortex\/review-cases\/case-uuid\?/, 200, imported({ allowed: true, reason: null })]
    const page = await openCase()
    routes.unshift([/\/approve$/, 200, { decision_id: 'd', case_id: CASE, decision: 'approve', records: [], policy_revision: null }])

    expect((page.get('[data-test-id="cortex-decision-desk-valid-to-input"]').element as HTMLInputElement).value).toBe('2027-03-31')
    await page.get('[data-test-id="cortex-decision-desk-valid-to-input"]').setValue('2027-06-30')
    await page.get('[data-test-id="cortex-decision-desk-approve-button"]').trigger('click')
    await flushPromises()

    const post = calls.find(c => c.url.endsWith('/approve'))!
    expect(JSON.parse(String(post.init?.body))).toEqual({ authority: 20, enforcement: 'default', reason: null, valid_to: '2027-06-30' })
  })

  it('clears the last notice when you act as someone else', async () => {
    const page = await openCase()
    routes.unshift([/\/approve$/, 200, { decision_id: 'd', case_id: CASE, decision: 'approve', records: [], policy_revision: null }])
    await page.get('[data-test-id="cortex-decision-desk-approve-button"]').trigger('click')
    await vi.waitFor(() => expect(page.find('[data-test-id="cortex-decision-desk-notice-text"]').exists()).toBe(true))

    await page.get('[data-test-id="cortex-decision-desk-person-select"]').setValue(CORRECTOR)
    await flushPromises()

    expect(page.find('[data-test-id="cortex-decision-desk-notice-text"]').exists()).toBe(false)
  })

  it('lists what expires soon in the scope', async () => {
    const page = await mountSuspended(DecisionDesk)
    await vi.waitFor(() => expect(page.find('[data-test-id="cortex-decision-desk-expiring-item-text"]').exists()).toBe(true))

    expect(page.get('[data-test-id="cortex-decision-desk-expiring-item-text"]').text())
      .toContain('備考に注文番号を記載する（全顧客）。')
    expect(page.get('[data-test-id="cortex-decision-desk-expiring-item-text"]').text()).toContain('until 2027-03-31')
  })

  it('gives every control a cortex-decision-desk test id', async () => {
    const page = await openCase()
    const controls = page.element.querySelectorAll('button, input, select, textarea, a, [role="button"]')
    const missing = [...controls]
      .map(el => el.getAttribute('data-test-id') ?? `<${el.tagName.toLowerCase()} without data-test-id>`)
      .filter(id => !TEST_ID.test(id))

    expect(controls.length).toBeGreaterThan(5)
    expect(missing).toEqual([])
  })
})
