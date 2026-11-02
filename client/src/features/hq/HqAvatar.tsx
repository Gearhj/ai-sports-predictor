/**
 * The round badge at the top right of Home and Games. It was hard-coded to the owner's initials, so every tester
 * saw "JG" (10-31). It now shows the signed-in person's initials, or "Sign in" when nobody is signed in.
 */
import { Link } from 'react-router-dom'
import { useAuthLoading, useOptionalUser } from '../../hooks/useAuth'

export function initials(name: string | null | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts.length === 1 ? parts[0].slice(0, 2) : `${parts[0][0]}${parts[parts.length - 1][0]}`).toUpperCase()
}

export function HqAvatar() {
  const user = useOptionalUser()
  const checking = useAuthLoading()
  // A stored sign-in is still being checked (sweep 11-01: "Sign in" showed for 3-8 s to a signed-in admin).
  if (!user && checking) return <span className="hq-avatar" aria-label="Checking your sign-in" title="Checking your sign-in">…</span>
  if (!user) return <Link to="/login" className="hq-avatar hq-avatar--signin" title="Sign in or create your account">Sign in</Link>
  return <Link to="/account" className="hq-avatar" title={`${user.display_name} · account`}>{initials(user.display_name)}</Link>
}
