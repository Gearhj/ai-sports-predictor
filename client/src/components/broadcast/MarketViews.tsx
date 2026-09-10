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
import { apiRequest } from '../../services/api'
import { MarketReplay } from './MarketReplay'
import { RffComponents, RffHeadline, RffShareCard, bandTone } from './RffLab'
import { Panel, clockOf, pct } from './primitives'
import type {
  InformationEventsForGame, MarketTape, RffBoard,
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
  const resource = useApiResource<MarketTape>(loader)
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
  const resource = useApiResource<RffBoard>(loader)
  const board = resource.data
  const games = board?.games ?? []
  const active = games.find((game) => game.game_id === selected) ?? games[0] ?? null

  return (
    <>
      <Panel eyebrow="RFF Index — Ripe For Fuckery" title="A research measure of market unrest"
        action={<span className="bx-chip bx-chip--warn">Provisional</span>}>
        <p className="bx-muted" style={{ maxWidth: '78ch' }}>
          {board?.description
            ?? 'A research measure of market imbalance, unusual movement and unresolved information. It is not evidence that a game is manipulated.'}{' '}
          It is <strong>provisional</strong> because the component it is really about — where the
          public money is — has no source in AISP, and no price series substitutes for it. The score
          never affects a forecast, a threshold, a decision or a position.
        </p>
        {resource.loading && !board && <p className="bx-empty" role="status">Scoring the board…</p>}
        {resource.error && <p className="bx-empty" role="alert">{resource.error}</p>}
        {board && games.length === 0 && (
          <p className="bx-empty">
            No game on {board.date} has enough persisted evidence for any component to score. That
            is an absence of evidence, not a calm market.
          </p>
        )}
        {games.length > 0 && (
          <div className="bx-rff__board">
            {games.map((game) => (
              <button key={game.game_id} type="button"
                className={`bx-rff__card${active?.game_id === game.game_id ? ' is-active' : ''}`}
                style={{ ['--bx-band' as string]: bandTone(game.band.key) }}
                onClick={() => onSelect(game.game_id)}>
                <span className="bx-rff__card-head">
                  <b>{game.event.away?.abbreviation} @ {game.event.home?.abbreviation}</b>
                  <em>{game.band.label}</em>
                </span>
                <strong className="bx-num">{game.score?.toFixed(0) ?? '—'}</strong>
                <span className="bx-dim">
                  {game.score_range
                    ? `${game.score_range[0].toFixed(0)}–${game.score_range[1].toFixed(0)}`
                    : 'unscored'}
                  {' · '}{pct(game.evidence_coverage, 0)} coverage
                </span>
              </button>
            ))}
          </div>
        )}
        {board && board.unscored.length > 0 && (
          <p className="bx-dim" style={{ marginTop: 12 }}>
            {board.unscored.length} game{board.unscored.length === 1 ? '' : 's'} could not be scored
            at all: {board.unscored[0].requirement}
          </p>
        )}
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

function InformationEventPanel({ gameId, family }: { gameId: string; family: Family }) {
  const loader = useCallback((signal: AbortSignal) => apiRequest<InformationEventsForGame>(
    `/information-events/${gameId}?market=${family}`, {}, signal,
  ), [gameId, family])
  const resource = useApiResource<InformationEventsForGame>(loader)
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
