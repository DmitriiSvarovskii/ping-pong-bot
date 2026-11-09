import styles from './index.css?raw'
import document from '../../index.html?raw'

describe('application safe area layout', () => {
  it('stacks device and Telegram content insets without duplicating bridge sources', () => {
    expect(styles).toContain(
      'calc(var(--tg-safe-area-inset-top, 0px) + var(--tg-content-safe-area-inset-top, 0px))',
    )
    expect(styles).toContain('var(--app-telegram-safe-content-inset-top, 0px)')
    expect(styles).toContain(
      'calc(var(--tg-safe-area-inset-bottom, 0px) + var(--tg-content-safe-area-inset-bottom, 0px))',
    )
    expect(styles).toContain('var(--app-telegram-safe-content-inset-bottom, 0px)')
  })

  it('reserves the same bottom safe area for page content and fixed navigation', () => {
    expect(styles).toContain('padding-bottom: calc(76px + var(--app-safe-bottom))')
    expect(styles).toContain('calc(10px + var(--app-safe-bottom))')
  })
})

describe('mobile zoom prevention', () => {
  it('uses manipulation touch behavior without disabling accessible pinch zoom', () => {
    expect(styles).toContain(
      'button, a, label, input, textarea, select { touch-action: manipulation; }',
    )
    expect(document).not.toContain('user-scalable=no')
    expect(document).not.toMatch(/maximum-scale\s*=\s*1(?:\.0)?/)
  })

  it('keeps every text-like form control at the iOS-safe 16px minimum', () => {
    expect(styles).toContain(
      'input:not([type="checkbox"]), textarea, select { font-size: 1rem; }',
    )
    expect(styles).toContain('.score-control input')
    expect(styles).toContain('font-size: 1.5rem')
  })
})

describe('active session player layout', () => {
  it('wraps current-pair names without ellipsis, including unbroken names', () => {
    expect(styles).toContain(
      '.queue-player-name { min-width: 0; line-height: 1.35; overflow-wrap: anywhere; white-space: normal; }',
    )
    expect(styles).not.toContain('.player-chip strong { overflow: hidden; text-overflow: ellipsis')
  })
})
