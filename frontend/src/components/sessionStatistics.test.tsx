import { render, screen, waitFor, within } from '@testing-library/react'
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
    rating_state: 'not_applicable',
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

  it('renders loading, error, and empty history states independently', async () => {
    const retry = vi.fn()
    const { rerender } = render(<SessionHistory matches={[]} loading />)
    expect(screen.getByText('Загружаем историю матчей…')).toBeInTheDocument()

    rerender(<SessionHistory matches={[]} error={new Error('offline')} onRetry={retry} />)
    expect(screen.getByText('Не удалось загрузить историю матчей.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))
    expect(retry).toHaveBeenCalledTimes(1)

    rerender(<SessionHistory matches={[]} />)
    expect(screen.getByText('Завершённых матчей пока нет.')).toBeInTheDocument()
  })
  it('allows voiding only manageable completed matches', async () => {
    const onVoidMatch = vi.fn()
    const completed = completedMatch('2026-11-08T11:32:00Z')

    const voided: SessionMatch = {
      ...completed,
      id: 'voided-match',
      status: 'voided',
      winner_id: null,
      sequence_number: 5,
    }

    const cancelled: SessionMatch = {
      ...completed,
      id: 'cancelled-match',
      status: 'cancelled',
      winner_id: null,
      sequence_number: 6,
    }

    const { rerender } = render(
      <SessionHistory
        matches={[completed, voided, cancelled]}
        onVoidMatch={onVoidMatch}
      />,
    )

    expect(screen.getAllByLabelText(/Действия матча/)).toHaveLength(1)

    const menu = screen.getByLabelText('Действия матча 4')

    await userEvent.click(menu)
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'Аннулировать матч' }),
    )

    expect(onVoidMatch).toHaveBeenCalledTimes(1)
    expect(onVoidMatch).toHaveBeenCalledWith(completed)
    expect(menu.closest('details')).not.toHaveAttribute('open')

    rerender(
      <SessionHistory matches={[completed, voided, cancelled]} />,
    )

    expect(screen.queryByLabelText(/Действия матча/))
      .not.toBeInTheDocument()
  })
  it('shows whole-session counts and the published rating states', () => {
    const pending = {
      ...completedMatch('2026-11-08T11:32:00Z'),
      id: 'pending-match',
      is_ranked: true,
      rating_state: 'pending' as const,
    }
    const recalculating = {
      ...pending,
      id: 'recalculating-match',
      rating_state: 'recalculating' as const,
      sequence_number: 5,
    }
    const voided = {
      ...pending,
      id: 'voided-match',
      status: 'voided' as const,
      rating_state: 'not_applicable' as const,
      winner_id: null,
      sequence_number: 6,
    }
    const cancelled = {
      ...pending,
      id: 'cancelled-match',
      status: 'cancelled' as const,
      rating_state: 'not_applicable' as const,
      winner_id: null,
      completed_at: null,
      sequence_number: 7,
    }

    render(
      <SessionHistory
        matches={[pending, recalculating, voided, cancelled]}
        summary={{ played: 2, ranked: 2, friendly: 0, pending_rating: 2 }}
      />,
    )

    expect(screen.getByText('Ожидает расчёта')).toBeInTheDocument()
    expect(screen.getByText('Пересчитывается')).toBeInTheDocument()
    expect(screen.getByText('Исключён из статистики и рейтинга')).toBeInTheDocument()
    expect(screen.getByText('Отменён / пропущен')).toBeInTheDocument()
    expect(screen.getByLabelText('Статистика матчей сессии')).toHaveTextContent(
      'Сыграно2Рейтинговых2Товарищеских0Ожидают расчёта2',
    )
  })

  it('shows the edit action only when a completed match is manageable', async () => {
    const onEditMatch = vi.fn()
    const completed = completedMatch('2026-11-08T11:32:00Z')
    const cancelled = {
      ...completed,
      id: 'cancelled-match',
      status: 'cancelled' as const,
      completed_at: null,
    }
    const { rerender } = render(
      <SessionHistory
        matches={[completed, cancelled]}
        onEditMatch={onEditMatch}
      />,
    )

    expect(screen.getByLabelText('Действия матча 4')).toBeInTheDocument()
    expect(screen.getAllByLabelText(/Действия матча/)).toHaveLength(1)
    await userEvent.click(screen.getByLabelText('Действия матча 4'))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Редактировать результат' }))
    expect(onEditMatch).toHaveBeenCalledWith(completed)

    rerender(<SessionHistory matches={[completed]} />)
    expect(screen.queryByLabelText('Действия матча 4')).not.toBeInTheDocument()
  })

  it('requests another page when a session history longer than one hundred reaches the end', async () => {
    const onLoadNext = vi.fn()
    vi.stubGlobal(
      'IntersectionObserver',
      class {
        constructor(private readonly callback: IntersectionObserverCallback) { }
        observe(target: Element) {
          this.callback(
            [{ isIntersecting: true, target } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver,
          )
        }
        disconnect() { }
      },
    )

    render(
      <SessionHistory
        matches={[completedMatch('2026-11-08T11:32:00Z')]}
        total={101}
        hasNextPage
        onLoadNext={onLoadNext}
      />,
    )

    expect(screen.getByText('101')).toBeInTheDocument()
    await waitFor(() => expect(onLoadNext).toHaveBeenCalledTimes(1))
    vi.unstubAllGlobals()
  })
})
