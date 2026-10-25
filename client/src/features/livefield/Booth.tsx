/**
 * Ask the Booth: one question at a time to a small language model that
 * explains the situation from the context this page already shows. It never
 * predicts or recommends; the answer carries its model, latency and tokens.
 */
import { useState, type FormEvent } from 'react'
import type { BoothTurn } from './liveFieldData'

const FOOTBALL_PROMPTS = [
  'Why did the win probability just move?',
  'What does this offense usually do in this spot?',
  'Should they go for it on fourth down here?',
  'What am I looking at in this formation?',
]

const PROMPTS = [
  'Why did the win probability just move?',
  'What is the standard play in this situation?',
  'Explain the model vs the market for this game.',
  'What should I watch for this at-bat?',
]

export function Booth({ turns, busy, ask, sport }: { turns: BoothTurn[]; busy: boolean; ask: (q: string) => void; sport: string }) {
  const [text, setText] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    ask(text)
    setText('')
  }
  return (
    <section className="lf-booth" aria-label="Ask the Booth">
      <header className="lf-booth__head">
        <h3>Ask the Booth</h3>
        <span className="lf-pill lf-pill--test">test mode · explains only</span>
      </header>
      <p className="lf-muted">A small model (Claude Haiku) answers from the numbers on this page plus general {sport === 'MLB' ? 'baseball' : sport === 'NFL' || sport === 'CFB' ? 'football' : 'game'} knowledge. It does not predict, pick sides or suggest a wager.</p>
      <div className="lf-booth__chips">
        {(sport === 'NFL' || sport === 'CFB' ? FOOTBALL_PROMPTS : PROMPTS).map((p) => <button key={p} type="button" className="lf-chipbtn" disabled={busy} onClick={() => ask(p)}>{p}</button>)}
      </div>
      <form className="lf-booth__form" onSubmit={submit}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask anything about what's happening…" aria-label="Your question" maxLength={600} disabled={busy} />
        <button type="submit" disabled={busy || !text.trim()}>{busy ? 'Thinking…' : 'Ask'}</button>
      </form>
      <ol className="lf-booth__turns">
        {turns.slice(0, 1).map((t) => (
          <li key={t.id} className="lf-turn">
            <p className="lf-turn__q">{t.question}</p>
            {t.answer ? (
              <>
                <p className="lf-turn__a">{t.answer.answer}</p>
                <p className="lf-turn__meta">Claude Haiku · {(t.answer.latency_ms / 1000).toFixed(1)} s · explains only, never a pick or a bet</p>
              </>
            ) : t.error ? <p className="lf-turn__err">{t.error}</p> : <p className="lf-turn__a lf-muted">Thinking…</p>}
          </li>
        ))}
      </ol>
    </section>
  )
}
