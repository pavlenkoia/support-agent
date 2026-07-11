import { DialogListItem } from './DialogListItem'

export function DialogList({
  dialogs,
  isLoading,
  isRefreshing = false,
  onDayChange,
  onRefresh,
  selectedConversationId,
  selectedDay,
  onSelect,
  theme,
  className = '',
}) {
  const isDark = theme === 'dark'

  return (
    <aside
      className={[
        'h-full min-h-0 w-full flex-col border-r',
        isDark ? 'border-slate-800 bg-slate-950' : 'border-slate-200 bg-slate-50',
        className,
      ].join(' ')}
    >
      <div className={["border-b px-4 py-3", isDark ? 'border-slate-800' : 'border-slate-200'].join(' ')}>
        <div className="flex items-center gap-2">
          <input
            className={[
              'min-w-0 flex-1 rounded-xl border px-3 py-2 text-sm outline-none ring-0 transition focus:border-sky-500',
              isDark
                ? 'border-slate-700 bg-slate-900 text-slate-100'
                : 'border-slate-300 bg-white text-slate-900',
            ].join(' ')}
            type="date"
            value={selectedDay}
            onChange={(event) => onDayChange(event.target.value)}
          />
          <button
            type="button"
            onClick={onRefresh}
            disabled={isRefreshing}
            title="Обновить текущую дату"
            aria-label="Обновить текущую дату"
            className={[
              'inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border transition focus:outline-none focus:ring-2 focus:ring-sky-500/50 disabled:cursor-wait disabled:opacity-60',
              isDark
                ? 'border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800'
                : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-100',
            ].join(' ')}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={["h-4 w-4", isRefreshing ? 'animate-spin' : ''].join(' ')}
              aria-hidden="true"
            >
              <path d="M21 2v6h-6" />
              <path d="M3 12a9 9 0 0 1 15.55-6.36L21 8" />
              <path d="M3 22v-6h6" />
              <path d="M21 12a9 9 0 0 1-15.55 6.36L3 16" />
            </svg>
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {isLoading ? (
          <div
            className={[
              'rounded-2xl border px-4 py-6 text-sm',
              isDark ? 'border-slate-800 bg-slate-900/60 text-slate-400' : 'border-slate-200 bg-white text-slate-500',
            ].join(' ')}
          >
            Загружаю диалоги…
          </div>
        ) : dialogs.length === 0 ? (
          <div
            className={[
              'rounded-2xl border border-dashed px-4 py-6 text-sm',
              isDark ? 'border-slate-800 bg-slate-900/40 text-slate-400' : 'border-slate-300 bg-white text-slate-500',
            ].join(' ')}
          >
            За выбранный день диалогов нет.
          </div>
        ) : (
          <div className="space-y-3">
            {dialogs.map((dialog) => (
              <DialogListItem
                key={dialog.conversation_id}
                dialog={dialog}
                isActive={dialog.conversation_id === selectedConversationId}
                onClick={() => onSelect(dialog.conversation_id)}
                theme={theme}
              />
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
