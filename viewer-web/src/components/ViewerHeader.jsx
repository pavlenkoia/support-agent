export function ViewerHeader({ theme, onThemeToggle }) {
  const isDark = theme === 'dark'
  const nextThemeLabel = isDark ? 'Переключить на светлую тему' : 'Переключить на тёмную тему'

  return (
    <header
      className={[
        'fixed inset-x-0 top-0 z-20 border-b backdrop-blur',
        isDark ? 'border-slate-800 bg-slate-950/95' : 'border-slate-200 bg-white/95',
      ].join(' ')}
    >
      <div className="mx-auto flex h-20 max-w-[1800px] items-center justify-between gap-4 px-6">
        <div>
          <p className={["text-xs font-semibold uppercase tracking-[0.18em]", isDark ? 'text-sky-400' : 'text-sky-600'].join(' ')}>
            support-agent
          </p>
          <h1 className={["text-2xl font-semibold", isDark ? 'text-white' : 'text-slate-900'].join(' ')}>
            VK dialog viewer
          </h1>
        </div>

        <button
          aria-label={nextThemeLabel}
          className={[
            'inline-flex h-11 w-11 items-center justify-center rounded-xl border text-lg transition',
            isDark
              ? 'border-slate-700 bg-slate-900 text-slate-100 hover:border-slate-600 hover:bg-slate-800'
              : 'border-slate-300 bg-slate-50 text-slate-700 hover:border-slate-400 hover:bg-slate-100',
          ].join(' ')}
          onClick={onThemeToggle}
          title={nextThemeLabel}
          type="button"
        >
          <span aria-hidden="true">{isDark ? '☀️' : '🌙'}</span>
        </button>
      </div>
    </header>
  )
}
