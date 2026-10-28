/** ROOKIE QUESTS (Start page): four first steps, ticked off as you do them, +50 XP each and a trophy for all four. */
import { Link } from 'react-router-dom'
import { XP_QUEST, useProgress } from './progress'
import './progress.css'

export function QuestCard() {
  const p = useProgress()
  const done = p.quests.filter((q) => q.done).length
  return (
    <section className={`qc${done === p.quests.length ? ' is-done' : ''}`} aria-label="Rookie quests">
      <header className="qc__head">
        <h2>ROOKIE QUESTS</h2>
        <span>{done}/{p.quests.length} done · +{XP_QUEST} XP each · finish all four for the 🎓 Rookie Graduate trophy</span>
        <i className="qc__bar"><b style={{ width: `${(100 * done) / p.quests.length}%` }} /></i>
      </header>
      <ol className="qc__list">
        {p.quests.map((q, i) => (
          <li key={q.id} className={q.done ? 'is-done' : ''}>
            <span className="qc__n">{q.done ? '✓' : i + 1}</span>
            <span className="qc__txt"><b>{q.title}</b><small>{q.how}</small></span>
            {q.done ? <em>+{XP_QUEST} XP</em> : <Link to={q.to}>Go →</Link>}
          </li>
        ))}
      </ol>
    </section>
  )
}
