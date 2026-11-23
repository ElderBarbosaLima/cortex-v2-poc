import { defineStore } from 'pinia'
import { CortexApiError, errorMessage } from '~/utils/api/client'
import {
  approveReviewCase,
  getReviewCase,
  listReviewCases,
  rejectReviewCase,
  type CortexApproveRequest,
  type CortexReviewCaseDetail,
  type CortexReviewCaseSummary
} from '~/utils/api/cortex'

export interface DevPerson { key: string, label: string, role: string, id: string, tenantId: string }
export interface DevScope { key: string, name: string, id: string, type: string, tenantId: string }

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
    notice: null as string | null
  }),
  getters: {
    person: state => state.people.find(p => p.id === state.personId) ?? null,
    labelFor: state => (id: string) => state.people.find(p => p.id === id)?.label ?? id,
    scopeName: state => (id: string) => state.scopes.find(s => s.id === id)?.name ?? id,
    teamScopes: state => state.scopes.filter(s => s.type === 'team' && s.key.split('/').length > 2)
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
      this.personId ||= seed.people.find(p => p.role === 'final_validator')?.id ?? seed.people[0]?.id ?? ''
      this.scopeId ||= this.teamScopes[0]?.id ?? ''
    },
    async loadCases() {
      if (!this.scopeId) return
      this.loading = true
      this.problem = null
      try {
        const page = await listReviewCases({ scope: this.scopeId, status: this.status, limit: 100 }, this.personId || undefined)
        this.cases = page.items
        if (this.selected && !this.cases.some(c => c.case_id === this.selected?.case_id)) this.selected = null
      } catch (error) {
        this.cases = []
        this.problem = problem(error)
      } finally {
        this.loading = false
      }
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
