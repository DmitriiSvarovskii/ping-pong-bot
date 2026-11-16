import { ApiError } from '../api/client'

export function isRatingNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === 'rating_not_found'
}
