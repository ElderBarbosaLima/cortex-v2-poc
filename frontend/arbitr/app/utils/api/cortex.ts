// Typed calls to /v1/cortex. Types come only from the generated contract
// (libraries/cortex-contract/ts/cortex.d.ts), so a backend change that is not
// regenerated fails `make typecheck`.
import type { components } from '#cortex-contract/cortex'
import { cortexRequest } from './client'

type Schemas = components['schemas']
export type CortexWhoAmI = Schemas['WhoAmI']
export type CortexReviewCasePage = Schemas['ReviewCasePage']
export type CortexReviewCaseSummary = Schemas['ReviewCaseSummary']
export type CortexReviewCaseDetail = Schemas['ReviewCaseDetail']
export type CortexApproveRequest = Schemas['ApproveRequest']
export type CortexRejectRequest = Schemas['RejectRequest']
export type CortexDecisionResult = Schemas['DecisionResult']
export type CortexExpiringPage = Schemas['ExpiringPage']
export type CortexCaseProposal = Schemas['CaseProposal']

export const REVIEW_PURPOSE = 'rule_review'

export function getWhoAmI(signal?: AbortSignal): Promise<CortexWhoAmI> {
  return cortexRequest('GET', '/whoami', { signal })
}

export function listReviewCases(
  params: { scope: string, status?: 'open' | 'decided', cursor?: string, limit?: number },
  endUser?: string
): Promise<CortexReviewCasePage> {
  return cortexRequest('GET', '/review-cases', { query: { purpose: REVIEW_PURPOSE, ...params }, endUser })
}

export function getReviewCase(caseId: string, endUser?: string): Promise<CortexReviewCaseDetail> {
  return cortexRequest('GET', `/review-cases/${encodeURIComponent(caseId)}`, {
    query: { purpose: REVIEW_PURPOSE },
    endUser
  })
}

export function approveReviewCase(caseId: string, body: CortexApproveRequest, endUser: string): Promise<CortexDecisionResult> {
  return cortexRequest('POST', `/review-cases/${encodeURIComponent(caseId)}/approve`, { body, endUser })
}

export function rejectReviewCase(caseId: string, body: CortexRejectRequest, endUser: string): Promise<CortexDecisionResult> {
  return cortexRequest('POST', `/review-cases/${encodeURIComponent(caseId)}/reject`, { body, endUser })
}

export function listExpiringRecords(
  params: { scope: string, within_days?: number },
  endUser?: string
): Promise<CortexExpiringPage> {
  return cortexRequest('GET', '/records/expiring', { query: { purpose: REVIEW_PURPOSE, ...params }, endUser })
}
