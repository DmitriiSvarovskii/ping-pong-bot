export type DraftScore = number | ''

export interface ResultDraftSet {
  player1_score: DraftScore
  player2_score: DraftScore
}

interface StoredResultDraft {
  sets: ResultDraftSet[]
  updatedAt: string
}

const DRAFT_PREFIX = 'pingstats:result-draft:'

export function loadResultDraft(
  userId: string,
  matchId: string,
): ResultDraftSet[] | null {
  try {
    const raw = window.localStorage.getItem(draftKey(userId, matchId))
    if (!raw) return null
    const candidate = JSON.parse(raw) as unknown
    if (!isStoredDraft(candidate)) return null
    return candidate.sets
  } catch {
    return null
  }
}

export function saveResultDraft(userId: string, matchId: string, sets: ResultDraftSet[]): void {
  try {
    const value: StoredResultDraft = { sets, updatedAt: new Date().toISOString() }
    window.localStorage.setItem(draftKey(userId, matchId), JSON.stringify(value))
  } catch {
    // An unavailable storage must not block score entry or submission.
  }
}

export function removeResultDraft(userId: string, matchId: string): void {
  try {
    window.localStorage.removeItem(draftKey(userId, matchId))
  } catch {
    // Successful server persistence remains authoritative when storage cleanup fails.
  }
}

export function hasUnsavedResultDraft(userId: string): boolean {
  try {
    const prefix = `${DRAFT_PREFIX}${userId}:`
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index)
      if (!key?.startsWith(prefix)) continue
      const raw = window.localStorage.getItem(key)
      if (!raw) continue
      const candidate = JSON.parse(raw) as unknown
      if (isStoredDraft(candidate) && candidate.sets.some(hasEditedScore)) return true
    }
  } catch {
    return false
  }
  return false
}

function draftKey(userId: string, matchId: string): string {
  return `${DRAFT_PREFIX}${userId}:${matchId}`
}

function isStoredDraft(value: unknown): value is StoredResultDraft {
  if (!isRecord(value) || !Array.isArray(value.sets) || value.sets.length === 0) return false
  if (typeof value.updatedAt !== 'string') return false
  return value.sets.every((set) =>
    isRecord(set) && isScore(set.player1_score) && isScore(set.player2_score))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isScore(value: unknown): value is DraftScore {
  return value === '' || (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= 32767
  )
}

function hasEditedScore(set: ResultDraftSet): boolean {
  return set.player1_score === '' || set.player2_score === '' ||
    set.player1_score > 0 || set.player2_score > 0
}
