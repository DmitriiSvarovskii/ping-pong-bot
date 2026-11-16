export const ratingKeys = {
  all: ['ratings'] as const,
  current: (userId: string) => [...ratingKeys.all, 'current', userId] as const,
  leaderboard: (userId: string) => [...ratingKeys.all, 'leaderboard', userId] as const,
  status: (userId: string) => [...ratingKeys.all, 'status', userId] as const,
  player: (userId: string) => [...ratingKeys.all, 'player', userId] as const,
}
