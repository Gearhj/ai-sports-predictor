/**
 * Sign in, create an account, and the Account page. Self-serve since the scale plan (2026-11-02): the emailed links
 * land here — /account?verify=<token> confirms an email, /login?reset=<token> opens the new-password form — and a
 * signed-in member can resend the verification, change their password, or delete the account.
 */
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { CheckCircle2, CloudUpload, KeyRound, LogOut, MailCheck, ShieldCheck, Trash2, UserRound } from 'lucide-react'
import { PageHeader, Panel } from '../components/ui'
import { useAuth } from '../hooks/useAuth'
import { endpoints } from '../services/api'
import { useUserStateStatus } from '../services/userState'

type Mode = 'login' | 'register' | 'forgot' | 'reset'

function errorText(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback
}

function SyncLine() {
  const s = useUserStateStatus()
  const when = s.at ? new Date(s.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : null
  const text = s.state === 'synced' ? `Your picks, Arena calls, XP and model follow this account · saved ${when ?? 'just now'}`
    : s.state === 'syncing' ? 'Saving your picks, Arena calls, XP and model to this account…'
    : s.state === 'error' ? `Could not save to your account (${s.detail ?? 'network'}) · this browser keeps a copy and will retry`
    : 'Your picks, Arena calls, XP and model are saved to this account when it is signed in'
  return <p className="callout-note account-sync"><CloudUpload size={14} /> {text}</p>
}

export function AuthPage() {
  const { user, loading, login, register, logout, refresh, adopt } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const params = new URLSearchParams(location.search)
  const verifyToken = params.get('verify')
  const resetToken = params.get('reset')

  const [mode, setMode] = useState<Mode>(resetToken ? 'reset' : 'login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // A verification link: confirm it whether or not the visitor is signed in, then drop the token from the URL.
  useEffect(() => {
    if (!verifyToken) return
    let dead = false
    endpoints.confirmVerification(verifyToken)
      .then(async (r) => { if (dead) return; setNotice(`${r.email} is confirmed. Thank you.`); await refresh() })
      .catch((reason) => { if (!dead) setNotice(errorText(reason, 'That link is not valid any more.')) })
      .finally(() => { if (!dead) navigate(location.pathname, { replace: true }) })
    return () => { dead = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [verifyToken])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setMessage(null)
    try {
      if (mode === 'login') await login(email, password)
      else if (mode === 'register') await register(email, password, displayName)
      else if (mode === 'forgot') {
        await endpoints.forgotPassword(email)
        setNotice(`If ${email} has an account, a reset link is on its way. It works once and expires in an hour.`)
        setMode('login')
      } else if (mode === 'reset' && resetToken) {
        adopt(await endpoints.resetPassword(resetToken, password))
        setNotice('Your password is changed and you are signed in everywhere anew.')
        navigate('/account', { replace: true })
      }
    } catch (reason) {
      setMessage(errorText(reason, 'That did not work. Try again.'))
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <div className="page auth-page"><div className="auth-loading">Checking your session…</div></div>

  if (user) return <AccountView notice={notice} onNotice={setNotice} />

  const heading = mode === 'login' ? 'Welcome back' : mode === 'register' ? 'Create your account' : mode === 'forgot' ? 'Forgot your password?' : 'Choose a new password'
  const sub = mode === 'login' ? 'Use your registered email and password.'
    : mode === 'register' ? 'Registration is by invitation during the beta; your email gets a confirmation link.'
    : mode === 'forgot' ? 'Enter the email on the account. The reset link works once and expires in an hour.'
    : 'At least 12 characters. Every other session on this account will be signed out.'
  return (
    <div className="page auth-page">
      <div className="auth-layout">
        <section className="auth-intro">
          <span className="auth-intro__mark"><KeyRound size={25} /></span>
          <p className="eyebrow">MEMBER ACCESS</p><h1>Research that shows its work.</h1><p>Sign in and your picks, Arena calls, XP and model follow you to any device. The public pages, the record and the methodology stay open to everyone.</p>
          <div className="auth-principles"><div><ShieldCheck size={16} /><span><strong>Secure credentials</strong><small>Passwords are handled by the API and never stored in this browser.</small></span></div><div><UserRound size={16} /><span><strong>Information and entertainment</strong><small>No wagers are placed and no prizes are paid, here or anywhere on the site.</small></span></div></div>
        </section>
        <section className="auth-card">
          {mode !== 'reset' ? (
            <div className="auth-tabs"><button type="button" className={mode === 'login' ? 'is-active' : ''} onClick={() => { setMode('login'); setMessage(null) }}>Sign in</button><button type="button" className={mode === 'register' ? 'is-active' : ''} onClick={() => { setMode('register'); setMessage(null) }}>Create account</button></div>
          ) : null}
          <div className="auth-card__heading"><h2>{heading}</h2><p>{sub}</p></div>
          {notice && <div className="callout-note" role="status">{notice}</div>}
          <form onSubmit={submit} className="auth-form">
            {mode === 'register' && <label><span>Display name</span><input type="text" value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" minLength={2} required placeholder="Your name" /></label>}
            {mode !== 'reset' && <label><span>Email address</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required placeholder="you@example.com" /></label>}
            {mode !== 'forgot' && <label><span>{mode !== 'reset' ? 'Password' : 'New password'}</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={mode === 'login' ? 8 : 12} required placeholder={mode === 'login' ? 'At least 8 characters' : 'At least 12 characters'} /></label>}
            {message && <div className="form-error" role="alert">{message}</div>}
            <button className="primary-button auth-submit" type="submit" disabled={submitting}>
              {submitting ? 'Please wait…' : mode === 'login' ? 'Sign in securely' : mode === 'register' ? 'Create account' : mode === 'forgot' ? 'Send the reset link' : 'Set the new password'}
            </button>
            {mode === 'login' && <button type="button" className="link-button auth-forgot" onClick={() => { setMode('forgot'); setMessage(null) }}>Forgot your password?</button>}
            {mode === 'forgot' && <button type="button" className="link-button auth-forgot" onClick={() => { setMode('login'); setMessage(null) }}>Back to sign in</button>}
          </form>
          <p className="auth-privacy">AI Sports Predictor is an information and entertainment service: probabilities, prices and a public record, not advice, not guaranteed outcomes, and never a wagering service. You must be 18 or older. By continuing you accept the <Link to="/terms">terms of use</Link> and the <Link to="/privacy">privacy policy</Link>.</p>
        </section>
      </div>
    </div>
  )
}

function AccountView({ notice, onNotice }: { notice: string | null; onNotice: (n: string | null) => void }) {
  const { user, logout, refresh, adopt } = useAuth()
  const navigate = useNavigate()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmDelete, setConfirmDelete] = useState('')
  const [busy, setBusy] = useState<'resend' | 'change' | 'delete' | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!user) return null

  async function resend() {
    setBusy('resend'); setError(null)
    try { const r = await endpoints.requestVerification(); onNotice(r.status === 'already_verified' ? 'This email is already confirmed.' : `A new confirmation link is on its way to ${user!.email}.`); await refresh() }
    catch (reason) { setError(errorText(reason, 'Could not send the link.')) }
    finally { setBusy(null) }
  }
  async function change(event: FormEvent) {
    event.preventDefault(); setBusy('change'); setError(null)
    try { adopt(await endpoints.changePassword(current, next)); setCurrent(''); setNext(''); onNotice('Password changed. Every other session on this account is signed out.') }
    catch (reason) { setError(errorText(reason, 'Could not change the password.')) }
    finally { setBusy(null) }
  }
  async function remove(event: FormEvent) {
    event.preventDefault()
    if (!window.confirm('Delete this account? Your picks, Arena calls, XP and model saved to it are removed. This cannot be undone.')) return
    setBusy('delete'); setError(null)
    try { await endpoints.deleteAccount(confirmDelete); logout(); navigate('/', { replace: true }) }
    catch (reason) { setError(errorText(reason, 'Could not delete the account.')) }
    finally { setBusy(null) }
  }

  return (
    <div className="page account-page">
      <PageHeader eyebrow="ACCOUNT" title={`Welcome, ${user.display_name}`} description="Your identity, your saved play, and the keys to this account." />
      {notice && <div className="callout-note" role="status">{notice}</div>}
      {error && <div className="form-error" role="alert">{error}</div>}
      <div className="account-grid">
        <Panel title="Profile" eyebrow="SIGNED IN">
          <div className="profile-card"><span className="profile-avatar">{user.display_name.slice(0, 2).toUpperCase()}</span><div><h2>{user.display_name}</h2><p>{user.email}</p><span className="role-chip"><ShieldCheck size={13} /> {user.role}</span></div></div>
          <dl className="account-details">
            <div><dt>Email</dt><dd>{user.email_verified ? <><MailCheck size={13} /> Confirmed</> : <>Not confirmed yet · <button type="button" className="link-button" onClick={resend} disabled={busy === 'resend'}>{busy === 'resend' ? 'Sending…' : 'send the link again'}</button></>}</dd></div>
            <div><dt>Account ID</dt><dd className="mono">{user.id}</dd></div>
            <div><dt>Access</dt><dd>{user.entitlements?.includes('premium') ? 'Premium member' : user.role === 'ADMIN' ? 'Owner' : 'Beta member'} · everything is open during the beta</dd></div>
            <div><dt>Session</dt><dd><CheckCircle2 size={13} /> Active</dd></div>
          </dl>
          <SyncLine />
          <button type="button" className="secondary-button danger-button" onClick={logout}><LogOut size={15} /> Sign out</button>
        </Panel>
        <Panel title="Change password" eyebrow="KEYS">
          <form onSubmit={change} className="auth-form">
            <label><span>Current password</span><input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" minLength={8} required /></label>
            <label><span>New password</span><input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={12} required placeholder="At least 12 characters" /></label>
            <button className="primary-button" type="submit" disabled={busy === 'change'}>{busy === 'change' ? 'Changing…' : 'Change password'}</button>
          </form>
          <p className="callout-note">Changing it signs out every other device. Forgot it? Sign out and use "Forgot your password?" on the sign-in page.</p>
        </Panel>
        <Panel title="Delete account" eyebrow="GOODBYE">
          <p>Removes your saved picks, Arena calls, XP and model from this account and disables it. The public record of AISP's own calls is unaffected: it never contained yours.</p>
          <form onSubmit={remove} className="auth-form">
            <label><span>Your password, to confirm</span><input type="password" value={confirmDelete} onChange={(e) => setConfirmDelete(e.target.value)} autoComplete="current-password" minLength={8} required /></label>
            <button className="secondary-button danger-button" type="submit" disabled={busy === 'delete'}><Trash2 size={15} /> {busy === 'delete' ? 'Deleting…' : 'Delete my account'}</button>
          </form>
        </Panel>
      </div>
    </div>
  )
}
