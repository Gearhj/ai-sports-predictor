/**
 * One sport's paper-research status, read from `/sports/readiness`.
 *
 * The research pages used to state from memory that the sport had no account
 * or policy. Both now exist (a virtual paper account and a paper policy), so
 * the page reads them from the same source as the Experiment page and names
 * the exact reason no assessment or position can happen yet.
 */
import { useSportReadiness, type SportReadinessEntry } from './SportReadiness'

const BLOCKER_TEXT: Record<string, string> = {
  SGO_LEAGUE_IDENTITY_UNVERIFIED: "SportsGameOdds' league identity for this sport is not verified",
  NO_CANONICAL_GAME_IDENTITY: 'AISP has no admitted schedule, so a price could not be bound to a game',
}

export function PaperStatusStrip({ sport, seasonNote }: { sport: 'NBA' | 'CFB'; seasonNote?: string }) {
  const readiness = useSportReadiness()
  const sports = (readiness.data as { sports?: SportReadinessEntry[] } | null)?.sports
  const entry = Array.isArray(sports) ? sports.find((s) => s.sport === sport) : undefined
  if (!entry) return null
  const blocker = typeof entry.price?.blocker === 'string' ? entry.price.blocker : null
  const blockers = blocker ? blocker.split(';').map((code) => BLOCKER_TEXT[code] ?? code) : []
  return (
    <section className="paper-strip" aria-label={`${entry.label} paper-research status`} data-testid={`paper-strip-${sport}`}>
      <dl>
        <div><dt>Virtual account</dt><dd>{entry.account?.funded ? `${entry.account.label} · paper only, no real money` : 'None'}</dd></div>
        <div><dt>Paper policy</dt><dd>{entry.policy_version ?? 'None'}</dd></div>
        <div><dt>Forecast eligibility</dt><dd>{entry.eligibility ? (entry.eligibility.eligible ? 'Eligible' : `Not eligible · ${entry.eligibility.reason}`) : 'Not recorded'}</dd></div>
        <div><dt>Sportsbook price</dt><dd>{entry.price?.label ?? 'Not recorded'}</dd></div>
      </dl>
      <p>
        <strong>Assessment:</strong>{' '}
        {blockers.length
          ? <>not possible yet — {blockers.join('; ')} <code>{blocker}</code>. This is AISP&rsquo;s capture state, not a finding that no value exists.</>
          : entry.pipeline.blocker ?? entry.pipeline.could_qualify ?? 'No blocker recorded.'}
        {seasonNote ? <> {seasonNote}</> : null}
      </p>
    </section>
  )
}
