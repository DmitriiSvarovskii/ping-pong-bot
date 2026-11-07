import { Link } from 'react-router-dom'

import { PaddleIcon } from '../components/icons'

export function PlaceholderPage({ title, description }: { title: string; description: string }) {
  return <div className="placeholder"><div className="placeholder-icon"><PaddleIcon /></div><p className="eyebrow">СКОРО</p><h1>{title}</h1><p>{description}</p><Link className="secondary-button" to="/">Вернуться на главную</Link></div>
}
