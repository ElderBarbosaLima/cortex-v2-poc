// Example sets (issue 39): the same review story in Japanese and in English,
// each in its own tenant. Pure helpers, kept free of h3 so they are unit-tested.
// Dev-only: the routes that use them answer 404 without NUXT_DEV_EXAMPLES_DIR.

export const EXAMPLE_KEYS = ['ja', 'en'] as const
export type ExampleKey = typeof EXAMPLE_KEYS[number]

/** Only an exact, known key selects a set; anything else is the default (Japanese). */
export function exampleKey(value: unknown): ExampleKey {
  return value === 'en' ? 'en' : 'ja'
}

interface RuleBody { text_ja: string, targets: string[], condition: string, [field: string]: unknown }

export interface ExampleWorkflow {
  label: string
  scope: string
  domain: string
  purpose: string
  base_rules: Array<{ key: string, scope: string, enforcement: string, authority: number, by: string, approver: string, body: RuleBody }>
  /** Invoices: one case and a correction. Cash: several cases and the same outcome each time. */
  case?: Record<string, unknown> & { case_id: string }
  cases?: Array<Record<string, unknown> & { case_id: string }>
  correction?: { reviewer: string, changes: unknown[], note_ja: string }
  outcome?: { reviewer: string, [field: string]: unknown }
  proposal: { by: string, enforcement: string, authority: number, body: RuleBody, entities?: string[] }
  import: { by: string, source_system: string, authority: number, valid_to: string, body: RuleBody, entities?: string[] }
}

export interface ExampleSet {
  key: ExampleKey
  label: string
  tenant: string
  note: string
  workflows: ExampleWorkflow[]
}

export interface ExampleStep { step: string, outcome: 'created' | 'already_there' | 'failed', detail: string }

export interface Ids { node: (key: string) => string, person: (key: string) => string }

export type CortexCall = (
  method: 'GET' | 'POST',
  path: string,
  options: { as: 'agent' | 'desk', user?: string, body?: unknown }
) => Promise<{ status: number, body: any }> // eslint-disable-line @typescript-eslint/no-explicit-any

const VALID_FROM = '2026-11-01'
const AS_OF = '2026-12-04'

function ruleItem(w: ExampleWorkflow, ids: Ids, scope: string, body: RuleBody, enforcement: string, authority: number,
  opts: { validTo?: string, entities?: string[] } = {}) {
  return {
    domain: w.domain,
    kind: 'rule',
    schema_version: `${w.domain}.rule/1`,
    body,
    scope: ids.node(scope),
    entities: opts.entities ?? [],
    enforcement,
    authority,
    sensitivity: { class: 'internal' },
    purpose: w.purpose,
    valid_from: VALID_FROM,
    ...(opts.validTo ? { valid_to: opts.validTo } : {})
  }
}

function failed(step: string, status: number, body: unknown): ExampleStep {
  const b = (typeof body === 'object' && body !== null ? body : {}) as { detail?: unknown, problems?: unknown }
  const why = b.detail ?? (Array.isArray(b.problems) && b.problems.length ? b.problems : undefined)
  const detail = why === undefined ? String(status) : JSON.stringify(why)
  return { step, outcome: 'failed', detail: `${status}: ${detail}`.slice(0, 300) }
}

async function groupRules(w: ExampleWorkflow, ids: Ids, call: CortexCall, steps: ExampleStep[]): Promise<void> {
  for (const rule of w.base_rules) {
    const name = `${w.label} · group rule ${rule.key}`
    const imported = await call('POST', '/imports', {
      as: 'agent',
      user: ids.person(rule.by),
      body: { source_system: 'example-group-rules', items: [ruleItem(w, ids, rule.scope, rule.body, rule.enforcement, rule.authority)] }
    })
    const item = imported.body?.items?.[0]
    if (imported.status !== 200 || !item) {
      steps.push(failed(name, imported.status, imported.body))
      continue
    }
    if (item.status === 'duplicate') {
      steps.push({ step: name, outcome: 'already_there', detail: 'approved before' })
      continue
    }
    if (item.status !== 'proposed' && item.status !== 'pending') {
      steps.push(failed(name, imported.status, item))
      continue
    }
    const approved = await call('POST', `/review-cases/${item.review_case_id}/approve`, {
      as: 'desk',
      user: ids.person(rule.approver),
      body: { record_id: item.record_id, authority: rule.authority, enforcement: rule.enforcement }
    })
    steps.push(approved.status === 200
      ? { step: name, outcome: 'created', detail: `approved by ${rule.approver}` }
      : failed(name, approved.status, approved.body))
  }
}

async function corrections(w: ExampleWorkflow, setKey: string, ids: Ids, call: CortexCall, steps: ExampleStep[]): Promise<void> {
  const cases = w.cases ?? (w.case ? [w.case] : [])
  const name = cases.length > 1 ? `${w.label} · the same fix on ${cases.length} payments` : `${w.label} · correction`
  let learned = false
  let replayed = 0
  for (const [n, c] of cases.entries()) {
    const value = (c.payment as { value_date?: string } | undefined)?.value_date ?? AS_OF
    const pack = await call('POST', '/context-packs', {
      as: 'agent',
      body: { domain: w.domain, case: c, scope: ids.node(w.scope), purpose: w.purpose, as_of: value }
    })
    if (pack.status !== 201) {
      steps.push(failed(name, pack.status, pack.body))
      return
    }
    const reviewer = (w.outcome ?? w.correction)!.reviewer
    const correction = w.outcome
      ? { ...w.outcome, correction_id: `EX-O-${setKey}-${n}`, case_id: c.case_id, corrected_at: `${AS_OF}T15:00:00+09:00` }
      : { correction_id: `EX-C-${setKey}-1`, case_id: c.case_id, reviewer, corrected_at: `${AS_OF}T15:00:00+09:00`,
          changes: w.correction!.changes, note_ja: w.correction!.note_ja }
    const sent = await call('POST', w.outcome ? '/outcomes' : '/corrections', {
      as: 'agent',
      user: ids.person(reviewer),
      body: { pack_id: pack.body.pack_id, domain: w.domain, case_id: c.case_id, case: c, correction,
        idempotency_key: `examples-${setKey}-${w.domain}-${n}` }
    })
    if (sent.status === 202) {
      if (sent.body.replayed) replayed += 1
      if (sent.body.proposed_record_id) learned = true
    } else if (sent.status === 409) {
      replayed += 1 // submitted by an earlier load: its key now names that one
    } else {
      steps.push(failed(name, sent.status, sent.body))
      return
    }
  }
  steps.push(replayed === cases.length
    ? { step: name, outcome: 'already_there', detail: 'submitted by an earlier load' }
    : { step: name, outcome: 'created', detail: learned ? 'a learned rule to decide' : 'recorded' })
}

/**
 * Load one set through the public API, as an agent and the people of its
 * tenant would, workflow by workflow: group rules imported and approved by
 * their owner, then corrections (or the same outcome repeated up to the
 * pattern threshold), a local rule that loosens a group rule, and an import.
 * Idempotent: duplicates and replays are reported as already there.
 */
export async function loadExampleSet(set: ExampleSet, ids: Ids, call: CortexCall): Promise<ExampleStep[]> {
  const steps: ExampleStep[] = []
  for (const w of set.workflows) {
    await groupRules(w, ids, call, steps)
    await corrections(w, set.key, ids, call, steps)

    const proposed = await call('POST', '/records', {
      as: 'agent',
      user: ids.person(w.proposal.by),
      body: ruleItem(w, ids, w.scope, w.proposal.body, w.proposal.enforcement, w.proposal.authority, { entities: w.proposal.entities })
    })
    const loosens = `${w.label} · local rule that loosens a group rule`
    steps.push(proposed.status === 201
      ? { step: loosens, outcome: proposed.body.already_pending ? 'already_there' : 'created', detail: `${(proposed.body.warnings ?? []).length} warning(s)` }
      : failed(loosens, proposed.status, proposed.body))

    const imported = await call('POST', '/imports', {
      as: 'agent',
      user: ids.person(w.import.by),
      body: {
        source_system: w.import.source_system,
        items: [ruleItem(w, ids, w.scope, w.import.body, 'default', w.import.authority, { validTo: w.import.valid_to, entities: w.import.entities })]
      }
    })
    const item = imported.body?.items?.[0]
    steps.push(imported.status === 200 && item && item.status !== 'rejected'
      ? { step: `${w.label} · import`, outcome: item.status === 'proposed' ? 'created' : 'already_there', detail: `valid until ${w.import.valid_to}` }
      : failed(`${w.label} · import`, imported.status, item ?? imported.body))
  }
  return steps
}
