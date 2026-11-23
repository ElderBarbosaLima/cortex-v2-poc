<script setup lang="ts">
import { useCortexUserStore } from '~/stores/cortexUser'

const { t, locale, setLocale } = useI18n()
const user = useCortexUserStore()
const route = useRoute()

const LOCALE_KEY = 'cortex.locale'
async function chooseLocale(code: 'en' | 'ja') {
  await setLocale(code)
  try {
    localStorage.setItem(LOCALE_KEY, code)
  } catch { /* a convenience */ }
}

async function signOut() {
  user.signOut()
  await navigateTo('/login')
}

const links = computed(() => [
  { label: t('cortex.shell.nav.home'), to: '/cortex' },
  { label: t('cortex.shell.nav.desk'), to: '/cortex/decision-desk' }
])
</script>

<template>
  <header class="sticky top-0 z-40 border-b border-default bg-default/90 backdrop-blur">
    <div class="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
      <NuxtLink
        to="/cortex"
        class="flex items-center gap-2"
        data-test-id="cortex-shell-home-link"
      >
        <span class="grid size-7 place-items-center rounded-lg bg-primary font-display text-sm font-bold text-inverted">C</span>
        <span class="font-display text-md font-semibold text-highlighted">{{ t('cortex.shell.brand') }}</span>
        <span class="hidden text-caption text-muted sm:inline">· arbitr</span>
      </NuxtLink>
      <nav
        class="hidden gap-1 sm:flex"
        :aria-label="t('cortex.shell.nav.label')"
      >
        <NuxtLink
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          class="rounded-lg px-3 py-1.5 text-body text-muted hover:bg-elevated hover:text-highlighted"
          :class="{ 'bg-elevated text-highlighted': route.path === link.to }"
          :data-test-id="`cortex-shell-nav-${link.to.split('/').pop()}-link`"
        >
          {{ link.label }}
        </NuxtLink>
      </nav>
      <div class="ml-auto flex items-center gap-3">
        <select
          :value="locale"
          class="rounded-lg border border-default bg-default px-2 py-1 text-caption"
          :aria-label="t('cortex.shell.language')"
          data-test-id="cortex-shell-language-select"
          @change="chooseLocale((($event.target as HTMLSelectElement).value === 'ja' ? 'ja' : 'en'))"
        >
          <option value="en">
            English
          </option>
          <option value="ja">
            日本語
          </option>
        </select>
        <template v-if="user.user">
          <span class="hidden flex-col items-end leading-tight md:flex">
            <span class="text-body text-highlighted">{{ user.user.label }}</span>
            <span class="text-2xs text-muted">{{ user.user.personKey }} · {{ t(`cortex.login.roles.${user.user.role}`) }}</span>
          </span>
          <UButton
            size="sm"
            color="neutral"
            :variant="user.devMode ? 'soft' : 'ghost'"
            icon="i-lucide-wrench"
            :aria-pressed="user.devMode"
            :title="t('cortex.shell.devMode')"
            data-test-id="cortex-shell-devmode-button"
            @click="user.toggleDevMode()"
          />
          <UButton
            size="sm"
            color="neutral"
            variant="outline"
            data-test-id="cortex-shell-signout-button"
            @click="signOut"
          >
            {{ t('cortex.shell.signOut') }}
          </UButton>
        </template>
        <UButton
          v-else
          size="sm"
          to="/login"
          data-test-id="cortex-shell-signin-link"
        >
          {{ t('cortex.login.submit') }}
        </UButton>
      </div>
    </div>
  </header>
</template>
