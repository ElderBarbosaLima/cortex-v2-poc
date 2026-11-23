<script setup lang="ts">
import type { DevPerson, DevScope, ExampleSetInfo } from '~/stores/decisionDesk'
import { useCortexUserStore } from '~/stores/cortexUser'

const { t } = useI18n()
const route = useRoute()
const user = useCortexUserStore()

useHead({ title: () => t('cortex.login.title') })

const people = ref<DevPerson[]>([])
const scopes = ref<DevScope[]>([])
const sets = ref<ExampleSetInfo[]>([])
const setKey = ref<'ja' | 'en'>('ja')
const personId = ref('')
const failed = ref(false)

const company = computed(() => sets.value.find(s => s.key === setKey.value))
const companyName = (s: ExampleSetInfo) => scopes.value.find(n => n.id === s.tenantId)?.name ?? s.label
const candidates = computed(() => people.value.filter(p => p.tenantId === company.value?.tenantId))

onMounted(async () => {
  user.load()
  try {
    const [seed, examples] = await Promise.all([
      fetch('/api/dev/seed').then(r => r.json() as Promise<{ people: DevPerson[], scopes: DevScope[] }>),
      fetch('/api/dev/examples').then(r => (r.ok ? r.json() : { sets: [] }) as Promise<{ sets: ExampleSetInfo[] }>)
    ])
    people.value = seed.people
    scopes.value = seed.scopes
    sets.value = examples.sets
    setKey.value = user.user?.setKey ?? (sets.value[0]?.key ?? 'ja')
  } catch {
    failed.value = true
  }
})

watch(setKey, () => {
  personId.value = candidates.value.find(p => p.role === 'final_validator')?.id ?? candidates.value[0]?.id ?? ''
})
watch(candidates, (list) => {
  if (!list.some(p => p.id === personId.value)) personId.value = list.find(p => p.role === 'final_validator')?.id ?? list[0]?.id ?? ''
})

async function signIn() {
  const person = candidates.value.find(p => p.id === personId.value)
  if (!person) return
  user.signIn(setKey.value, person)
  await navigateTo(typeof route.query.next === 'string' && route.query.next.startsWith('/') ? route.query.next : '/cortex/decision-desk')
}
</script>

<template>
  <div class="mx-auto flex w-full max-w-xl flex-col gap-6 px-4 py-12">
    <div>
      <h1 class="font-display text-h2 text-highlighted">
        {{ t('cortex.login.title') }}
      </h1>
      <p class="mt-1 text-body text-muted">
        {{ t('cortex.login.intro') }}
      </p>
    </div>

    <UAlert
      v-if="failed"
      color="error"
      variant="subtle"
      :title="t('cortex.login.unavailable')"
    />

    <UCard class="rounded-xl">
      <form
        class="flex flex-col gap-5"
        @submit.prevent="signIn"
      >
        <fieldset class="flex flex-col gap-2">
          <legend class="mb-2 text-caption font-semibold uppercase tracking-wide text-muted">
            {{ t('cortex.login.company') }}
          </legend>
          <label
            v-for="s in sets"
            :key="s.key"
            :for="`cortex-login-company-${s.key}`"
            class="flex cursor-pointer items-center gap-3 rounded-lg border border-default px-3 py-2 hover:bg-elevated"
            :class="{ 'ring-2 ring-primary': setKey === s.key }"
          >
            <input
              :id="`cortex-login-company-${s.key}`"
              v-model="setKey"
              type="radio"
              name="company"
              :value="s.key"
              :data-test-id="`cortex-login-company-${s.key}-input`"
            >
            <span class="text-body text-highlighted">{{ companyName(s) }}</span>
            <span class="ml-auto text-caption text-muted">{{ s.label }}</span>
          </label>
        </fieldset>

        <fieldset class="flex flex-col gap-2">
          <legend class="mb-2 text-caption font-semibold uppercase tracking-wide text-muted">
            {{ t('cortex.login.person') }}
          </legend>
          <label
            v-for="p in candidates"
            :key="p.id"
            :for="`cortex-login-person-${p.id}`"
            class="flex cursor-pointer items-center gap-3 rounded-lg border border-default px-3 py-2 hover:bg-elevated"
            :class="{ 'ring-2 ring-primary': personId === p.id }"
          >
            <input
              :id="`cortex-login-person-${p.id}`"
              v-model="personId"
              type="radio"
              name="person"
              :value="p.id"
              data-test-id="cortex-login-person-input"
            >
            <UAvatar
              :alt="p.label"
              size="sm"
            />
            <span class="flex flex-col">
              <span class="text-body text-highlighted">{{ p.label }}</span>
              <span class="text-caption text-muted">{{ p.key }} · {{ t(`cortex.login.roles.${p.role}`) }}</span>
            </span>
          </label>
        </fieldset>

        <UButton
          type="submit"
          block
          size="lg"
          class="disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="!personId"
          data-test-id="cortex-login-submit-button"
        >
          {{ t('cortex.login.submit') }}
        </UButton>
        <p class="text-caption text-muted">
          {{ t('cortex.login.note') }}
        </p>
      </form>
    </UCard>
  </div>
</template>
