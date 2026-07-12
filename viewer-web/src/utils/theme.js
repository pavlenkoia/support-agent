export const THEME_STORAGE_KEY = 'vk-dialog-viewer-theme'
export const THEME_COLORS = {
  dark: {
    background: '#020617',
    text: '#e2e8f0',
    themeColor: '#020617',
  },
  light: {
    background: '#f8fafc',
    text: '#0f172a',
    themeColor: '#f8fafc',
  },
}

export function loadInitialTheme() {
  if (typeof window === 'undefined') return 'light'
  return window.localStorage.getItem(THEME_STORAGE_KEY) || 'light'
}

export function applyThemeToDocument(theme) {
  if (typeof document === 'undefined') return

  const palette = THEME_COLORS[theme] || THEME_COLORS.light
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
  document.documentElement.style.backgroundColor = palette.background
  document.documentElement.style.color = palette.text

  if (document.body) {
    document.body.style.backgroundColor = palette.background
    document.body.style.color = palette.text
  }

  const themeMeta = document.querySelector('meta[name="theme-color"]')
  if (themeMeta) {
    themeMeta.setAttribute('content', palette.themeColor)
  }
}
