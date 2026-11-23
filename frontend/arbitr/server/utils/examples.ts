// Example sets (issue 39): the same review story in Japanese and in English,
// each in its own tenant. Pure helpers, kept free of h3 so they are unit-tested.
// Dev-only: the routes that use them answer 404 without NUXT_DEV_EXAMPLES_DIR.

export const EXAMPLE_KEYS = ['ja', 'en'] as const
export type ExampleKey = typeof EXAMPLE_KEYS[number]

/** Only an exact, known key selects a set; anything else is the default (Japanese). */
export function exampleKey(value: unknown): ExampleKey {
  return value === 'en' ? 'en' : 'ja'
}

interface RuleBody { text_ja: string, targets: string[], condition: string }

export interface ExampleSet {
  key: ExampleKey
  label: string
  tenant: string
  scope: string
  domain: string
  purpose: string
  note: string
  base_rules: Array<{ key: string, scope: string, enforcement: string, authority: number, by: string, approver: string, body: RuleBody }>
  case: Record<string, unknown> & { case_id: string }
  correction: { reviewer: string, changes: unknown[], note_ja: string }
  proposal: { by: string, enforcement: string, authority: number, body: RuleBody }
  import: { by: string, source_system: string, authority: number, valid_to: string, body: RuleBody }
}

export interface ExampleStep { step: string, outcome: 'created' | 'already_there' | 'failed', detail: string }

export interface Ids { node: (key: string) => string, person: (key: string) => string }

export type CortexCall = (
  method: 'GET' | 'POST',
  path: string,
  options: { as: 'agent' | 'desk', user?: string, body?: unknown }
) => Promise<{ status: number, body: any }> // eslint-disable-line @typescript-eslint/no-explicit-any

const SCHEMA = 'custom_invoices.rule/1'
const VALID_FROM = '2026-11-01'
const AS_OF = '2026-12-04'

function ruleItem(set: ExampleSet, ids: Ids, scope: string, body: RuleBody, enforcement: string, authority: number, validTo?: string) {
  return {
    domain: set.domain,
    kind: 'rule',
    schema_version: SCHEMA,
    body,
    scope: ids.node(scope),
    entities: [],
    enforcement,
    authority,
    sensitivity: { class: 'internal' },
    purpose: set.purpose,
    valid_from: VALID_FROM,
    ...(validTo ? { valid_to: validTo } : {})
  }
}

function failed(step: string, status: number, body: unknown): ExampleStep {
  const detail = typeof body === 'object' && body !== null && 'detail' in body ? JSON.stringify((body as { detail: unknown }).detail) : String(status)
  return { step, outcome: 'failed', detail: `${status}: ${detail}`.slice(0, 300) }
}

/**
 * Load one set through the public API, as an agent and the people of its
 * tenant would: group rules imported and approved by the owner (English set
 * only), then a correction, a loosening proposal and an import to review.
 * Idempotent: an import of the same rule is a duplicate, the correction
 * replays by its idempotency key.
 */
export async function loadExampleSet(set: ExampleSet, ids: Ids, call: CortexCall): Promise<ExampleStep[]> {
  const steps: ExampleStep[] = []

  for (const rule of set.base_rules) {
    const name = `group rule ${rule.key}`
    const imported = await call('POST', '/imports', {
      as: 'agent',
      user: ids.person(rule.by),
      body: { source_system: 'example-group-rules', items: [ruleItem(set, ids, rule.scope, rule.body, rule.enforcement, rule.authority)] }
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
    // proposed now, or still waiting from an earlier load: the owner approves it either way
    const approved = await call('POST', `/review-cases/${item.review_case_id}/approve`, {
      as: 'desk',
      user: ids.person(rule.approver),
      body: { record_id: item.record_id, authority: rule.authority, enforcement: rule.enforcement }
    })
    steps.push(approved.status === 200
      ? { step: name, outcome: 'created', detail: `approved by ${rule.approver}` }
      : failed(name, approved.status, approved.body))
  }

  const scope = ids.node(set.scope)
  const pack = await call('POST', '/context-packs', {
    as: 'agent',
    body: { domain: set.domain, case: set.case, scope, purpose: set.purpose, as_of: AS_OF }
  })
  if (pack.status !== 201) {
    steps.push(failed('context pack', pack.status, pack.body))
  } else {
    const corrected = await call('POST', '/corrections', {
      as: 'agent',
      user: ids.person(set.correction.reviewer),
      body: {
        pack_id: pack.body.pack_id,
        domain: set.domain,
        case_id: set.case.case_id,
        case: set.case,
        correction: {
          correction_id: `EX-C-${set.key}-1`,
          case_id: set.case.case_id,
          reviewer: set.correction.reviewer,
          corrected_at: `${AS_OF}T15:00:00+09:00`,
          changes: set.correction.changes,
          note_ja: set.correction.note_ja
        },
        idempotency_key: `examples-${set.key}-correction-1`
      }
    })
    if (corrected.status === 202) {
      steps.push({ step: 'correction', outcome: corrected.body.replayed ? 'already_there' : 'created', detail: corrected.body.proposed_record_id ? 'a learned rule to decide' : 'recorded' })
    } else if (corrected.status === 409) {
      // Submitted by an earlier load: its idempotency key now names that one (with an older pack).
      steps.push({ step: 'correction', outcome: 'already_there', detail: 'submitted by an earlier load' })
    } else {
      steps.push(failed('correction', corrected.status, corrected.body))
    }
  }

  const proposed = await call('POST', '/records', {
    as: 'agent',
    user: ids.person(set.proposal.by),
    body: ruleItem(set, ids, set.scope, set.proposal.body, set.proposal.enforcement, set.proposal.authority)
  })
  steps.push(proposed.status === 201
    ? { step: 'local rule that loosens a group rule', outcome: proposed.body.already_pending ? 'already_there' : 'created', detail: `${(proposed.body.warnings ?? []).length} warning(s)` }
    : failed('local rule that loosens a group rule', proposed.status, proposed.body))

  const imported = await call('POST', '/imports', {
    as: 'agent',
    user: ids.person(set.import.by),
    body: {
      source_system: set.import.source_system,
      items: [ruleItem(set, ids, set.scope, set.import.body, 'default', set.import.authority, set.import.valid_to)]
    }
  })
  const item = imported.body?.items?.[0]
  steps.push(imported.status === 200 && item && item.status !== 'rejected'
    ? { step: 'import', outcome: item.status === 'proposed' ? 'created' : 'already_there', detail: `valid until ${set.import.valid_to}` }
    : failed('import', imported.status, item ?? imported.body))

  return steps
}
