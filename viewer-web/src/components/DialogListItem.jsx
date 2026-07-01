import { formatClockTime } from '../utils/time'

function formatCount(count) {
  if (count % 10 === 1 && count % 100 !== 11) return `${count} сообщение`
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) return `${count} сообщения`
  return `${count} сообщений`
}

export function DialogListItem({ dialog, isActive, onClick, theme }) {
  const isDark = theme === 'dark'
  const title = dialog.case_id ? `Обращение №${dialog.case_id}` : dialog.display_name || dialog.external_chat_id
  const subtitle = `${formatClockTime(dialog.last_message_at)} · ${formatCount(dialog.message_count)}`

  return (
    <button
      className={[
        'w-full rounded-2xl border px-4 py-3 text-left transition',
        isActive
          ? isDark
            ? 'border-sky-500 bg-sky-500/10 shadow-[0_0_0_1px_rgba(14,165,233,0.15)]'
            : 'border-sky-500 bg-sky-50 shadow-[0_0_0_1px_rgba(14,165,233,0.12)]'
          : isDark
            ? 'border-slate-800 bg-slate-900/70 hover:border-slate-700 hover:bg-slate-900'
            : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50',
      ].join(' ')}
      onClick={onClick}
      type="button"
    >
      <div className={["truncate text-sm font-semibold", isDark ? 'text-slate-100' : 'text-slate-900'].join(' ')}>{title}</div>
      <div className={["mt-1 text-sm", isDark ? 'text-slate-400' : 'text-slate-500'].join(' ')}>{subtitle}</div>
    </button>
  )
}
