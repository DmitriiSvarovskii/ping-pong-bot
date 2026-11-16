export const ratingKeys = {
  all: ['ratings'] as const,
  current: (userId: string) => [...ratingKeys.all, 'current', userId] as const,
  leaderboard: (userId: string) => [...ratingKeys.all, 'leaderboard', userId] as const,
}
