const DATE_INPUT_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

export const HORIZONTAL_DAY_SWIPE_MIN_DISTANCE_PX = 64
export const HORIZONTAL_DAY_SWIPE_MAX_VERTICAL_DRIFT_PX = 96

export function shiftDateInputDay(value, offsetDays) {
  const match = DATE_INPUT_PATTERN.exec(value)
  if (!match || !Number.isInteger(offsetDays)) return value

  const [, year, month, day] = match
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day) + offsetDays))

  return date.toISOString().slice(0, 10)
}

export function getHorizontalDaySwipeDirection({ startX, startY, endX, endY }) {
  const deltaX = endX - startX
  const deltaY = endY - startY
  const horizontalDistance = Math.abs(deltaX)
  const verticalDistance = Math.abs(deltaY)

  if (
    horizontalDistance < HORIZONTAL_DAY_SWIPE_MIN_DISTANCE_PX ||
    verticalDistance > HORIZONTAL_DAY_SWIPE_MAX_VERTICAL_DRIFT_PX ||
    horizontalDistance <= verticalDistance
  ) {
    return null
  }

  return deltaX > 0 ? 'previous' : 'next'
}
