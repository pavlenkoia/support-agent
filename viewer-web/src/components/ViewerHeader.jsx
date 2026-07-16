export function ViewerHeader({ theme, onThemeToggle, onPushToggle, pushStatus }) {
  const isDark = theme === 'dark'
  const nextThemeLabel = isDark ? 'Переключить на светлую тему' : 'Переключить на тёмную тему'
  const pushEnabled = pushStatus === 'enabled'
  const pushLabel = pushEnabled ? 'Отключить уведомления о новых сообщениях' : 'Включить уведомления о новых сообщениях'

  return (
    <header
      className={[
        'sticky inset-x-0 top-0 z-20 shrink-0 border-b backdrop-blur',
        isDark ? 'border-slate-800 bg-slate-950/95' : 'border-slate-200 bg-white/95',
      ].join(' ')}
    >
      <div className="mx-auto flex h-14 max-w-[1800px] items-center justify-between gap-3 px-4 md:h-16 md:gap-4 md:px-6">
        <div>
          <p className={["text-[10px] font-semibold uppercase tracking-[0.16em] md:text-xs", isDark ? 'text-sky-400' : 'text-sky-600'].join(' ')}>
            support-agent
          </p>
          <h1 className={["text-lg font-semibold md:text-xl", isDark ? 'text-white' : 'text-slate-900'].join(' ')}>
            VK dialog viewer
          </h1>
        </div>

        <div className="flex items-center gap-2">
          {pushStatus !== 'unsupported' && pushStatus !== 'unavailable' ? (
            <button
              aria-label={pushLabel}
              className={[
                'inline-flex h-10 w-10 items-center justify-center rounded-xl border text-base transition md:h-11 md:w-11 md:text-lg',
                isDark
                  ? 'border-slate-700 bg-slate-900 text-slate-100 hover:border-slate-600 hover:bg-slate-800'
                  : 'border-slate-300 bg-slate-50 text-slate-700 hover:border-slate-400 hover:bg-slate-100',
              ].join(' ')}
              disabled={pushStatus === 'pending'}
              onClick={onPushToggle}
              title={pushLabel}
              type="button"
            >
              <span aria-hidden="true">{pushEnabled ? '🔔' : '🔕'}</span>
            </button>
          ) : null}
          <button
            aria-label={nextThemeLabel}
            className={[
              'inline-flex h-10 w-10 items-center justify-center rounded-xl border text-base transition md:h-11 md:w-11 md:text-lg',
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
      </div>
    </header>
  )
}
