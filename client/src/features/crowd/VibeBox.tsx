/**
 * THE CROWD (owner 10-27: "a small box in the upper right corner ... what the vibe is in the public ... the feeling
 * that you are getting hot off the presses current info ... how the crowd reacts to big plays or controversial calls").
 *
 * Public posts from the last 6 hours on Bluesky and Mastodon (free, keyless) through the test-mode preview server
 * (/preview-api/vibe), scored locally with a sports lexicon. Read every 2 minutes. Colour, not a probability and never
 * a wager signal; X/Twitter and Reddit refuse free access, so they are not here.
 */
import { useEffect, useState } from 'react'
import './vibe.css'

export interface Vibe {
  label: 'QUIET' | 'SALTY' | 'HYPED' | 'ANGRY' | 'NERVOUS' | 'MIXED'
  vibe: number
  posts: number
  per_hour: number
  controversy: number
  split: Record<'home' | 'away', { team: string; mentions: number; vibe: number | null } | undefined>
  trending: string[]
  quotes: { text: string; author: string; source: string; url: string | null; age_min: number; tone: 'up' | 'down' | 'flat' }[]
  reason?: string
}

type Team = { abbr: string; name?: string | null }
const LEAGUES = ['nfl', 'nba', 'nhl', 'cfb', 'mlb'] as const
const MOOD: Record<Vibe['label'], { icon: string; word: string }> = {
  HYPED: { icon: '🔥', word: 'Hyped' },
  ANGRY: { icon: '😤', word: 'Angry' },
  SALTY: { icon: '🧂', word: 'Salty at the refs' },
  NERVOUS: { icon: '😬', word: 'Nervous' },
  MIXED: { icon: '🌗', word: 'Mixed' },
  QUIET: { icon: '🤫', word: 'Quiet' },
}

export function useVibe(league: string, home?: Team | null, away?: Team | null) {
  const [data, setData] = useState<Vibe | null>(null)
  const [failed, setFailed] = useState(false)
  const key = `${league}|${home?.abbr ?? ''}|${away?.abbr ?? ''}`
  useEffect(() => {
    let alive = true
    setData(null)
    setFailed(false)
    const qs = new URLSearchParams({ league: league.toLowerCase() })
    if (home && away) {
      qs.set('home', home.abbr); qs.set('away', away.abbr)
      if (home.name) qs.set('home_name', home.name)
      if (away.name) qs.set('away_name', away.name)
    }
    const load = () => fetch(`/preview-api/vibe?${qs}`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((b: Vibe) => { if (alive) { setData(b); setFailed(false) } })
      .catch(() => { if (alive) setFailed(true) })
    void load()
    const t = window.setInterval(load, 120_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, failed }
}

const ago = (m: number) => (m < 1 ? 'just now' : m < 60 ? `${m}m ago` : `${Math.floor(m / 60)}h ago`)

/** With both teams: the crowd around that game. Without: a whole league, with a league switcher. */
export function VibeBox({ league, home, away, className = '', inline = false }: { league: string; home?: Team | null; away?: Team | null; className?: string; inline?: boolean }) {
  // Owner 10-27: the box must not push the page down. As a strip (default) the full read opens over the page; `inline`
  // shows the full card in place, for a side column that has room (Game Night rail, beside Shop the Line).
  const [open, setOpen] = useState(inline)
  const game = Boolean(home && away)
  const [pick, setPick] = useState(league.toLowerCase())
  const lg = game ? league.toLowerCase() : pick
  const { data, failed } = useVibe(lg, game ? home : null, game ? away : null)
  const [qi, setQi] = useState(0)
  const quotes = data?.quotes ?? []
  useEffect(() => { setQi(0) }, [data])
  useEffect(() => {
    if (quotes.length < 2) return undefined
    const t = window.setInterval(() => setQi((i) => (i + 1) % quotes.length), 7000)
    return () => window.clearInterval(t)
  }, [quotes.length])
  const mood = data ? MOOD[data.label] ?? MOOD.MIXED : null
  const q = quotes[qi]
  const pos = data ? Math.round(((Math.max(-1, Math.min(1, data.vibe * 2.5)) + 1) / 2) * 100) : 50
  const heat = data ? Math.min(1, data.per_hour / 30) : 0
  const sides = (['away', 'home'] as const).map((s) => data?.split?.[s]).filter(Boolean) as NonNullable<Vibe['split']['home']>[]
  const total = sides.reduce((a, s) => a + s.mentions, 0) || 1

  const tone = `vb--${(data?.label ?? 'loading').toLowerCase()}`
  return (
    <div className={`vb-wrap${inline ? ' vb-wrap--inline' : ''} ${className}`}>
      {!inline && <button type="button" className={`vb vb--strip ${tone}`} aria-expanded={open} onClick={() => setOpen((v) => !v)} title="The crowd: public mood (click for quotes)">
        <span className="vb__kicker"><i className="vb__dot" aria-hidden="true" />THE CROWD</span>
        <span className="vb__icon" aria-hidden="true">{mood?.icon ?? '…'}</span>
        <span className="vb__word"><b>{mood?.word ?? (failed ? 'Feed resting' : 'Listening…')}</b><small>{data ? (data.posts ? `${data.per_hour}/hr · ${game ? `${away!.abbr} @ ${home!.abbr}` : pick.toUpperCase()}` : 'hardly anyone posting') : ''}</small></span>
        {q ? <span className="vb__line">“{q.text}”</span> : null}
        {data && data.controversy > 0 ? <span className="vb__ref" title="Posts arguing about the officiating">🚩{data.controversy}</span> : null}
        <span className="vb__caret" aria-hidden="true">{open ? '▴' : '▾'}</span>
      </button>}
      {open && (
    <section className={`vb vb--pop ${tone}`} aria-label="The crowd: public mood">
      <header className="vb__head">
        <span className="vb__kicker"><i className="vb__dot" aria-hidden="true" />THE CROWD</span>
        <span className="vb__scope">{game ? `${away!.abbr} @ ${home!.abbr}` : (
          <span className="vb__lgs">{LEAGUES.map((l) => <button key={l} type="button" className={l === pick ? 'is-on' : ''} onClick={() => setPick(l)}>{l.toUpperCase()}</button>)}</span>
        )}</span>
      </header>
      {!data ? (
        <p className="vb__muted" role="status">{failed ? 'The crowd feed is not answering right now. It retries every 2 minutes.' : 'Listening to the crowd…'}</p>
      ) : (
        <>
          <div className="vb__mood">
            <span className="vb__icon" aria-hidden="true">{mood!.icon}</span>
            <span className="vb__word"><b>{mood!.word}</b><small>{data.posts ? `${data.posts} posts · ${data.per_hour}/hr` : 'hardly anyone posting yet'}</small></span>
            <span className="vb__heat" title={`${data.per_hour} posts an hour`} aria-hidden="true">{[0, 1, 2, 3, 4].map((i) => <i key={i} className={heat > i / 5 ? 'is-on' : ''} />)}</span>
          </div>
          <div className="vb__gauge" title={`Mood score ${data.vibe.toFixed(2)} (−1 sour … +1 thrilled)`}>
            <span className="vb__needle" style={{ left: `${pos}%` }} />
            <small>sour</small><small>thrilled</small>
          </div>
          {game && sides.length === 2 && (
            <div className="vb__split" aria-label="Who the crowd is talking about">
              {sides.map((s, i) => (
                <span key={s.team} style={{ flex: Math.max(0.15, s.mentions / total) }} className={`vb__side vb__side--${i ? 'home' : 'away'}`}>
                  {(i ? home! : away!).abbr} {Math.round((s.mentions / total) * 100)}%
                </span>
              ))}
            </div>
          )}
          {data.controversy > 0 && <p className="vb__flag">🚩 {data.controversy} post{data.controversy === 1 ? '' : 's'} arguing about the officiating</p>}
          {q ? (
            <blockquote key={`${qi}-${q.author}`} className={`vb__quote vb__quote--${q.tone}`}>
              <p>“{q.text}”</p>
              <footer>{q.url && q.url.startsWith('https://') ? <a href={q.url} target="_blank" rel="noopener noreferrer">{q.author}</a> : q.author} · {q.source} · {ago(q.age_min)}</footer>
            </blockquote>
          ) : <p className="vb__muted">No posts worth quoting in the last 6 hours.</p>}
          {data.trending.length > 0 && <p className="vb__trend">{data.trending.slice(0, 5).map((w) => <span key={w}>#{w}</span>)}</p>}
        </>
      )}
      <footer className="vb__foot">Public posts · Bluesky + Mastodon · last 6 h · not a betting signal</footer>
    </section>
      )}
    </div>
  )
}
