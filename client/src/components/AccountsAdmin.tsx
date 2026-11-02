/**
 * The owner's accounts panel on the admin page (scale plan Phase 1, item 3): find a member, grant or revoke the
 * PREMIUM entitlement by hand. Testers and a partner's comp accounts work this way until a billing provider exists.
 */
import { useCallback, useState, type FormEvent } from 'react'
import { Search, Sparkles, UserRound } from 'lucide-react'
import { Panel, TableScroll } from './ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { AdminAccount } from '../types/domain'

export function AccountsAdmin() {
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const { data, loading, error, refresh } = useApiResource(
    useCallback((signal: AbortSignal) => endpoints.adminUsers(query, signal), [query]),
    { key: `admin/users:${query}`, staleTimeMs: 15_000 },
  )

  function search(event: FormEvent) { event.preventDefault(); setQuery(q.trim()) }

  async function toggle(account: AdminAccount) {
    const premium = account.active.includes('premium')
    setBusy(account.id); setMessage(null)
    try {
      const next = await endpoints.setEntitlement(account.id, { kind: 'premium', active: !premium })
      setMessage(`${next.email}: premium ${next.active.includes('premium') ? 'granted' : 'revoked'}.`)
      refresh()
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Could not change the entitlement.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel title="Accounts" eyebrow="MEMBERS · PREMIUM BY HAND">
      <form onSubmit={search} className="accounts-admin__search">
        <label className="visually-hidden" htmlFor="accounts-q">Find an account</label>
        <input id="accounts-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="email or name" />
        <button type="submit" className="secondary-button"><Search size={14} /> Find</button>
      </form>
      {message && <p className="callout-note" role="status">{message}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <TableScroll>
        <table className="data-table accounts-admin__table">
          <thead><tr><th>Member</th><th>Role</th><th>Email</th><th>Last sign-in</th><th>Premium</th></tr></thead>
          <tbody>
            {(data?.users ?? []).map((u) => {
              const premium = u.active.includes('premium')
              return (
                <tr key={u.id}>
                  <td><UserRound size={13} /> <b>{u.display_name}</b><br /><small className="mono">{u.email}</small></td>
                  <td>{u.role}{u.status !== 'ACTIVE' ? ` · ${u.status}` : ''}</td>
                  <td>{u.email_verified ? 'confirmed' : 'not confirmed'}</td>
                  <td>{u.last_login_at ? formatTimestamp(u.last_login_at) : 'never'}</td>
                  <td>
                    <button type="button" className={premium ? 'secondary-button danger-button' : 'primary-button'} disabled={busy === u.id || u.role === 'ADMIN'} onClick={() => toggle(u)} title={u.role === 'ADMIN' ? 'Administrators have everything' : undefined}>
                      <Sparkles size={13} /> {premium ? 'Revoke' : 'Grant'}
                    </button>
                  </td>
                </tr>
              )
            })}
            {!loading && data && data.users.length === 0 ? <tr><td colSpan={5}>No account matches.</td></tr> : null}
          </tbody>
        </table>
      </TableScroll>
      <p className="callout-note">Premium is recorded on the account and shown on its Account page. While the beta keeps every page open it changes nothing else; once gating is on, premium pages need it.</p>
    </Panel>
  )
}
