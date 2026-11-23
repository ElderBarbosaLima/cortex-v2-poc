<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'

const { t } = useI18n()
const desk = useDecisionDeskStore()
const emit = defineEmits<{ change: [] }>()
</script>

<template>
  <section
    class="rounded-xl border border-dashed border-warning/60 bg-warning/5 p-4"
    :aria-label="t('cortex.desk.dev.title')"
    data-test-id="cortex-decision-desk-devtools-panel"
  >
    <h2 class="flex items-center gap-2 text-caption font-semibold uppercase tracking-wide text-warning">
      <UIcon name="i-lucide-wrench" />
      {{ t('cortex.desk.dev.title') }}
    </h2>
    <div class="mt-3 flex flex-wrap items-end gap-3">
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
      <UButton
        v-if="desk.exampleSets.length"
        color="neutral"
        variant="subtle"
        icon="i-lucide-database"
        data-test-id="cortex-decision-desk-load-examples-button"
        :loading="desk.loadingExamples"
        @click="desk.loadExampleCases()"
      >
        {{ t('cortex.desk.examples.load') }}
      </UButton>
    </div>
    <p class="mt-3 text-caption text-muted">
      {{ t('cortex.desk.toolbar.devNote') }}
    </p>
    <p
      v-if="desk.exampleSet"
      class="text-caption text-muted"
      data-test-id="cortex-decision-desk-examples-note-text"
    >
      {{ desk.exampleSet.note }}
    </p>
    <ul
      v-if="desk.exampleReport"
      class="mt-3 rounded-lg border border-default bg-default p-3 text-body"
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
