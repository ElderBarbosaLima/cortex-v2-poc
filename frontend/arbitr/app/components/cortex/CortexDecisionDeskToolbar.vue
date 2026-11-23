<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'

const { t } = useI18n()
const desk = useDecisionDeskStore()
const emit = defineEmits<{ change: [] }>()
</script>

<template>
  <section
    class="flex flex-wrap items-end gap-3"
    :aria-label="t('cortex.desk.toolbar.label')"
  >
    <label
      for="cortex-desk-scope"
      class="flex min-w-64 flex-col gap-1"
    >
      <span class="text-caption font-medium text-muted">{{ t('cortex.desk.toolbar.scope') }}</span>
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
          {{ scope.name }}
        </option>
      </select>
    </label>
    <div
      class="flex rounded-lg border border-default bg-default p-0.5"
      role="group"
      :aria-label="t('cortex.desk.toolbar.status')"
    >
      <button
        v-for="s in (['open', 'decided'] as const)"
        :key="s"
        type="button"
        class="rounded-md px-3 py-1.5 text-body"
        :class="desk.status === s ? 'bg-primary text-inverted' : 'text-muted hover:text-highlighted'"
        :aria-pressed="desk.status === s"
        :data-test-id="`cortex-decision-desk-status-${s}-button`"
        @click="desk.status = s; emit('change')"
      >
        {{ t(`cortex.desk.status.${s}`) }}
      </button>
    </div>
    <select
      v-model="desk.status"
      class="sr-only"
      tabindex="-1"
      aria-hidden="true"
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
    <UButton
      color="neutral"
      variant="outline"
      icon="i-lucide-refresh-cw"
      data-test-id="cortex-decision-desk-refresh-button"
      :loading="desk.loading"
      @click="emit('change')"
    >
      {{ t('cortex.desk.toolbar.refresh') }}
    </UButton>
  </section>
</template>
