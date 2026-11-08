import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { UserPublicProfile } from '../types/api'
import type { SessionMatch, SessionPlayerStatistics } from '../types/gameSessions'
import { SessionHistory } from './SessionHistory'
import { SessionLeaderboard } from './SessionLeaderboard'

function profile(index: number): UserPublicProfile {
  return {
    id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    first_name: `Игрок ${index}`,
    last_name: null,
    username: null,
    photo_url: null,
    created_at: '2026-11-07T10:00:00Z',
    last_active_at: null,
  }
}

function statistic(index: number): SessionPlayerStatistics {
  return {
    user: profile(index),
    wins: 10 - index,
    losses: index,
    points_for: 100,
    points_against: index === 1 ? 80 : 100 + index,
    point_difference: index === 1 ? 20 : -index,
  }
}

function completedMatch(completedAt: string | null): SessionMatch {
  const first = profile(1)
  const second = profile(2)
  return {
    id: '11111111-1111-4111-8111-111111111111',
    context: 'game_session',
    status: 'completed',
    created_by: first.id,
    points_to_win: 11,
    win_by: 2,
    sets_to_win: 2,
    is_ranked: false,
    winner_id: first.id,
    created_at: '2026-11-07T10:00:00Z',
    updated_at: '2026-11-08T11:32:00Z',
    started_at: '2026-11-08T11:20:00Z',
    completed_at: completedAt,
    cancelled_at: null,
    version: 2,
    participants: [{ position: 1, user: first }, { position: 2, user: second }],
    sets: [
      { id: 's1', set_number: 1, player1_score: 11, player2_score: 8, created_at: '2026-11-08T11:25:00Z' },
      { id: 's2', set_number: 2, player1_score: 9, player2_score: 11, created_at: '2026-11-08T11:28:00Z' },
      { id: 's3', set_number: 3, player1_score: 11, player2_score: 6, created_at: '2026-11-08T11:32:00Z' },
    ],
    game_session_id: '22222222-2222-4222-8222-222222222222',
    sequence_number: 4,
  }
}

describe('session leaderboard', () => {
  it('shows top five, signed differences, and expands and collapses a longer list', async () => {
    render(<SessionLeaderboard items={Array.from({ length: 7 }, (_, index) => statistic(index + 1))} />)

    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(6)
    expect(within(table).getByText('+20')).toBeInTheDocument()
    expect(within(table).getByText('-2')).toBeInTheDocument()
    expect(screen.getByText('В — победы, П — поражения, ± — разница мячей')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Показать всех' }))
    expect(within(table).getAllByRole('row')).toHaveLength(8)
    await userEvent.click(screen.getByRole('button', { name: 'Свернуть' }))
    expect(within(table).getAllByRole('row')).toHaveLength(6)
  })

  it('does not show an expand control for five players', () => {
    render(<SessionLeaderboard items={Array.from({ length: 5 }, (_, index) => statistic(index + 1))} />)
    expect(screen.queryByRole('button', { name: 'Показать всех' })).not.toBeInTheDocument()
  })
})

describe('session history', () => {
  it('shows the set score and a localized completion time', () => {
    render(<SessionHistory matches={[completedMatch('2026-11-08T11:32:00Z')]} />)

    expect(screen.getByText('2:1')).toBeInTheDocument()
    expect(screen.getByText(/8 ноября/)).toBeInTheDocument()
    expect(screen.queryByText('2026-11-08T11:32:00Z')).not.toBeInTheDocument()
  })

  it('does not invent a timestamp when completion time is absent', () => {
    render(<SessionHistory matches={[completedMatch(null)]} />)
    expect(screen.getByText('Время завершения неизвестно')).toBeInTheDocument()
  })
})
