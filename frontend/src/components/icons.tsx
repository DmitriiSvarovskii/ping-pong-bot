import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

const baseProps: IconProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

export function HomeIcon(props: IconProps) {
  return <svg {...baseProps} {...props}><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10M9 20v-6h6v6"/></svg>
}

export function UserIcon(props: IconProps) {
  return <svg {...baseProps} {...props}><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>
}

export function PlusIcon(props: IconProps) {
  return <svg {...baseProps} {...props}><path d="M12 5v14M5 12h14"/></svg>
}

export function JoinIcon(props: IconProps) {
  return <svg {...baseProps} {...props}><path d="M15 4h4v16h-4M10 8l4 4-4 4M14 12H3"/></svg>
}

export function PaddleIcon(props: IconProps) {
  return <svg {...baseProps} {...props}><circle cx="9" cy="9" r="6"/><path d="m13.5 13.5 5.5 5.5M18 5h.01"/></svg>
}

export function ChevronIcon(props: IconProps) {
  return <svg {...baseProps} {...props}><path d="m9 18 6-6-6-6"/></svg>
}
