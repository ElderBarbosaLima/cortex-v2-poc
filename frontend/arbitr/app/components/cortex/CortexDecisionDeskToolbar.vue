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
          v-for="person in desk.people"
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
    <p class="w-full text-caption text-muted">
      {{ t('cortex.desk.toolbar.devNote') }}
    </p>
  </section>
</template>
