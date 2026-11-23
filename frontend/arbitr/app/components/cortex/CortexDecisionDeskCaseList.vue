<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'

const { t } = useI18n()
const desk = useDecisionDeskStore()
</script>

<template>
  <nav :aria-label="t('cortex.desk.list.label')">
    <p
      v-if="!desk.loading && !desk.cases.length"
      class="text-body text-muted"
      data-test-id="cortex-decision-desk-case-list-empty-text"
    >
      {{ t('cortex.desk.list.empty') }}
    </p>
    <div
      v-if="desk.status === 'open' && desk.cases.length > 1"
      class="mb-2 flex flex-wrap items-center gap-2"
    >
      <UButton
        size="sm"
        class="disabled:cursor-not-allowed disabled:opacity-40"
        data-test-id="cortex-decision-desk-approve-selected-button"
        :disabled="!desk.picked.length || desk.deciding"
        :loading="desk.deciding"
        @click="desk.approvePicked()"
      >
        {{ t('cortex.desk.bulk.approve', { count: desk.picked.length }) }}
      </UButton>
      <span class="text-caption text-muted">{{ t('cortex.desk.bulk.hint') }}</span>
    </div>
    <ul class="flex flex-col gap-2">
      <li
        v-for="item in desk.cases"
        :key="item.case_id"
        class="flex items-start gap-2"
      >
        <input
          v-if="desk.status === 'open' && desk.cases.length > 1"
          type="checkbox"
          class="mt-3"
          :checked="desk.picked.includes(item.case_id)"
          :aria-label="t('cortex.desk.bulk.pick', { title: item.title })"
          data-test-id="cortex-decision-desk-pick-checkbox"
          @change="desk.togglePick(item.case_id)"
        >
        <button
          type="button"
          class="w-full rounded-lg border border-default px-3 py-2 text-left hover:bg-elevated"
          :class="{ 'ring-2 ring-primary': desk.selected?.case_id === item.case_id }"
          :aria-current="desk.selected?.case_id === item.case_id ? 'true' : undefined"
          :data-case-id="item.case_id"
          data-test-id="cortex-decision-desk-case-item-button"
          @click="desk.select(item.case_id)"
        >
          <span class="block text-body text-highlighted">{{ item.title }}</span>
          <span class="block text-caption text-muted">
            {{ t('cortex.desk.list.meta', { count: item.proposal_count, scope: desk.scopeName(item.scope) }) }}
          </span>
        </button>
      </li>
    </ul>
  </nav>
</template>
