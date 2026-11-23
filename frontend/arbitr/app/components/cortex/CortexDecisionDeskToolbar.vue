<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'

const { t } = useI18n()
const desk = useDecisionDeskStore()
const emit = defineEmits<{ change: [] }>()
</script>

<template>
  <section
    class="flex flex-wrap items-end gap-4"
    :aria-label="t('cortex.desk.toolbar.label')"
  >
    <label
      v-if="desk.exampleSets.length"
      for="cortex-desk-examples"
      class="flex flex-col gap-1"
    >
      <span class="text-caption text-muted">{{ t('cortex.desk.examples.label') }}</span>
      <select
        id="cortex-desk-examples"
        :value="desk.examples"
        class="rounded-lg border border-default bg-default px-3 py-2 text-body"
        data-test-id="cortex-decision-desk-examples-select"
        @change="desk.useExamples(($event.target as HTMLSelectElement).value === 'en' ? 'en' : 'ja')"
      >
        <option
          v-for="set in desk.exampleSets"
          :key="set.key"
          :value="set.key"
        >
          {{ set.label }}
        </option>
      </select>
    </label>
    <label
      for="cortex-desk-person"
      class="flex flex-col gap-1"
    >
      <span class="text-caption text-muted">{{ t('cortex.desk.toolbar.person') }}</span>
      <select
        id="cortex-desk-person"
        v-model="desk.personId"
        class="rounded-lg border border-default bg-default px-3 py-2 text-body"
        data-test-id="cortex-decision-desk-person-select"
        @change="emit('change')"
      >
        <option
          v-for="person in desk.visiblePeople"
          :key="person.id"
          :value="person.id"
        >
          {{ person.key }} · {{ person.label }}
        </option>
      </select>
    </label>
    <label
      for="cortex-desk-scope"
      class="flex flex-col gap-1"
    >
      <span class="text-caption text-muted">{{ t('cortex.desk.toolbar.scope') }}</span>
      <select
        id="cortex-desk-scope"
        v-model="desk.scopeId"
        class="rounded-lg border border-default bg-default px-3 py-2 text-body"
        data-test-id="cortex-decision-desk-scope-select"
        @change="emit('change')"
      >
        <option
          v-for="scope in desk.teamScopes"
          :key="scope.id"
          :value="scope.id"
        >
          {{ scope.name }} ({{ scope.key }})
        </option>
      </select>
    </label>
    <label
      for="cortex-desk-status"
      class="flex flex-col gap-1"
    >
      <span class="text-caption text-muted">{{ t('cortex.desk.toolbar.status') }}</span>
      <select
        id="cortex-desk-status"
        v-model="desk.status"
        class="rounded-lg border border-default bg-default px-3 py-2 text-body"
        data-test-id="cortex-decision-desk-status-select"
        @change="emit('change')"
      >
        <option value="open">
          {{ t('cortex.desk.status.open') }}
        </option>
        <option value="decided">
          {{ t('cortex.desk.status.decided') }}
        </option>
      </select>
    </label>
    <UButton
      color="neutral"
      variant="outline"
      data-test-id="cortex-decision-desk-refresh-button"
      :loading="desk.loading"
      @click="emit('change')"
    >
      {{ t('cortex.desk.toolbar.refresh') }}
    </UButton>
    <UButton
      v-if="desk.exampleSets.length"
      color="neutral"
      variant="subtle"
      data-test-id="cortex-decision-desk-load-examples-button"
      :loading="desk.loadingExamples"
      @click="desk.loadExampleCases()"
    >
      {{ t('cortex.desk.examples.load') }}
    </UButton>
    <p class="w-full text-caption text-muted">
      {{ t('cortex.desk.toolbar.devNote') }}
    </p>
    <p
      v-if="desk.exampleSet"
      class="w-full text-caption text-muted"
      data-test-id="cortex-decision-desk-examples-note-text"
    >
      {{ desk.exampleSet.note }}
    </p>
    <ul
      v-if="desk.exampleReport"
      class="w-full rounded-lg border border-default p-3 text-body"
      :aria-label="t('cortex.desk.examples.report')"
      data-test-id="cortex-decision-desk-examples-report-text"
    >
      <li
        v-for="step in desk.exampleReport"
        :key="step.step"
        :class="step.outcome === 'failed' ? 'text-error' : ''"
      >
        {{ t(`cortex.desk.examples.outcome.${step.outcome}`) }} · {{ step.step }} · {{ step.detail }}
      </li>
    </ul>
  </section>
</template>
