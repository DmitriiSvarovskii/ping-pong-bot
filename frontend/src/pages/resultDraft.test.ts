import {
  hasUnsavedResultDraft,
  loadResultDraft,
  removeResultDraft,
  saveResultDraft,
  type ResultDraftSet,
} from './resultDraft'

const USER_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const USER_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const MATCH_A = '11111111-1111-4111-8111-111111111111'
const MATCH_B = '22222222-2222-4222-8222-222222222222'
const SETS: ResultDraftSet[] = [{ player1_score: 11, player2_score: 7 }]

beforeEach(() => window.localStorage.clear())

it('isolates drafts by match and user', () => {
  saveResultDraft(USER_A, MATCH_A, SETS)

  expect(loadResultDraft(USER_A, MATCH_A)).toEqual(SETS)
  expect(loadResultDraft(USER_A, MATCH_B)).toBeNull()
  expect(loadResultDraft(USER_B, MATCH_A)).toBeNull()
})

it('removes a persisted draft without affecting another match', () => {
  saveResultDraft(USER_A, MATCH_A, SETS)
  saveResultDraft(USER_A, MATCH_B, [{ player1_score: '', player2_score: 4 }])

  removeResultDraft(USER_A, MATCH_A)

  expect(loadResultDraft(USER_A, MATCH_A)).toBeNull()
  expect(loadResultDraft(USER_A, MATCH_B)).toEqual([
    { player1_score: '', player2_score: 4 },
  ])
})

it('ignores malformed local data', () => {
  window.localStorage.setItem(
    `pingstats:result-draft:${USER_A}:${MATCH_A}`,
    JSON.stringify({ sets: [{ player1_score: -1, player2_score: 4 }] }),
  )

  expect(loadResultDraft(USER_A, MATCH_A)).toBeNull()
})

it('detects only an edited draft belonging to the current user', () => {
  saveResultDraft(USER_A, MATCH_A, [{ player1_score: 0, player2_score: 0 }])
  saveResultDraft(USER_B, MATCH_B, SETS)

  expect(hasUnsavedResultDraft(USER_A)).toBe(false)
  expect(hasUnsavedResultDraft(USER_B)).toBe(true)

  saveResultDraft(USER_A, MATCH_A, [{ player1_score: '', player2_score: 0 }])
  expect(hasUnsavedResultDraft(USER_A)).toBe(true)
})
