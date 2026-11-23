import { useCortexUserStore } from '~/stores/cortexUser'

/** The Decision Desk needs someone signed in (issue 54). */
export default defineNuxtRouteMiddleware((to) => {
  const user = useCortexUserStore()
  user.load()
  if (!user.user) return navigateTo({ path: '/login', query: { next: to.fullPath } })
})
