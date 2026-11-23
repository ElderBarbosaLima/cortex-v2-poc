<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'
import { previewApproval, type CortexApprovalPreview, type CortexReviewCaseDetail } from '~/utils/api/cortex'
import { businessDate } from '~/utils/businessDates'

const props = defineProps<{ detail: CortexReviewCaseDetail }>()
const { t } = useI18n()
const desk = useDecisionDeskStore()

const first = computed(() => props.detail.proposals.find(p => p.outcome === 'pending'))
const isRule = computed(() => first.value?.kind === 'rule')
const enforcement = ref<'mandatory' | 'prohibition' | 'default'>(first.value?.enforcement ?? 'default')
// Opens with what was proposed (issue 51); the owner raises it deliberately.
const proposedAuthority = first.value?.authority ?? 10
const authority = ref<number>(proposedAuthority)
const reason = ref('')
// Validity (issue 33 in the API, issue 38 here): prefilled with what was proposed;
// only a change is sent, so an approval never moves dates by accident.
const initialFrom = businessDate(first.value?.valid_from) ?? ''
const initialTo = businessDate(first.value?.valid_to) ?? ''
const validFrom = ref(initialFrom)
const validTo = ref(initialTo)
const datesValid = computed(() => !validFrom.value || !validTo.value || validTo.value > validFrom.value)

const approve = computed(() => props.detail.can_decide.approve)
const reject = computed(() => props.detail.can_decide.reject)
const authorityValid = computed(() => Number.isInteger(authority.value) && authority.value >= 0 && authority.value <= 40)

// What approving changes (issue 52): the origin case's pack, today and once approved.
const preview = ref<CortexApprovalPreview | null>(null)
const previewing = ref(false)
async function showPreview() {
  if (!first.value) return
  previewing.value = true
  try {
    preview.value = await previewApproval(props.detail.case_id, {
      record_id: first.value.record_id,
      enforcement: isRule.value ? enforcement.value : null,
      authority: authority.value
    }, desk.personId || undefined)
  } finally {
    previewing.value = false
  }
}
watch([enforcement, authority], () => {
  preview.value = null
})

function submitApprove() {
  desk.approve({
    authority: authority.value,
    enforcement: isRule.value ? enforcement.value : null,
    reason: reason.value || null,
    ...(validFrom.value && validFrom.value !== initialFrom ? { valid_from: validFrom.value } : {}),
    ...(validTo.value && validTo.value !== initialTo ? { valid_to: validTo.value } : {})
  })
}

function submitReject() {
  desk.reject(reason.value)
}
</script>

<template>
  <form
    class="mt-6 flex flex-col gap-4 rounded-xl border border-default p-4"
    :aria-label="t('cortex.desk.decide.label')"
    @submit.prevent
  >
    <h3 class="text-md font-semibold text-highlighted">
      {{ t('cortex.desk.decide.title') }}
    </h3>
    <div class="flex flex-wrap gap-4">
      <label
        v-if="isRule"
        for="cortex-desk-enforcement"
        class="flex flex-col gap-1"
      >
        <span class="text-caption text-muted">{{ t('cortex.desk.decide.enforcement') }}</span>
        <select
          id="cortex-desk-enforcement"
          v-model="enforcement"
          class="rounded-lg border border-default bg-default px-3 py-2 text-body"
          data-test-id="cortex-decision-desk-enforcement-select"
        >
          <option value="mandatory">
            {{ t('cortex.desk.enforcement.mandatory') }}
          </option>
          <option value="prohibition">
            {{ t('cortex.desk.enforcement.prohibition') }}
          </option>
          <option value="default">
            {{ t('cortex.desk.enforcement.default') }}
          </option>
        </select>
      </label>
      <label
        for="cortex-desk-authority"
        class="flex flex-col gap-1"
      >
        <span class="text-caption text-muted">{{ t('cortex.desk.decide.authority') }}</span>
        <input
          id="cortex-desk-authority"
          v-model.number="authority"
          type="number"
          min="0"
          max="40"
          class="w-28 rounded-lg border border-default bg-default px-3 py-2 text-body"
          data-test-id="cortex-decision-desk-authority-input"
        >
      </label>
      <p class="basis-full text-caption text-muted">
        {{ t('cortex.desk.decide.authorityHint', { proposed: proposedAuthority }) }}
      </p>
    </div>
    <div class="flex flex-wrap gap-4">
      <label
        for="cortex-desk-valid-from"
        class="flex flex-col gap-1"
      >
        <span class="text-caption text-muted">{{ t('cortex.desk.decide.validFrom') }}</span>
        <input
          id="cortex-desk-valid-from"
          v-model="validFrom"
          type="date"
          class="rounded-lg border border-default bg-default px-3 py-2 text-body"
          data-test-id="cortex-decision-desk-valid-from-input"
        >
      </label>
      <label
        for="cortex-desk-valid-to"
        class="flex flex-col gap-1"
      >
        <span class="text-caption text-muted">{{ t('cortex.desk.decide.validTo') }}</span>
        <input
          id="cortex-desk-valid-to"
          v-model="validTo"
          type="date"
          :min="validFrom || undefined"
          class="rounded-lg border border-default bg-default px-3 py-2 text-body"
          data-test-id="cortex-decision-desk-valid-to-input"
        >
      </label>
    </div>
    <p class="text-caption text-muted">
      {{ t('cortex.desk.decide.validityHint') }}
    </p>
    <label
      for="cortex-desk-reason"
      class="flex flex-col gap-1"
    >
      <span class="text-caption text-muted">{{ t('cortex.desk.decide.reason') }}</span>
      <textarea
        id="cortex-desk-reason"
        v-model="reason"
        rows="2"
        class="rounded-lg border border-default bg-default px-3 py-2 text-body"
        data-test-id="cortex-decision-desk-reason-textarea"
      />
    </label>
    <div
      v-if="isRule && first"
      class="rounded-lg border border-default p-3"
    >
      <p class="text-caption text-muted">
        {{ t('cortex.desk.preview.appliesTo') }}
        <code class="font-mono text-caption text-highlighted">{{ String(first.body?.condition ?? '') }}</code>
        <span v-if="first.entities.length"> · {{ first.entities.join(', ') }}</span>
      </p>
      <UButton
        size="sm"
        color="neutral"
        variant="outline"
        icon="i-lucide-eye"
        class="mt-2"
        :loading="previewing"
        data-test-id="cortex-decision-desk-preview-button"
        @click="showPreview"
      >
        {{ t('cortex.desk.preview.button') }}
      </UButton>
      <div
        v-if="preview"
        class="mt-3"
        data-test-id="cortex-decision-desk-preview-panel"
      >
        <p
          v-if="!preview.available"
          class="text-body text-muted"
        >
          {{ t('cortex.desk.preview.unavailable') }}
        </p>
        <template v-else>
          <p class="text-body font-semibold text-highlighted">
            {{ t('cortex.desk.preview.title', { case: preview.case_ref ?? '' }) }}
          </p>
          <p
            v-if="!preview.applies"
            class="text-body text-warning"
          >
            {{ t('cortex.desk.preview.notApplicable', { case: preview.case_ref ?? '' }) }}
          </p>
          <div class="mt-2 grid gap-3 md:grid-cols-2">
            <div
              v-for="column in (['before', 'after'] as const)"
              :key="column"
            >
              <p class="text-caption font-semibold uppercase tracking-wide text-muted">
                {{ t(`cortex.desk.preview.${column}`) }}
              </p>
              <ul class="mt-1 flex flex-col gap-1">
                <li
                  v-for="r in preview[column]"
                  :key="r.record_id"
                  class="flex items-start gap-2 rounded-md px-2 py-1 text-caption"
                  :class="r.proposed ? 'bg-primary/10 ring-1 ring-primary' : 'bg-muted/60'"
                >
                  <UBadge
                    size="sm"
                    variant="subtle"
                    :color="r.role === 'overridden' ? 'neutral' : (r.role === 'constraint' ? 'warning' : 'success')"
                  >
                    {{ t(`cortex.desk.preview.role.${r.role}`) }}
                  </UBadge>
                  <span :class="r.role === 'overridden' ? 'text-muted line-through' : 'text-highlighted'">
                    {{ r.text ?? r.record_id }}
                    <strong v-if="r.proposed"> · {{ t('cortex.desk.preview.proposed') }}</strong>
                  </span>
                </li>
              </ul>
            </div>
          </div>
        </template>
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-3">
      <UButton
        data-test-id="cortex-decision-desk-approve-button"
        class="disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="!approve.allowed || !authorityValid || !datesValid || desk.deciding"
        :aria-describedby="approve.allowed ? undefined : 'cortex-desk-approve-denied'"
        @click="submitApprove"
      >
        {{ t('cortex.desk.decide.approve') }}
      </UButton>
      <UButton
        color="error"
        variant="outline"
        data-test-id="cortex-decision-desk-reject-button"
        class="disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="!reject.allowed || !reason.trim() || desk.deciding"
        :aria-describedby="reject.allowed ? undefined : 'cortex-desk-reject-denied'"
        @click="submitReject"
      >
        {{ t('cortex.desk.decide.reject') }}
      </UButton>
    </div>
    <p
      v-if="!approve.allowed"
      id="cortex-desk-approve-denied"
      class="text-body text-error"
      data-test-id="cortex-decision-desk-approve-denied-text"
    >
      {{ t('cortex.desk.decide.approveDenied', { reason: approve.reason }) }}
    </p>
    <p
      v-if="!reject.allowed && reject.reason !== approve.reason"
      id="cortex-desk-reject-denied"
      class="text-body text-error"
      data-test-id="cortex-decision-desk-reject-denied-text"
    >
      {{ t('cortex.desk.decide.rejectDenied', { reason: reject.reason }) }}
    </p>
    <p
      v-else-if="!reject.allowed"
      id="cortex-desk-reject-denied"
      class="sr-only"
    >
      {{ t('cortex.desk.decide.rejectDenied', { reason: reject.reason }) }}
    </p>
    <p
      v-if="reject.allowed && !reason.trim()"
      class="text-caption text-muted"
    >
      {{ t('cortex.desk.decide.reasonNeeded') }}
    </p>
  </form>
</template>
