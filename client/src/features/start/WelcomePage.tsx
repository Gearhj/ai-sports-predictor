/**
 * BETA WELCOME (launch plan 10-28): what AISP is, the plain-English disclaimer, what is kept about you, and a
 * feedback box that goes straight to the owner (POST /api/v1/feedback, signed-in readers only).
 */
import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { AispSide } from '../flow/AispSide'
import { apiRequest, getStoredToken } from '../../services/api'
import '../slate/slate.css'
import './welcome.css'

type Kind = 'idea' | 'bug' | 'confusing' | 'love'
const KINDS: { key: Kind; label: string }[] = [
  { key: 'idea', label: 'Idea' }, { key: 'bug', label: 'Something broke' }, { key: 'confusing', label: 'Confusing' }, { key: 'love', label: 'Love this' },
]

export function FeedbackBox({ page }: { page?: string }) {
  const { pathname } = useLocation()
  const [kind, setKind] = useState<Kind>('idea')
  const [rating, setRating] = useState<number | null>(null)
  const [text, setText] = useState('')
  const [state, setState] = useState<{ status: 'idle' | 'sending' | 'sent' | 'error'; message?: string }>({ status: 'idle' })
  const signedIn = Boolean(getStoredToken())
  const send = async () => {
    setState({ status: 'sending' })
    try {
      const r = await apiRequest<{ message: string }>('/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page: page ?? pathname, kind, rating, text }),
      })
      setState({ status: 'sent', message: r.message }); setText(''); setRating(null)
    } catch (e) {
      setState({ status: 'error', message: e instanceof Error ? e.message : 'The note could not be sent. Try again in a minute.' })
    }
  }
  return (
    <section id="feedback" className="wl-fb" aria-label="Send feedback">
      <h2>Tell James what you think</h2>
      <p>Every note goes straight to him, with the page you were on. Bugs, ideas, anything confusing.</p>
      <div className="wl-fb__kinds" role="group" aria-label="What kind of note">
        {KINDS.map((k) => <button key={k.key} type="button" aria-pressed={kind === k.key} className={kind === k.key ? 'is-on' : ''} onClick={() => setKind(k.key)}>{k.label}</button>)}
      </div>
      <label htmlFor="wl-fb-text" className="wl-fb__label">Your note</label>
      <textarea id="wl-fb-text" value={text} maxLength={4000} rows={5} onChange={(e) => setText(e.target.value)} placeholder="What did you try, what happened, what would make it better?" />
      <div className="wl-fb__row">
        <span className="wl-fb__stars" role="group" aria-label="Rating, 1 to 5">
          {[1, 2, 3, 4, 5].map((n) => <button key={n} type="button" aria-label={`${n} of 5`} aria-pressed={rating === n} className={rating != null && n <= rating ? 'is-on' : ''} onClick={() => setRating(rating === n ? null : n)}>★</button>)}
        </span>
        <button type="button" className="wl-fb__send" disabled={!signedIn || text.trim().length < 3 || state.status === 'sending'} onClick={() => void send()}>
          {state.status === 'sending' ? 'Sending…' : 'Send to James'}
        </button>
      </div>
      <p className={`wl-fb__status${state.status === 'error' ? ' is-err' : state.status === 'sent' ? ' is-ok' : ''}`} role="status">
        {!signedIn ? <>Sign in to send a note. <Link to="/login">Sign in</Link></> : state.status === 'sent' || state.status === 'error' ? state.message : 'Up to 4,000 characters.'}
      </p>
    </section>
  )
}

export function WelcomePage() {
  return (
    <div className="hq wl">
      <AispSide />
      <div className="hq-main">
        <header className="sl-top">
          <div className="sl-title"><small>PRIVATE BETA</small><h1>Welcome to AISP</h1><p>Thanks for helping test it. Here is what it is, what it is not, and how to tell James what you think.</p></div>
        </header>
        <main id="main-content" className="wl-body">
          <section className="wl-card wl-what">
            <h2>What AISP is</h2>
            <p>AISP is a sports prediction research system. Its models simulate every game 10,000 times, compare their numbers with the sportsbooks, and make <b>paper</b> picks under fixed, public rules: NFL and NHL get small play-money stakes, other sports are recorded only. Every pick, pass and result is kept, including the losses.</p>
            <ul>
              <li><b>Start here</b> shows the map of every page. <Link to="/start">Open the map</Link></li>
              <li><b>Focus</b> mode (top right) is the phone-sized view of tonight’s games and your picks.</li>
              <li><b>My Picks</b> and <b>The Arena</b> are yours: your own calls, kept apart from AISP’s.</li>
            </ul>
          </section>
          <section className="wl-card wl-not" aria-label="Disclaimer">
            <h2>What it is not</h2>
            <ul>
              <li><b>No real money.</b> Every AISP stake is play money on a paper bankroll. Nothing on this site places a bet, takes a payment or connects to a sportsbook account.</li>
              <li><b>Not betting advice.</b> Model numbers are estimates and they are often wrong. Sportsbook prices are shown as observed, not as recommendations.</li>
              <li><b>A beta.</b> Pages change often, and some features are experiments. If something looks wrong, it may be; please say so below.</li>
            </ul>
            <p className="wl-help">If gambling is a problem for you or someone you know, call or text <b>1-800-GAMBLER</b>.</p>
          </section>
          <section className="wl-card wl-data" aria-label="What is kept about you">
            <h2>What is kept about you</h2>
            <ul>
              <li>Your <b>email, display name and a scrambled (hashed) password</b>, to sign you in. No one can read your password, including James.</li>
              <li>Your picks, Arena calls, model settings and player card live <b>in your own browser</b> unless a page says otherwise.</li>
              <li>Notes you send below, with the page you were on.</li>
              <li>No ads, no tracking scripts, no selling of anything to anyone.</li>
            </ul>
          </section>
          <FeedbackBox page="/welcome" />
        </main>
      </div>
    </div>
  )
}
