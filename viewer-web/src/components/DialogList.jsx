import { useEffect, useRef, useState } from 'react'

import { getHorizontalDaySwipeDirection, shiftDateInputDay } from '../utils/day-navigation'
import { DialogListItem } from './DialogListItem'

const DAY_SWIPE_COMPLETE_ANIMATION_MS = 180
const DAY_SWIPE_MAX_VERTICAL_DRIFT_PX = 96

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
  const [dragOffsetX, setDragOffsetX] = useState(0)
  const [isDragging, setIsDragging] = useState(false)
  const [isTeleporting, setIsTeleporting] = useState(false)
  const swipeStateRef = useRef({ startX: null, startY: null, panelWidth: 0, tracking: false })
  const suppressClickRef = useRef(false)
  const suppressClickTimerRef = useRef(null)
  const completeTimerRef = useRef(null)
  const resetFrameRef = useRef(null)

  useEffect(() => () => {
    if (suppressClickTimerRef.current !== null) window.clearTimeout(suppressClickTimerRef.current)
    if (completeTimerRef.current !== null) window.clearTimeout(completeTimerRef.current)
    if (resetFrameRef.current !== null) window.cancelAnimationFrame(resetFrameRef.current)
  }, [])

  const clearCompletionTimer = () => {
    if (completeTimerRef.current !== null) {
      window.clearTimeout(completeTimerRef.current)
      completeTimerRef.current = null
    }
  }

  const clearSuppressClickTimer = () => {
    if (suppressClickTimerRef.current !== null) {
      window.clearTimeout(suppressClickTimerRef.current)
      suppressClickTimerRef.current = null
    }
  }

  const resetSwipeState = ({ keepOffset = false } = {}) => {
    swipeStateRef.current = { startX: null, startY: null, panelWidth: 0, tracking: false }
    setIsDragging(false)
    if (!keepOffset) setDragOffsetX(0)
  }

  const changeDay = (nextDay) => {
    if (!nextDay || nextDay === selectedDay) return
    onDayChange(nextDay)
  }

  const handleDateInputChange = (event) => {
    changeDay(event.target.value)
  }

  const finishDaySwipe = (direction) => {
    const width = swipeStateRef.current.panelWidth || (typeof window !== 'undefined' ? window.innerWidth : 0)
    const exitOffset = direction === 'previous' ? width : -width

    resetSwipeState({ keepOffset: true })
    setDragOffsetX(exitOffset)

    clearCompletionTimer()
    completeTimerRef.current = window.setTimeout(() => {
      completeTimerRef.current = null
      setIsTeleporting(true)
      changeDay(shiftDateInputDay(selectedDay, direction === 'previous' ? -1 : 1))
      setDragOffsetX(0)

      resetFrameRef.current = window.requestAnimationFrame(() => {
        resetFrameRef.current = null
        setIsTeleporting(false)
      })
    }, DAY_SWIPE_COMPLETE_ANIMATION_MS)
  }

  const handleListTouchStart = (event) => {
    const touch = event.touches?.[0]
    if (!touch) return

    clearCompletionTimer()
    clearSuppressClickTimer()
    setIsTeleporting(false)
    setIsDragging(false)
    setDragOffsetX(0)
    swipeStateRef.current = {
      startX: touch.clientX,
      startY: touch.clientY,
      panelWidth: event.currentTarget.clientWidth || 0,
      tracking: true,
    }
  }

  const handleListTouchMove = (event) => {
    const { startX, startY, panelWidth, tracking } = swipeStateRef.current
    const touch = event.touches?.[0]
    if (!tracking || startX === null || startY === null || !touch) return

    const deltaX = touch.clientX - startX
    const deltaY = touch.clientY - startY
    const horizontalDistance = Math.abs(deltaX)
    const verticalDistance = Math.abs(deltaY)

    if (
      horizontalDistance <= verticalDistance ||
      verticalDistance > DAY_SWIPE_MAX_VERTICAL_DRIFT_PX
    ) {
      resetSwipeState()
      return
    }

    const maxOffset = panelWidth || (typeof window !== 'undefined' ? window.innerWidth : horizontalDistance)
    setIsDragging(true)
    setDragOffsetX(Math.max(-maxOffset, Math.min(deltaX, maxOffset)))
  }

  const handleListTouchEnd = (event) => {
    const { startX, startY, tracking } = swipeStateRef.current
    const touch = event.changedTouches?.[0]
    if (!tracking || startX === null || startY === null || !touch) {
      resetSwipeState()
      return
    }

    const direction = getHorizontalDaySwipeDirection({
      startX,
      startY,
      endX: touch.clientX,
      endY: touch.clientY,
    })
    if (!direction) {
      resetSwipeState()
      return
    }

    suppressClickRef.current = true
    clearSuppressClickTimer()
    suppressClickTimerRef.current = window.setTimeout(() => {
      suppressClickTimerRef.current = null
      suppressClickRef.current = false
    }, 300)
    finishDaySwipe(direction)
  }

  const listStyle = {
    touchAction: 'pan-y',
    transform: dragOffsetX === 0 ? 'translate3d(0, 0, 0)' : `translate3d(${dragOffsetX}px, 0, 0)`,
    transition: isDragging || isTeleporting ? 'none' : 'transform 220ms cubic-bezier(0.22, 1, 0.36, 1)',
    willChange: dragOffsetX === 0 ? 'auto' : 'transform',
  }

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
            onChange={handleDateInputChange}
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

      <div
        className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-4 py-4"
        onTouchStart={handleListTouchStart}
        onTouchMove={handleListTouchMove}
        onTouchEnd={handleListTouchEnd}
        onTouchCancel={() => resetSwipeState()}
      >
        <div
          style={listStyle}
          onClickCapture={(event) => {
            if (!suppressClickRef.current) return
            suppressClickRef.current = false
            event.preventDefault()
            event.stopPropagation()
          }}
        >
          <div className="viewer-day-content">
            {isLoading ? null : dialogs.length === 0 ? (
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
        </div>
      </div>
    </aside>
  )
}
