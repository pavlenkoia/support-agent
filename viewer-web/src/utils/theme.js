export const THEME_STORAGE_KEY = 'vk-dialog-viewer-theme'

export function loadInitialTheme() {
  if (typeof window === 'undefined') return 'light'
  return window.localStorage.getItem(THEME_STORAGE_KEY) || 'light'
}
