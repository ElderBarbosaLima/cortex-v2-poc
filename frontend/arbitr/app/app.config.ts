export default defineAppConfig({
  ui: {
    // Nuxt UI reads each role from the v2 scales in main.css. The text, surface
    // and border roles are set on `night` steps in main.css's `:root` block, so
    // controls need no colour overrides here, only the shape overrides below.
    colors: {
      primary: 'brand',
      neutral: 'night',
      success: 'mint',
      warning: 'honey',
      error: 'ember',
      info: 'azure'
    },
    // The named type steps in main.css (`text-2xs`, `text-caption`, …) are
    // font sizes. Without this, tailwind-merge reads `text-caption` as a text
    // colour and drops it when a colour class follows in the same list.
    tv: {
      twMergeConfig: {
        extend: {
          theme: {
            text: ['2xs', 'caption', 'body', 'md', 'h2']
          }
        }
      }
    },
    // Buttons, inputs and selects are 8px.
    button: {
      slots: {
        base: 'rounded-lg'
      }
    },
    input: {
      slots: {
        base: 'rounded-lg'
      }
    },
    textarea: {
      slots: {
        base: 'rounded-lg'
      }
    },
    select: {
      slots: {
        base: 'rounded-lg'
      }
    },
    selectMenu: {
      slots: {
        base: 'rounded-lg',
        // Floating panels sit above the sticky header and nav (z-50 / z-40).
        content: 'z-50'
      }
    },
    popover: {
      slots: {
        content: 'z-50'
      }
    },
    // Toasts: a solid tint per type with a matching edge; the icon keeps
    // Nuxt UI's semantic colour, which is each scale's 500 step.
    toast: {
      slots: {
        title: 'text-body font-semibold text-highlighted',
        description: 'text-body text-highlighted'
      },
      variants: {
        color: {
          success: { root: 'bg-mint-50 ring-mint-200' },
          error: { root: 'bg-ember-50 ring-ember-200' },
          warning: { root: 'bg-honey-50 ring-honey-200' },
          info: { root: 'bg-azure-50 ring-azure-200' }
        }
      }
    },
    // Modals and slide-overs: no shadow, a hairline border. Modals are 12px.
    modal: {
      variants: {
        fullscreen: {
          false: {
            content: 'rounded-xl shadow-none ring-default'
          }
        }
      }
    },
    slideover: {
      slots: {
        content: 'sm:shadow-none'
      }
    }
  }
})
