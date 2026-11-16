import type { RatingState } from '../types/matches'
import type { RatingCalculationState } from '../types/ratings'

type NumericValue = number | string | null | undefined

export type RatingReliability = 'high' | 'medium' | 'low'

const EMPTY_VALUE = '—'

const ratingStateLabels: Record<RatingState, string> = {
  not_applicable: 'Не учитывается',
  pending: 'Ожидает расчёта',
  rated: 'Учтён в рейтинге',
  recalculating: 'Пересчитывается',
}

const calculationStateLabels: Record<RatingCalculationState, string> = {
  up_to_date: 'Рейтинг актуален',
  pending: 'Ожидает расчёта',
  calculating: 'Рассчитывается',
  rebuilding: 'Пересчитывается история',
  delayed: 'Расчёт задерживается',
}

const reliabilityLabels: Record<RatingReliability, string> = {
  high: 'Высокая',
  medium: 'Средняя',
  low: 'Низкая',
}

export function roundRating(value: NumericValue): number | null {
  const numeric = finiteNumber(value)
  return numeric === null ? null : Math.round(numeric)
}

export function formatRating(value: NumericValue): string {
  const rounded = roundRating(value)
  return rounded === null ? EMPTY_VALUE : String(rounded)
}

export function formatRatingPosition(position: number | null | undefined): string {
  return Number.isInteger(position) && (position ?? 0) > 0 ? `#${position}` : EMPTY_VALUE
}

export function calculateRatingChange(
  ratingBefore: NumericValue,
  ratingAfter: NumericValue,
): number | null {
  const before = finiteNumber(ratingBefore)
  const after = finiteNumber(ratingAfter)
  return before === null || after === null ? null : after - before
}

export function formatRatingChange(
  ratingBefore: NumericValue,
  ratingAfter: NumericValue,
): string {
  const change = calculateRatingChange(ratingBefore, ratingAfter)
  if (change === null) return EMPTY_VALUE
  const rounded = Math.sign(change) * Math.round(Math.abs(change))
  if (rounded > 0) return `+${rounded}`
  if (rounded < 0) return `−${Math.abs(rounded)}`
  return '0'
}

export function getRatingReliability(effectiveRd: NumericValue): RatingReliability | null {
  const rd = finiteNumber(effectiveRd)
  if (rd === null || rd < 0) return null
  if (rd <= 50) return 'high'
  if (rd <= 100) return 'medium'
  return 'low'
}

export function formatRatingReliability(effectiveRd: NumericValue): string {
  const reliability = getRatingReliability(effectiveRd)
  return reliability === null ? EMPTY_VALUE : reliabilityLabels[reliability]
}

export function formatRatingPeriodDate(
  value: string | null | undefined,
  locale = 'ru-RU',
): string {
  const date = validDate(value)
  if (!date) return EMPTY_VALUE
  return new Intl.DateTimeFormat(locale, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date)
}

export function formatNextCalculationAt(
  value: string | null | undefined,
  options: { locale?: string; timeZone?: string } = {},
): string {
  const date = validDate(value)
  if (!date) return EMPTY_VALUE
  return new Intl.DateTimeFormat(options.locale ?? 'ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...(options.timeZone ? { timeZone: options.timeZone } : {}),
  }).format(date)
}

export function formatRatingState(state: RatingState | null | undefined): string {
  return state ? ratingStateLabels[state] : EMPTY_VALUE
}

export function formatRatingCalculationState(
  state: RatingCalculationState | null | undefined,
): string {
  return state ? calculationStateLabels[state] : EMPTY_VALUE
}

function finiteNumber(value: NumericValue): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function validDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}
