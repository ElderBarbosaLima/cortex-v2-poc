<script setup lang="ts">
import { EXPIRING_DAYS, useDecisionDeskStore } from '~/stores/decisionDesk'
import { businessDate } from '~/utils/businessDates'

const { t } = useI18n()
const desk = useDecisionDeskStore()
const picked = ref<string[]>([])
const validTo = ref(`${new Date().getFullYear() + 1}-03-31`)

const kinds = (item: (typeof desk.alerts)[number]) => item.alerts.map(a => a.kind)

function toggle(id: string) {
  picked.value = picked.value.includes(id) ? picked.value.filter(x => x !== id) : [...picked.value, id]
}

async function renew() {
  await desk.renew(picked.value, validTo.value)
  picked.value = []
}
</script>

<template>
  <section
    class="rounded-xl border border-default p-3"
    :aria-label="t('cortex.desk.alerts.title')"
    data-test-id="cortex-decision-desk-expiring-panel"
  >
    <h2 class="text-md font-semibold text-highlighted">
      {{ t('cortex.desk.alerts.title') }}
    </h2>
    <p class="text-caption text-muted">
      {{ t('cortex.desk.alerts.intro', { days: EXPIRING_DAYS }) }}
    </p>
    <p
      v-if="!desk.alerts.length"
      class="mt-2 text-body text-muted"
      data-test-id="cortex-decision-desk-expiring-empty-text"
    >
      {{ t('cortex.desk.alerts.empty') }}
    </p>
    <ul
      v-else
      class="mt-2 flex flex-col gap-2"
    >
      <li
        v-for="item in desk.alerts"
        :key="item.record_id"
        class="flex items-start gap-2 text-body"
        data-test-id="cortex-decision-desk-expiring-item-text"
      >
        <input
          v-if="kinds(item).includes('expiring')"
          type="checkbox"
          class="mt-1"
          :checked="picked.includes(item.record_id)"
          :aria-label="t('cortex.desk.alerts.pick', { title: item.title ?? item.record_id })"
          data-test-id="cortex-decision-desk-renew-checkbox"
          @change="toggle(item.record_id)"
        >
        <span>
          <span class="text-highlighted">{{ item.title ?? t('cortex.desk.detail.erased') }}</span>
          <span
            v-for="alert in item.alerts"
            :key="alert.kind"
            class="block text-caption text-warning"
          >
            {{ alert.kind === 'expiring'
              ? t('cortex.desk.expiring.until', { date: businessDate(item.valid_to) })
              : t(`cortex.desk.alerts.kind.${alert.kind}`, { count: alert.count ?? 0 }) }}
          </span>
        </span>
      </li>
    </ul>
    <div
      v-if="desk.alerts.some(i => kinds(i).includes('expiring'))"
      class="mt-3 flex flex-wrap items-end gap-2"
    >
      <label
        for="cortex-desk-renew-to"
        class="flex flex-col gap-1"
      >
        <span class="text-caption text-muted">{{ t('cortex.desk.alerts.renewTo') }}</span>
        <input
          id="cortex-desk-renew-to"
          v-model="validTo"
          type="date"
          class="rounded-lg border border-default bg-default px-2 py-1 text-body"
          data-test-id="cortex-decision-desk-renew-to-input"
        >
      </label>
      <UButton
        size="sm"
        class="disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="!picked.length || desk.renewing"
        :loading="desk.renewing"
        data-test-id="cortex-decision-desk-renew-button"
        @click="renew"
      >
        {{ t('cortex.desk.alerts.renew', { count: picked.length }) }}
      </UButton>
    </div>
    <p
      v-if="desk.renewResult"
      class="mt-2 text-caption text-success"
      role="status"
      data-test-id="cortex-decision-desk-renew-result-text"
    >
      {{ t('cortex.desk.alerts.renewed', desk.renewResult) }}
    </p>
  </section>
</template>
