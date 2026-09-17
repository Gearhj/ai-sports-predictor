/**
 * The two views that sit beside the price board: Replay, and the RFF Lab.
 *
 * Both are views of the page you are already on rather than new destinations.
 * The sidebar has ten entries; neither of these is a place you go, it is
 * something you do to the board in front of you, and a permanent nav item for
 * a research index would give it a standing it has not earned.
 */
import { useCallback } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { apiRequest } from '../../services/api'
import { MarketReplay } from './MarketReplay'
import { RffComponents, RffHeadline, RffShareCard, bandTone } from './RffLab'
import { Panel, clockOf, pct } from './primitives'
import type {
  InformationEventsForGame, MarketTape, RffBoard, RffGame,
} from '../../types/marketIntelligence'

export type Family = 'MONEYLINE' | 'SPREAD' | 'TOTAL'

export interface ReplayChoice {
  gameId: string
  league: 'MLB' | 'NFL'
  label: string
  startsAt: string
  /** Whether the board already knows this game has a persisted quote. */
  hasQuote: boolean
}

/* -- Replay ---------------------------------------------------------------- */

export function ReplayView({
  family, games, gameId, onSelectGame, selectedBook, onSelectBook,
}: {
  family: Family
  games: ReplayChoice[]
  gameId: string | null
  onSelectGame: (id: string) => void
  selectedBook: string | null
  onSelectBook: (id: string | null) => void
}) {
  const loader = useCallback(
    async (signal: AbortSignal): Promise<MarketTape> => {
      if (!gameId) throw new Error('No game is selected.')
      return apiRequest<MarketTape>(`/markets/tape/${gameId}?market=${family}`, {}, signal)
    },
    [gameId, family],
  )
  const resource = useApiResource<MarketTape>(loader, {
    key: cacheKey('markets/tape', { gameId, family }), staleTimeMs: STALE_TIME.game,
  })
  // A tape for the previous selection is not this selection's tape; showing it
  // would put one game's movement under another game's name.
  const tape = resource.data && resource.data.game_id === gameId ? resource.data : null

  return (
    <Panel eyebrow="Market replay" title="Watch the price move"
      action={
        <label className="bx-field">Game
          <select aria-label="Replay game" value={gameId ?? ''}
            onChange={(event) => onSelectGame(event.target.value)}>
            {games.length === 0 && <option value="">No game on the board</option>}
            {games.map((choice) => (
              <option key={choice.gameId} value={choice.gameId}>
                {choice.league} · {choice.label}{choice.hasQuote ? '' : ' · no price stored'}
              </option>
            ))}
          </select>
        </label>
      }>
      {games.length === 0
        ? <p className="bx-empty">
          No game is on the board for this date and filter, so there is nothing to replay.
        </p>
        : resource.loading && !tape
          ? <p className="bx-empty" role="status">Loading the stored tape…</p>
          : resource.error
            ? <p className="bx-empty" role="alert">{resource.error}</p>
            : tape
              ? <MarketReplay tape={tape} family={family} selectedBook={selectedBook}
                onSelectBook={onSelectBook} />
              : <p className="bx-empty">Select a game to replay.</p>}
    </Panel>
  )
}

/* -- RFF Lab --------------------------------------------------------------- */

export function RffView({
  day, league, family, selected, onSelect,
}: {
  day: string
  league: 'MLB' | 'NFL' | null
  family: Family
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const loader = useCallback((signal: AbortSignal) => apiRequest<RffBoard>(
    `/markets/rff?date=${day}${league ? `&league=${league}` : ''}&market=${family}`, {}, signal,
  ), [day, league, family])
  const resource = useApiResource<RffBoard>(loader, {
    key: cacheKey('markets/rff', { day, league, family }), staleTimeMs: STALE_TIME.slate,
  })
  const board = resource.data
  const published = board?.published ?? board?.games ?? []
  const provisional = board?.provisional ?? []
  const insufficient = board?.insufficient ?? []
  const everything = [...published, ...provisional, ...insufficient]
  const active = everything.find((game) => game.game_id === selected) ?? everything[0] ?? null

  return (
    <>
      <Panel eyebrow="RFF Lens — Market reality check" title="A research measure of market unrest"
        action={<span className="bx-chip bx-chip--warn">Provisional</span>}>
        <p className="bx-muted" style={{ maxWidth: '78ch' }}>
          {board?.description
            ?? 'A research measure of market imbalance, unusual movement and unresolved information. It is not evidence that a game is manipulated.'}{' '}
          Components carry fixed weights, so a game with little evidence gets a <strong>wider
          range</strong>, never a higher score. A score is published only when enough of the
          measure is actually observed; everything else is shown as what was seen and what is
          still missing. The score never affects a forecast, a threshold, a decision or a position.
        </p>
        {resource.loading && !board && <p className="bx-empty" role="status">Reading the evidence…</p>}
        {resource.error && <p className="bx-empty" role="alert">{resource.error}</p>}

        <RffSection title="RFF scores with sufficient evidence" games={published}
          note={board?.section_notes?.published
            ?? 'Enough coverage, components and evidence families for a composite. Ranked.'}
          empty={`No game on ${board?.date ?? 'this slate'} has enough evidence for a published RFF score. That is an absence of evidence, not a calm market.`}
          selected={active?.game_id ?? null} onSelect={onSelect} kind="published" />

        <RffSection title="Provisional reads" games={provisional}
          note={board?.section_notes?.provisional
            ?? 'Half the measure or more, but not enough to publish a number. Not ranked against published games.'}
          empty="No game is in the provisional band."
          selected={active?.game_id ?? null} onSelect={onSelect} kind="provisional" />

        <RffSection title="Games needing more data" games={insufficient}
          note={board?.section_notes?.insufficient
            ?? 'Below half the measure. No score and no category: what is shown is what was observed and what is still needed.'}
          empty="Every game has at least half the measure behind it."
          selected={active?.game_id ?? null} onSelect={onSelect} kind="insufficient" />
      </Panel>

      {active && (
        <>
          <Panel><RffHeadline game={active} /></Panel>
          <div className="bx-rff__detail">
            <Panel eyebrow="Components" title="What is behind the number">
              <RffComponents game={active} />
              <p className="bx-dim" style={{ marginTop: 12 }}>
                {active.score_basis} A component with no data reads <b>Unavailable</b>; it is never
                scored zero, because zero is a finding and absence is not.
              </p>
            </Panel>
            <div style={{ display: 'grid', gap: 18, alignContent: 'start' }}>
              <RffShareCard game={active} />
              <InformationEventPanel gameId={active.game_id} family={family} />
            </div>
          </div>
        </>
      )}
    </>
  )
}

/**
 * One evidence tier. The three tiers are never merged into a single descending
 * list: a sparse game with one extreme component would sit above a fully
 * evidenced calm one, which is the misreading this page exists to prevent.
 */
function RffSection({ title, games, note, empty, selected, onSelect, kind }: {
  title: string
  games: RffGame[]
  note: string
  empty: string
  selected: string | null
  onSelect: (gameId: string) => void
  kind: 'published' | 'provisional' | 'insufficient'
}) {
  return (
    <section className={`bx-rff__section bx-rff__section--${kind}`} aria-label={title}>
      <h4 className="bx-rff__section-title">{title} <span>{games.length}</span></h4>
      <p className="bx-dim bx-rff__section-note">{note}</p>
      {games.length === 0
        ? <p className="bx-empty">{empty}</p>
        : (
          <div className="bx-rff__board">
            {games.map((game) => {
              const observed = game.contributions[0]
              return (
                <button key={game.game_id} type="button"
                  className={`bx-rff__card is-${kind}${selected === game.game_id ? ' is-active' : ''}`}
                  style={{ ['--bx-band' as string]: bandTone(game.band.key) }}
                  onClick={() => onSelect(game.game_id)}>
                  <span className="bx-rff__card-head">
                    <b>{game.event.away?.abbreviation} @ {game.event.home?.abbreviation}</b>
                    <em>{kind === 'published' ? game.band.label
                      : kind === 'provisional' ? 'Provisional' : 'Insufficient evidence'}</em>
                  </span>
                  {kind === 'published'
                    ? <strong className="bx-num">{game.score?.toFixed(0) ?? '—'}</strong>
                    : <strong className="bx-num bx-rff__known">{game.known_contribution.toFixed(0)}<small>known</small></strong>}
                  <span className="bx-dim">
                    {pct(game.evidence_coverage, 0)} coverage · {game.available_components} of{' '}
                    {game.available_components + game.unavailable_components} components
                  </span>
                  <span className="bx-dim">
                    Possible range {game.lower_bound.toFixed(0)}–{game.upper_bound.toFixed(0)}
                  </span>
                  {observed && (
                    <span className="bx-dim">Observed: {observed.label.toLowerCase()} {observed.score?.toFixed(0)}</span>
                  )}
                  {kind !== 'published' && <span className="bx-rff__unranked">Not ranked</span>}
                </button>
              )
            })}
          </div>
        )}
    </section>
  )
}

function InformationEventPanel({ gameId, family }: { gameId: string; family: Family }) {
  const loader = useCallback((signal: AbortSignal) => apiRequest<InformationEventsForGame>(
    `/information-events/${gameId}?market=${family}`, {}, signal,
  ), [gameId, family])
  const resource = useApiResource<InformationEventsForGame>(loader, {
    key: cacheKey('information-events', { gameId, family }), staleTimeMs: STALE_TIME.game,
  })
  const data = resource.data?.game_id === gameId ? resource.data : null

  return (
    <Panel eyebrow="Information events" title="What was announced, and when">
      {!data
        ? <p className="bx-dim" role="status">{resource.error ?? 'Loading information events…'}</p>
        : <>
          {data.events.length === 0
            ? <p className="bx-dim">No information event is recorded for this game.</p>
            : <div style={{ display: 'grid', gap: 10 }}>
              {data.events.map((event) => (
                <div key={event.event_id} className="bx-info-event">
                  <div className="bx-info-event__head">
                    <b>{event.headline}</b>
                    <span className="bx-chip">{event.reliability_tier}</span>
                  </div>
                  <p className="bx-dim">
                    {event.classification.replace(/_/g, ' ').toLowerCase()} · {event.source} ·
                    {' '}ingested {clockOf(event.ingested_at_utc)}
                    {event.published_at_utc
                      ? ` · published ${clockOf(event.published_at_utc)} (${event.lead_seconds}s lead)`
                      : ' · no usable publication time'}
                  </p>
                  {(event.market_before || event.market_after) && (
                    <p className="bx-dim">
                      Market home-win {pct(event.market_before?.consensus_no_vig_home, 1)} before →{' '}
                      {pct(event.market_after?.consensus_no_vig_home, 1)} after
                    </p>
                  )}
                  {event.evidence.clock_note && <p className="bx-dim">{event.evidence.clock_note}</p>}
                </div>
              ))}
            </div>}
          <details className="bx-details" style={{ marginTop: 12 }}>
            <summary>All seven classes and what each would need</summary>
            <div className="bx-details__body">
              {data.classes.map((entry) => (
                <p key={entry.key}>
                  <strong>{entry.key.replace(/_/g, ' ').toLowerCase()}</strong> —{' '}
                  {entry.state === 'COLLECTED'
                    ? `${entry.events} event(s) recorded.`
                    : entry.state === 'COLLECTED_NONE_TONIGHT'
                      ? 'Collected, but none for this game.'
                      : `Not collected. ${entry.requires}`}
                </p>
              ))}
              <p>{String(data.authority.statement ?? '')}</p>
            </div>
          </details>
        </>}
    </Panel>
  )
}
