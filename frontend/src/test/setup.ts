import '@testing-library/jest-dom/vitest'

beforeEach(() => {
  window.scrollTo = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
})
