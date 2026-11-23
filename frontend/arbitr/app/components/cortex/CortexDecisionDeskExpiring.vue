<script setup lang="ts">
import { EXPIRING_DAYS, useDecisionDeskStore } from '~/stores/decisionDesk'
import { businessDate } from '~/utils/businessDates'

const { t } = useI18n()
const desk = useDecisionDeskStore()
</script>

<template>
  <section
    class="rounded-xl border border-default p-3"
    :aria-label="t('cortex.desk.expiring.title')"
    data-test-id="cortex-decision-desk-expiring-panel"
  >
    <h2 class="text-md font-semibold text-highlighted">
      {{ t('cortex.desk.expiring.title') }}
    </h2>
    <p class="text-caption text-muted">
      {{ t('cortex.desk.expiring.intro', { days: EXPIRING_DAYS }) }}
    </p>
    <p
      v-if="!desk.expiring.length"
      class="mt-2 text-body text-muted"
      data-test-id="cortex-decision-desk-expiring-empty-text"
    >
      {{ t('cortex.desk.expiring.empty', { days: EXPIRING_DAYS }) }}
    </p>
    <ul
      v-else
      class="mt-2 flex flex-col gap-2"
    >
      <li
        v-for="item in desk.expiring"
        :key="item.record_id"
        class="text-body"
        data-test-id="cortex-decision-desk-expiring-item-text"
      >
        <span class="text-highlighted">{{ item.title ?? t('cortex.desk.detail.erased') }}</span>
        <span
          class="block text-caption"
          :class="item.expired ? 'text-error' : 'text-warning'"
        >
          {{ t(item.expired ? 'cortex.desk.expiring.expired' : 'cortex.desk.expiring.until', { date: businessDate(item.valid_to) }) }}
          · {{ desk.scopeName(item.scope) }}
        </span>
      </li>
    </ul>
  </section>
</template>
