import { useEffect } from 'react'

const STORAGE_PREFIX = 'pingstats:scroll:'

export function useRouteScrollRestoration(locationKey: string) {
  useEffect(() => {
    const storageKey = `${STORAGE_PREFIX}${locationKey}`
    let saveFrame: number | null = null
    let restoreFrame: number | null = window.requestAnimationFrame(() => {
      restoreFrame = null
      const scrollY = readScrollPosition(storageKey)
      window.scrollTo(0, scrollY)
    })

    const save = () => {
      if (saveFrame !== null) window.cancelAnimationFrame(saveFrame)
      saveFrame = window.requestAnimationFrame(() => {
        saveFrame = null
        writeScrollPosition(storageKey, window.scrollY)
      })
    }
    window.addEventListener('scroll', save, { passive: true })

    return () => {
      window.removeEventListener('scroll', save)
      if (restoreFrame !== null) window.cancelAnimationFrame(restoreFrame)
      if (saveFrame !== null) window.cancelAnimationFrame(saveFrame)
      writeScrollPosition(storageKey, window.scrollY)
    }
  }, [locationKey])
}

function readScrollPosition(key: string): number {
  try {
    const value = Number.parseFloat(window.sessionStorage.getItem(key) ?? '')
    return Number.isFinite(value) && value >= 0 ? value : 0
  } catch {
    return 0
  }
}

function writeScrollPosition(key: string, value: number) {
  try {
    window.sessionStorage.setItem(key, String(Math.max(0, value)))
  } catch {
    // Navigation remains usable when storage is unavailable or full.
  }
}
