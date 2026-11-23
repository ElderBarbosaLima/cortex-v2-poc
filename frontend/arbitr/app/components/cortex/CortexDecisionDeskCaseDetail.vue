<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'
import type { CortexCaseProposal, CortexReviewCaseDetail } from '~/utils/api/cortex'
import { businessDate } from '~/utils/businessDates'

const props = defineProps<{ detail: CortexReviewCaseDetail }>()
const { t } = useI18n()
const desk = useDecisionDeskStore()

const outcomeColor = {
  pending: 'warning',
  approved: 'success',
  rejected: 'error',
  retired: 'neutral',
  superseded: 'neutral'
} as const

function text(body: Record<string, unknown> | null, key: string): string {
  const value = body?.[key]
  return typeof value === 'string' ? value : ''
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

const owners = computed(() => props.detail.named_owners)

const IMPORT_CAP = 10

function cases(source: Record<string, unknown>): string {
  return list(source.case_refs).join(', ') || String(source.case_ref ?? '—')
}

function correctors(source: Record<string, unknown>): string {
  return list(source.contributors).map(desk.labelFor).join(', ') || '—'
}

/** Who or what proposed it, in words (issue 38): never an empty "suggestion from cases —". */
function provenance(proposal: CortexCaseProposal): string[] {
  const by = proposal.asserted_by as Record<string, unknown>
  const source = proposal.source as Record<string, unknown>
  switch (by.type) {
    case 'model':
      return [t('cortex.desk.detail.provenance.model', { model: String(by.id ?? ''), cases: cases(source), correctors: correctors(source) })]
    case 'policy':
      return [t('cortex.desk.detail.provenance.policy', { cases: cases(source), correctors: correctors(source) })]
    case 'user':
      return [t('cortex.desk.detail.provenance.user', { person: desk.labelFor(String(by.id ?? '')) })]
    case 'import': {
      const lines = [t('cortex.desk.detail.provenance.import', { system: String(by.id ?? ''), person: desk.labelFor(String(by.by ?? '')) })]
      const requested = Number(source.requested_authority)
      if (Number.isFinite(requested) && requested > IMPORT_CAP) {
        lines.push(t('cortex.desk.detail.provenance.importAuthority', { requested, cap: IMPORT_CAP }))
      }
      return lines
    }
    case 'replication':
      return [t('cortex.desk.detail.provenance.replication', { region: String(by.id ?? '') })]
    default:
      return [t('cortex.desk.detail.provenance.other', { type: String(by.type ?? ''), id: String(by.id ?? '') })]
  }
}

function validity(proposal: CortexCaseProposal): string {
  return t('cortex.desk.detail.validityRange', {
    from: businessDate(proposal.valid_from) ?? t('cortex.desk.detail.noStart'),
    to: businessDate(proposal.valid_to) ?? t('cortex.desk.detail.noEnd')
  })
}
</script>

<template>
  <article
    class="flex flex-col gap-4"
    data-test-id="cortex-decision-desk-detail-panel"
  >
    <header>
      <h2 class="text-h2 font-display text-highlighted">
        {{ detail.title }}
      </h2>
      <p class="mt-1 text-caption text-muted">
        {{ t('cortex.desk.detail.meta', { scope: desk.scopeName(detail.scope), status: t(`cortex.desk.status.${detail.status}`) }) }}
      </p>
      <p
        v-if="owners"
        class="text-caption text-muted"
      >
        {{ t('cortex.desk.detail.owners', { owner: desk.labelFor(owners.owner), backup: owners.backup ? desk.labelFor(owners.backup) : '—' }) }}
      </p>
    </header>

    <section
      v-for="proposal in detail.proposals"
      :key="proposal.record_id"
      class="rounded-xl border border-default p-4"
      :aria-label="t('cortex.desk.detail.proposal')"
    >
      <div class="flex items-center justify-between gap-2">
        <p
          v-if="proposal.body_erased"
          class="text-body text-muted"
        >
          {{ t('cortex.desk.detail.erased') }}
        </p>
        <p
          v-else
          class="text-body text-highlighted"
        >
          {{ text(proposal.body, 'text_ja') }}
        </p>
        <UBadge
          :color="outcomeColor[proposal.outcome]"
          variant="subtle"
          data-test-id="cortex-decision-desk-outcome-badge"
        >
          {{ t(`cortex.desk.outcome.${proposal.outcome}`) }}
        </UBadge>
      </div>
      <p
        v-for="conflict in proposal.inherited_conflicts ?? []"
        :key="conflict.record_id"
        class="mt-2 rounded-lg bg-warning/10 px-3 py-2 text-body text-warning"
        data-test-id="cortex-decision-desk-loosens-text"
      >
        {{ conflict.text_ja
          ? t('cortex.desk.detail.loosens', { enforcement: conflict.enforcement ?? '', text: conflict.text_ja, targets: conflict.targets.join(', ') })
          : t('cortex.desk.detail.loosensErased', { enforcement: conflict.enforcement ?? '', targets: conflict.targets.join(', ') }) }}
      </p>
      <dl class="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-body">
        <dt class="text-muted">
          {{ t('cortex.desk.detail.condition') }}
        </dt>
        <dd>
          <code class="font-mono text-caption">{{ text(proposal.body, 'condition') }}</code>
        </dd>
        <dt class="text-muted">
          {{ t('cortex.desk.detail.targets') }}
        </dt>
        <dd>{{ list(proposal.body?.targets).join(', ') }}</dd>
        <dt class="text-muted">
          {{ t('cortex.desk.detail.entities') }}
        </dt>
        <dd>{{ proposal.entities.join(', ') || '—' }}</dd>
        <dt class="text-muted">
          {{ t('cortex.desk.detail.proposed') }}
        </dt>
        <dd>{{ proposal.enforcement ?? '—' }} · {{ t('cortex.desk.detail.authority', { authority: proposal.authority }) }}</dd>
        <dt class="text-muted">
          {{ t('cortex.desk.detail.validity') }}
        </dt>
        <dd data-test-id="cortex-decision-desk-validity-text">
          {{ validity(proposal) }}
        </dd>
        <dt class="text-muted">
          {{ t('cortex.desk.detail.source') }}
        </dt>
        <dd data-test-id="cortex-decision-desk-provenance-text">
          <span
            v-for="line in provenance(proposal)"
            :key="line"
            class="block"
          >{{ line }}</span>
        </dd>
      </dl>
    </section>

    <section :aria-label="t('cortex.desk.detail.affected')">
      <h3 class="text-md font-semibold text-highlighted">
        {{ t('cortex.desk.detail.affected') }}
      </h3>
      <p class="text-body">
        {{ detail.affected_cases.join(', ') || '—' }}
      </p>
    </section>

    <section
      v-if="detail.decisions.length"
      :aria-label="t('cortex.desk.detail.decisions')"
    >
      <h3 class="text-md font-semibold text-highlighted">
        {{ t('cortex.desk.detail.decisions') }}
      </h3>
      <ul class="text-body">
        <li
          v-for="decision in detail.decisions"
          :key="decision.decision_id"
        >
          {{ t(`cortex.desk.decision.${decision.decision}`) }} · {{ desk.labelFor(decision.decided_by) }}
          <span v-if="decision.reason">· {{ decision.reason }}</span>
        </li>
      </ul>
    </section>

    <CortexDecisionDeskDecisionForm
      v-if="detail.status === 'open'"
      :key="detail.case_id"
      :detail="detail"
    />
  </article>
</template>
