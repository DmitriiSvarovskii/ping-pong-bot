import { useState } from 'react'

import { getDisplayName } from '../hooks/useDisplayName'
import type { SessionPlayerStatistics } from '../types/gameSessions'

const medals = ['🥇', '🥈', '🥉']

export function SessionLeaderboard({ items }: { items: SessionPlayerStatistics[] }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? items : items.slice(0, 5)

  return <section className="section-card session-leaderboard">
    <div className="section-title"><h2>🏆 Лидеры по победам</h2><span>{items.length}</span></div>
    <p className="leaderboard-legend" id="leaderboard-legend">В — победы, П — поражения, ± — разница мячей</p>
    {items.length ? <table aria-describedby="leaderboard-legend">
      <thead><tr><th scope="col">#</th><th scope="col">Игрок</th><th scope="col" aria-label="Победы">В</th><th scope="col" aria-label="Поражения">П</th><th scope="col" aria-label="Разница мячей">±</th></tr></thead>
      <tbody>{visible.map((item, index) => <tr key={item.user.id}>
        <td aria-label={`${index + 1} место`}>{medals[index] ?? index + 1}</td>
        <th scope="row" title={getDisplayName(item.user)}>{getDisplayName(item.user)}</th>
        <td>{item.wins}</td><td>{item.losses}</td>
        <td className={item.point_difference > 0 ? 'positive' : item.point_difference < 0 ? 'negative' : ''}>{item.point_difference > 0 ? '+' : ''}{item.point_difference}</td>
      </tr>)}</tbody>
    </table> : <p className="muted-copy">Участников пока нет.</p>}
    {items.length > 5 && <button className="quiet-button leaderboard-toggle" type="button" onClick={() => setExpanded((value) => !value)}>{expanded ? 'Свернуть' : 'Показать всех'}</button>}
  </section>
}
