import type { FormEvent, ReactNode } from 'react'

import { getDisplayName } from '../hooks/useDisplayName'
import type { MatchParticipant, SetScoreInput } from '../types/matches'

export type EditableScore = number | ''
export interface EditableSetScore {
  player1_score: EditableScore
  player2_score: EditableScore
}

export function MatchScoreForm({
  players,
  sets,
  onSetsChange,
  onSubmit,
  pending = false,
  disabled = false,
  submitLabel = 'Сохранить результат',
  notice,
  error,
}: {
  players: MatchParticipant[]
  sets: EditableSetScore[]
  onSetsChange: (sets: EditableSetScore[]) => void
  onSubmit: () => void
  pending?: boolean
  disabled?: boolean
  submitLabel?: string
  notice?: ReactNode
  error?: ReactNode
}) {
  const orderedPlayers = [...players].sort((left, right) => left.position - right.position)
  const canSubmit = getScorePayload(sets) !== null

  function updateSet(index: number, position: 1 | 2, value: EditableScore) {
    onSetsChange(sets.map((item, itemIndex) => itemIndex === index
      ? { ...item, [position === 1 ? 'player1_score' : 'player2_score']: value }
      : item))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!pending && !disabled && canSubmit) onSubmit()
  }

  return (
    <form className="score-form" onSubmit={submit}>
      {sets.map((set, index) => (
        <section className="set-editor" key={index}>
          <div className="section-title">
            <h2>Сет {index + 1}</h2>
            {sets.length > 1 && (
              <button
                type="button"
                className="quiet-button"
                onClick={() => onSetsChange(sets.filter((_, itemIndex) => itemIndex !== index))}
              >
                Удалить
              </button>
            )}
          </div>
          <div className="score-grid">
            <ScoreControl
              label={orderedPlayers[0] ? getDisplayName(orderedPlayers[0].user) : 'Игрок A'}
              value={set.player1_score}
              onChange={(value) => updateSet(index, 1, value)}
            />
            <span className="score-divider">:</span>
            <ScoreControl
              label={orderedPlayers[1] ? getDisplayName(orderedPlayers[1].user) : 'Игрок B'}
              value={set.player2_score}
              onChange={(value) => updateSet(index, 2, value)}
            />
          </div>
        </section>
      ))}
      {notice}
      {error}
      <div className="score-form-actions">
        <button
          type="button"
          className="secondary-button"
          onClick={() => onSetsChange([
            ...sets,
            { player1_score: 0, player2_score: 0 },
          ])}
        >
          + Добавить сет
        </button>
        <button className="primary-button" disabled={pending || disabled || !canSubmit}>
          {pending ? 'Сохраняем…' : submitLabel}
        </button>
      </div>
    </form>
  )
}

function ScoreControl({
  label,
  onChange,
  value,
}: {
  label: string
  value: EditableScore
  onChange: (value: EditableScore) => void
}) {
  const numericValue = value === '' ? 0 : value
  return (
    <label className="score-control">
      <span>{label}</span>
      <div>
        <button
          type="button"
          aria-label={`Уменьшить счёт ${label}`}
          onClick={() => onChange(Math.max(0, numericValue - 1))}
        >
          −
        </button>
        <input
          aria-label={`Счёт ${label}`}
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={5}
          value={value}
          onFocus={() => { if (value === 0) onChange('') }}
          onChange={(event) => {
            const next = event.target.value
            if (next === '') onChange('')
            else if (/^\d+$/.test(next) && Number(next) <= 32767) onChange(Number(next))
          }}
        />
        <button
          type="button"
          aria-label={`Увеличить счёт ${label}`}
          onClick={() => onChange(Math.min(32767, numericValue + 1))}
        >
          +
        </button>
      </div>
    </label>
  )
}

// Kept with the form so both result workflows share exactly the same client-side validation.
// eslint-disable-next-line react-refresh/only-export-components
export function getScorePayload(sets: EditableSetScore[]): SetScoreInput[] | null {
  if (!sets.every((set) => isValidScore(set.player1_score) && isValidScore(set.player2_score))) {
    return null
  }
  return sets.map((set) => ({
    player1_score: Number(set.player1_score),
    player2_score: Number(set.player2_score),
  }))
}

function isValidScore(value: EditableScore): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 32767
}
