import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { saveResultDraft } from '../pages/resultDraft'
import { AppUpdateBanner } from './AppUpdateBanner'

const state = vi.hoisted(() => ({
  availableBuildId: null as string | null,
  userId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
}))

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ user: { id: state.userId } }),
}))
vi.mock('../update/useAppUpdate', () => ({
  useAppUpdate: () => state.availableBuildId,
}))

beforeEach(() => {
  state.availableBuildId = null
  window.localStorage.clear()
})

it('stays hidden when the deployed build matches', () => {
  render(<AppUpdateBanner reload={vi.fn()} />)

  expect(screen.queryByText('Доступна новая версия приложения')).not.toBeInTheDocument()
})

it('shows a non-blocking update notice without reloading automatically', () => {
  state.availableBuildId = 'new-build'
  const reload = vi.fn()
  render(<AppUpdateBanner reload={reload} />)

  expect(screen.getByText('Доступна новая версия приложения')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Обновить' })).toBeInTheDocument()
  expect(reload).not.toHaveBeenCalled()
})

it('reloads on explicit action when there is no edited result draft', async () => {
  state.availableBuildId = 'new-build'
  const reload = vi.fn()
  render(<AppUpdateBanner reload={reload} />)

  await userEvent.click(screen.getByRole('button', { name: 'Обновить' }))

  expect(reload).toHaveBeenCalledOnce()
})

it('requires confirmation and preserves an edited draft before reload', async () => {
  state.availableBuildId = 'new-build'
  const reload = vi.fn()
  saveResultDraft(state.userId, 'match-id', [
    { player1_score: 8, player2_score: 6 },
  ])
  const storedDraft = window.localStorage.getItem(
    `pingstats:result-draft:${state.userId}:match-id`,
  )
  render(<AppUpdateBanner reload={reload} />)

  await userEvent.click(screen.getByRole('button', { name: 'Обновить' }))

  expect(reload).not.toHaveBeenCalled()
  expect(screen.getByText(/Черновик результата сохранён/)).toBeInTheDocument()
  expect(window.localStorage.getItem(
    `pingstats:result-draft:${state.userId}:match-id`,
  )).toBe(storedDraft)

  await userEvent.click(screen.getByRole('button', { name: 'Обновить' }))
  expect(reload).toHaveBeenCalledOnce()
  expect(window.localStorage.getItem(
    `pingstats:result-draft:${state.userId}:match-id`,
  )).toBe(storedDraft)
})
