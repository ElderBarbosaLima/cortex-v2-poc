<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'
import type { CortexReviewCaseDetail } from '~/utils/api/cortex'
import { businessDate } from '~/utils/businessDates'

const props = defineProps<{ detail: CortexReviewCaseDetail }>()
const { t } = useI18n()
const desk = useDecisionDeskStore()

const first = computed(() => props.detail.proposals.find(p => p.outcome === 'pending'))
const isRule = computed(() => first.value?.kind === 'rule')
const enforcement = ref<'mandatory' | 'prohibition' | 'default'>(first.value?.enforcement ?? 'default')
const authority = ref<number>(Math.max(first.value?.authority ?? 20, 20))
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
            mandatory
          </option>
          <option value="prohibition">
            prohibition
          </option>
          <option value="default">
            default
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
