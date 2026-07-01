import { DialogListItem } from './DialogListItem'

export function DialogList({ dialogs, isLoading, onDayChange, selectedConversationId, selectedDay, onSelect, theme, className = '' }) {
  const isDark = theme === 'dark'

  return (
    <aside
      className={[
        'h-full min-h-0 w-full flex-col border-r md:w-[22rem] md:min-w-[22rem]',
        isDark ? 'border-slate-800 bg-slate-950' : 'border-slate-200 bg-slate-50',
        className,
      ].join(' ')}
    >
      <div className={["border-b px-4 py-3", isDark ? 'border-slate-800' : 'border-slate-200'].join(' ')}>
        <input
          className={[
            'w-full rounded-xl border px-3 py-2 text-sm outline-none ring-0 transition focus:border-sky-500',
            isDark
              ? 'border-slate-700 bg-slate-900 text-slate-100'
              : 'border-slate-300 bg-white text-slate-900',
          ].join(' ')}
          type="date"
          value={selectedDay}
          onChange={(event) => onDayChange(event.target.value)}
        />
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
