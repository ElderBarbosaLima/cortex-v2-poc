import { defineStore } from 'pinia'
import { CortexApiError, errorMessage, setExamplesSet } from '~/utils/api/client'
import {
  approveReviewCase,
  getReviewCase,
  listExpiringRecords,
  listReviewCases,
  rejectReviewCase,
  type CortexApproveRequest,
  type CortexExpiringPage,
  type CortexReviewCaseDetail,
  type CortexReviewCaseSummary
} from '~/utils/api/cortex'

export interface DevPerson { key: string, label: string, role: string, id: string, tenantId: string }
export interface DevScope { key: string, name: string, id: string, type: string, tenantId: string }

export const EXPIRING_DAYS = 90
const EXAMPLES_STORAGE = 'cortex.desk.examples'

export type ExampleKey = 'ja' | 'en'
export interface ExampleSetInfo { key: ExampleKey, label: string, note: string, tenantId: string, scopeId: string }
export interface ExampleStep { step: string, outcome: 'created' | 'already_there' | 'failed', detail: string }

function rememberedExamples(): ExampleKey {
  try {
    return localStorage.getItem(EXAMPLES_STORAGE) === 'en' ? 'en' : 'ja'
  } catch {
    return 'ja'
  }
}

export interface DeskProblem { message: string, code: string, policyRevision: string | null }

function problem(error: unknown): DeskProblem {
  if (error instanceof CortexApiError) {
    return { message: error.message, code: error.code, policyRevision: error.policyRevision }
  }
  return { message: errorMessage('unexpected_error', String(error), 0), code: 'unexpected_error', policyRevision: null }
}

/**
 * The Decision Desk: a named owner reads a review case and approves or rejects
 * it. Every call goes through the dev proxy; `personId` is sent as
 * X-End-User-Id (dev user selector), and Cortex (OPA) decides what that
 * person may do.
 */
export const useDecisionDeskStore = defineStore('decisionDesk', {
  state: () => ({
    people: [] as DevPerson[],
    scopes: [] as DevScope[],
    personId: '' as string,
    scopeId: '' as string,
    status: 'open' as 'open' | 'decided',
    cases: [] as CortexReviewCaseSummary[],
    selected: null as CortexReviewCaseDetail | null,
    loading: false,
    deciding: false,
    problem: null as DeskProblem | null,
    notice: null as string | null,
    expiring: [] as CortexExpiringPage['items'],
    examples: 'ja' as ExampleKey,
    exampleSets: [] as ExampleSetInfo[],
    loadingExamples: false,
    exampleReport: null as ExampleStep[] | null
  }),
  getters: {
    person: state => state.people.find(p => p.id === state.personId) ?? null,
    labelFor: state => (id: string) => state.people.find(p => p.id === id)?.label ?? id,
    scopeName: state => (id: string) => state.scopes.find(s => s.id === id)?.name ?? id,
    exampleSet: state => state.exampleSets.find(e => e.key === state.examples) ?? null,
    /** The example set's tenant, plus people of tenants no set uses (X-001, to show isolation). */
    visiblePeople(state): DevPerson[] {
      const tenant = this.exampleSet?.tenantId
      if (!tenant) return state.people
      const setTenants = new Set(state.exampleSets.map(e => e.tenantId))
      // The set's own people first; others (X-001) last, only to show isolation.
      return [
        ...state.people.filter(p => p.tenantId === tenant),
        ...state.people.filter(p => p.tenantId !== tenant && !setTenants.has(p.tenantId))
      ]
    },
    teamScopes(state): DevScope[] {
      const tenant = this.exampleSet?.tenantId
      return state.scopes.filter(s => s.type === 'team' && s.key.split('/').length > 2 && (!tenant || s.tenantId === tenant))
    }
  },
  actions: {
    async loadSeed() {
      const response = await fetch('/api/dev/seed', { headers: { accept: 'application/json' } })
      if (!response.ok) {
        this.problem = { message: 'The dev user list is not available', code: 'dev_seed_unavailable', policyRevision: null }
        return
      }
      const seed = await response.json() as { people: DevPerson[], scopes: DevScope[] }
      this.people = seed.people
      this.scopes = seed.scopes
      await this.loadExampleSets()
      this.pickDefaults()
    },
    async loadExampleSets() {
      try {
        const response = await fetch('/api/dev/examples', { headers: { accept: 'application/json' } })
        this.exampleSets = response.ok ? (await response.json() as { sets: ExampleSetInfo[] }).sets : []
      } catch {
        this.exampleSets = []
      }
      this.examples = rememberedExamples()
      setExamplesSet(this.examples)
    },
    /** The set's owner and first team, unless the current ones still belong to it. */
    pickDefaults() {
      const tenant = this.exampleSet?.tenantId
      const own = this.visiblePeople.filter(p => !tenant || p.tenantId === tenant)
      if (!own.some(p => p.id === this.personId)) {
        this.personId = own.find(p => p.role === 'final_validator')?.id ?? own[0]?.id ?? ''
      }
      if (!this.teamScopes.some(s => s.id === this.scopeId)) {
        this.scopeId = this.teamScopes[0]?.id ?? ''
      }
    },
    /** Switch the example set: its tenant's key, people and scopes (issue 39). */
    async useExamples(key: ExampleKey) {
      this.examples = key
      setExamplesSet(key)
      try {
        localStorage.setItem(EXAMPLES_STORAGE, key)
      } catch { /* a remembered choice is a convenience */ }
      this.resetView()
      this.exampleReport = null
      this.cases = []
      this.expiring = []
      this.pickDefaults()
      await this.loadCases()
    },
    /** Create the set's cases to review, through the public API (dev only). */
    async loadExampleCases() {
      this.loadingExamples = true
      this.problem = null
      this.exampleReport = null
      try {
        const response = await fetch('/api/dev/examples/load', {
          method: 'POST',
          headers: { 'accept': 'application/json', 'content-type': 'application/json' },
          body: JSON.stringify({ set: this.examples })
        })
        const body = await response.json() as { steps?: ExampleStep[], error?: string, detail?: string }
        if (!response.ok || !body.steps) {
          this.problem = { message: body.detail ?? 'the example cases could not be loaded', code: body.error ?? 'examples_unavailable', policyRevision: null }
          return
        }
        this.exampleReport = body.steps
        await this.loadCases()
      } catch (error) {
        this.problem = problem(error)
      } finally {
        this.loadingExamples = false
      }
    },
    async loadCases() {
      if (!this.scopeId) return
      this.loading = true
      this.problem = null
      try {
        const page = await listReviewCases({ scope: this.scopeId, status: this.status, limit: 100 }, this.personId || undefined)
        this.cases = page.items
        void this.loadExpiring()
        if (this.selected && !this.cases.some(c => c.case_id === this.selected?.case_id)) this.selected = null
      } catch (error) {
        this.cases = []
        this.problem = problem(error)
      } finally {
        this.loading = false
      }
    },
    async loadExpiring() {
      try {
        const page = await listExpiringRecords({ scope: this.scopeId, within_days: EXPIRING_DAYS }, this.personId || undefined)
        this.expiring = page.items
      } catch {
        this.expiring = [] // the case list shows the problem; this panel stays quiet
      }
    },
    /** A new person, scope or status: what was said about the last one no longer applies. */
    resetView() {
      this.selected = null
      this.notice = null
      this.problem = null
    },
    async select(caseId: string) {
      this.problem = null
      this.notice = null
      try {
        this.selected = await getReviewCase(caseId, this.personId || undefined)
      } catch (error) {
        this.selected = null
        this.problem = problem(error)
      }
    },
    async approve(body: CortexApproveRequest) {
      await this.decide(id => approveReviewCase(id, body, this.personId), 'approved')
    },
    async reject(reason: string) {
      await this.decide(id => rejectReviewCase(id, { reason }, this.personId), 'rejected')
    },
    async decide(call: (caseId: string) => Promise<unknown>, done: string) {
      const caseId = this.selected?.case_id
      if (!caseId) return
      this.deciding = true
      this.problem = null
      this.notice = null
      try {
        await call(caseId)
        await this.select(caseId)
        await this.loadCases()
        this.notice = done
      } catch (error) {
        this.problem = problem(error)
      } finally {
        this.deciding = false
      }
    }
  }
})
