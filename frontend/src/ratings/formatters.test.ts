import {
  calculateRatingChange,
  formatNextCalculationAt,
  formatRating,
  formatRatingCalculationState,
  formatRatingChange,
  formatRatingPeriodDate,
  formatRatingPosition,
  formatRatingReliability,
  formatRatingState,
  getRatingReliability,
  roundRating,
} from './formatters'

describe('rating formatters', () => {
  it('rounds decimal strings without inventing a missing rating', () => {
    expect(roundRating('1612.50000')).toBe(1613)
    expect(formatRating('1612.49999')).toBe('1612')
    expect(roundRating(null)).toBeNull()
    expect(formatRating(undefined)).toBe('—')
  })

  it('formats positions and calculates change before rounding display values', () => {
    expect(formatRatingPosition(1)).toBe('#1')
    expect(formatRatingPosition(null)).toBe('—')
    expect(calculateRatingChange('1500.6', '1501.4')).toBeCloseTo(0.8)
    expect(formatRatingChange('1500.6', '1501.4')).toBe('+1')
    expect(formatRatingChange('1501.4', '1500.6')).toBe('−1')
    expect(formatRatingChange('1500.5', '1500')).toBe('−1')
    expect(formatRatingChange(null, '1500')).toBe('—')
  })

  it('uses the approved effective RD reliability thresholds', () => {
    expect(getRatingReliability('50')).toBe('high')
    expect(getRatingReliability('50.00001')).toBe('medium')
    expect(getRatingReliability(100)).toBe('medium')
    expect(getRatingReliability('100.01')).toBe('low')
    expect(formatRatingReliability(null)).toBe('—')
  })

  it('formats period dates in UTC and calculation time in a selected local zone', () => {
    expect(formatRatingPeriodDate('2026-11-10T23:30:00-03:00')).toBe('11.11.2026')
    const local = formatNextCalculationAt('2026-11-17T00:05:00Z', {
      timeZone: 'Europe/Moscow',
    })
    expect(local).toContain('17.11.2026')
    expect(local).toContain('03:05')
    expect(formatRatingPeriodDate('invalid')).toBe('—')
  })

  it('provides Russian labels for public states', () => {
    expect(formatRatingState('rated')).toBe('Учтён в рейтинге')
    expect(formatRatingState('recalculating')).toBe('Пересчитывается')
    expect(formatRatingCalculationState('delayed')).toBe('Расчёт задерживается')
    expect(formatRatingCalculationState(null)).toBe('—')
  })
})
