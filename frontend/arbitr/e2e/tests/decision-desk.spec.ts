// Decision Desk end to end (issue 28), against the running dev stack:
//   1. a reviewer's correction (API) → the case appears → the named owner
//      approves it on screen → the rule is in the next applicable case's pack;
//   2. the corrector sees the approve button disabled, with the policy reason.
// Setup goes through the real API as the agent (CORTEX_E2E_AGENT_KEY); the
// Decision Desk's own calls go through the frontend's proxy, as in the browser.
import { readFileSync } from 'node:fs'
import { expect, request, test, type APIRequestContext } from '@playwright/test'

const DATA = process.env.CORTEX_E2E_DATA_DIR ?? '/data'
const API_URL = process.env.CORTEX_E2E_API_URL ?? 'http://aws-emulator:5008'
const BASE_URL = process.env.CORTEX_E2E_BASE_URL ?? 'http://frontend:5001'
const AGENT_KEY = process.env.CORTEX_E2E_AGENT_KEY ?? ''

type Json = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

const tenant: Json = JSON.parse(readFileSync(`${DATA}/tenant.json`, 'utf8'))
const jsonl = (path: string): Json[] =>
  readFileSync(path, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line))
const CASES = new Map(jsonl(`${DATA}/custom_invoices/cases.jsonl`).map(c => [c.case_id, c]))
const CORRECTIONS = new Map(jsonl(`${DATA}/custom_invoices/corrections.jsonl`).map(c => [c.correction_id, c]))
const EXPECTED = new Map((JSON.parse(readFileSync(`${DATA}/custom_invoices/expected.json`, 'utf8')) as Json[]).map(e => [e.case_id, e]))

const node = (key: string): string => tenant.nodes.find((n: Json) => n.key === key).obj_id
const person = (key: string): string => tenant.people.find((p: Json) => p.key === key).obj_id
const SCOPE = node('jp/finance/q2c-invoicing')

let agent: APIRequestContext
let desk: APIRequestContext

test.beforeAll(async () => {
  expect(AGENT_KEY, 'set CORTEX_E2E_AGENT_KEY (.env)').not.toBe('')
  agent = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { authorization: `Bearer ${AGENT_KEY}` } })
  desk = await request.newContext({ baseURL: `${BASE_URL}/api/cortex/` })
})

test.afterAll(async () => {
  await rejectOpenCases()
  await agent.dispose()
  await desk.dispose()
})

/** Start each test from no open case at this scope (earlier runs leave theirs). */
async function rejectOpenCases(): Promise<void> {
  const headers = { 'x-end-user-id': person('OWN-001') }
  const page = await desk.get(`review-cases?scope=${SCOPE}&purpose=rule_review&status=open&limit=100`, { headers })
  expect(page.ok(), await page.text()).toBeTruthy()
  for (const item of (await page.json()).items as Json[]) {
    const done = await desk.post(`review-cases/${item.case_id}/reject`, { headers, data: { reason: 'e2e reset' } })
    expect(done.ok(), await done.text()).toBeTruthy()
  }
}

async function pack(caseId: string, asOf?: string): Promise<Json> {
  const resp = await agent.post('/v1/cortex/context-packs', {
    data: {
      domain: 'custom_invoices',
      case: CASES.get(caseId),
      scope: SCOPE,
      purpose: 'invoice_drafting',
      as_of: asOf ?? EXPECTED.get(caseId)!.as_of
    }
  })
  expect(resp.status(), await resp.text()).toBe(201)
  return resp.json()
}

/** A reviewer corrects the agent's output on `caseId` (through the API). */
async function correct(caseId: string, correctionId: string, reviewer: string): Promise<Json> {
  const served = await pack(caseId)
  const resp = await agent.post('/v1/cortex/corrections', {
    headers: { 'x-end-user-id': person(reviewer) },
    data: {
      pack_id: served.pack_id,
      domain: 'custom_invoices',
      case_id: caseId,
      case: CASES.get(caseId),
      correction: CORRECTIONS.get(correctionId),
      idempotency_key: `e2e-${correctionId}-${Date.now()}`
    }
  })
  expect(resp.status(), await resp.text()).toBe(202)
  const body = await resp.json()
  expect(body.review_case_id).toBeTruthy()
  return body
}

async function openDesk(page: import('@playwright/test').Page, actingAs: string, caseId: string): Promise<void> {
  await page.goto('/cortex/decision-desk')
  await page.getByTestId('cortex-decision-desk-person-select').selectOption(person(actingAs))
  await page.getByTestId('cortex-decision-desk-scope-select').selectOption(SCOPE)
  await page.getByTestId('cortex-decision-desk-status-select').selectOption('open')
  const item = page.locator(`[data-test-id="cortex-decision-desk-case-item-button"][data-case-id="${caseId}"]`)
  await expect(item).toBeVisible()
  await item.click()
  await expect(page.getByTestId('cortex-decision-desk-detail-panel')).toBeVisible()
}

test.beforeEach(async () => {
  await rejectOpenCases()
})

test('a correction becomes a case, the owner approves it, and the next pack carries the rule', async ({ page }) => {
  const corrected = await correct('INV-0007', 'C-001', 'R-001')

  await openDesk(page, 'OWN-001', corrected.review_case_id)
  await expect(page.getByTestId('cortex-decision-desk-detail-panel')).toContainText('invoice.customer_id == "CUST-0042"')
  await expect(page.getByTestId('cortex-decision-desk-outcome-badge').first()).toHaveText('Pending')
  await page.getByTestId('cortex-decision-desk-enforcement-select').selectOption('mandatory')
  await page.getByTestId('cortex-decision-desk-authority-input').fill('30')
  await page.getByTestId('cortex-decision-desk-approve-button').click()

  await expect(page.getByTestId('cortex-decision-desk-notice-text')).toBeVisible()
  await page.getByTestId('cortex-decision-desk-status-select').selectOption('decided')
  await page.locator(`[data-test-id="cortex-decision-desk-case-item-button"][data-case-id="${corrected.review_case_id}"]`).click()
  await expect(page.getByTestId('cortex-decision-desk-outcome-badge').first()).toHaveText('Approved')

  // The next applicable case (CUST-0042, Coupa, after the approval): the rule is a constraint now.
  const next = await pack('INV-0021')
  const rule = (next.records as Json[]).find(r => r.record_id === corrected.proposed_record_id)
  expect(rule?.role).toBe('constraint')
  // …and not in an unrelated one (CUST-0042 by email).
  const unrelated = await pack('INV-0024')
  expect((unrelated.records as Json[]).map(r => r.record_id)).not.toContain(corrected.proposed_record_id)
})

test('the corrector sees approve disabled, with the policy reason', async ({ page }) => {
  const corrected = await correct('INV-0012', 'C-002', 'R-002')

  await openDesk(page, 'R-002', corrected.review_case_id)
  await expect(page.getByTestId('cortex-decision-desk-approve-button')).toBeDisabled()
  await expect(page.getByTestId('cortex-decision-desk-approve-denied-text'))
    .toHaveText('You cannot approve: separation of duties: a contributor to this record cannot decide on it')

  // The owner, on the same case, may decide.
  await openDesk(page, 'OWN-001', corrected.review_case_id)
  await expect(page.getByTestId('cortex-decision-desk-approve-button')).toBeEnabled()
})
