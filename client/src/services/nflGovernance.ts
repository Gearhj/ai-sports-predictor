/**
 * Which NFL games a verified production handoff actually governs.
 *
 * Governance is not a property of the forecast row. The forecast bundles still
 * carry the producer's own `RESEARCH_SHADOW` / `NOT_PROMOTED` lineage, which is
 * metadata about how the model was built. What governs a game is the pinned
 * handoff plus the clock: a handoff declares `effective_from_utc`, and every
 * kickoff at or after that instant is governed by it. A kickoff before it is
 * not, and must keep reading as research — relabelling it would be a claim
 * about evidence that did not exist when the game was forecast.
 *
 * The status endpoint is fail-closed on the server: when no handoff is pinned,
 * or the bytes do not hash to the pin, `production_label_allowed` is false and
 * every game here reads as research.
 */
import { apiRequest } from './api'

export const NFL_PRODUCTION_BASELINE = 'NFL_M1_PRODUCTION_BASELINE_PROSPECTIVE_MONITORING'

export interface NflProductionStatus {
  status: string
  accepted: boolean
  fixture: boolean
  production_label_allowed: boolean
  handoff_sha256: string | null
  identity?: {
    model_id?: string
    configuration_id?: string
    model_artifact_sha256?: string
    draw_count?: number
    effective_from_utc?: string
    calibration_authority?: { minimumSettledGamesForPublishedCalibration?: number; status?: string }
  } | null
}

export function loadNflProductionStatus(signal?: AbortSignal): Promise<NflProductionStatus> {
  return apiRequest<NflProductionStatus>('/research/nfl-production-status', {}, signal)
}

/**
 * How many NFL games have actually been scored against an official final.
 * This is the sample the disclosure quotes, so it is read rather than assumed.
 */
export async function loadNflScoredCount(
  season: number, week: number, signal?: AbortSignal,
): Promise<number | null> {
  const card = await apiRequest<{ coverage?: { settled_and_scored?: number } }>(
    `/research/nfl-scorecard?season=${season}&week=${week}`, {}, signal)
  const scored = card?.coverage?.settled_and_scored
  return typeof scored === 'number' ? scored : null
}

/** True only for a kickoff the pinned handoff actually governs. */
export function isGoverned(status: NflProductionStatus | null | undefined, kickoffUtc: string | null): boolean {
  if (!status?.production_label_allowed || status.status !== NFL_PRODUCTION_BASELINE) return false
  if (status.fixture) return false
  const effective = status.identity?.effective_from_utc
  if (!effective || !kickoffUtc) return false
  const kickoff = Date.parse(kickoffUtc)
  const from = Date.parse(effective)
  if (Number.isNaN(kickoff) || Number.isNaN(from)) return false
  return kickoff >= from
}

/** The short badge on a game card. Plain language, never the raw status code. */
export function governanceBadge(governed: boolean): { label: string; title: string } {
  return governed
    ? {
      label: 'Production baseline',
      title: 'Governed by the verified NFL M1 production handoff · prospective monitoring. '
        + 'Monitoring only: nothing here is proven, validated for profitability or calibrated.',
    }
    : {
      label: 'Research only',
      title: 'This kickoff precedes the production handoff’s effective time, so it stays a '
        + 'research-shadow forecast and is not relabelled.',
    }
}

/** The section-level chip for a whole NFL slate. */
export function slateBadge(status: NflProductionStatus | null | undefined, anyGoverned: boolean): string {
  if (!status?.production_label_allowed) return 'Research shadow'
  return anyGoverned ? 'NFL M1 production baseline' : 'Research shadow'
}

/**
 * The standing disclosure. It states the sample honestly and never claims the
 * model is proven, validated for profitability, optimised or calibrated.
 */
export function monitoringNote(status: NflProductionStatus | null | undefined, scoredGames: number | null): string {
  if (!status?.production_label_allowed) {
    return 'NFL M1 research forecasts. A virtual paper position is recorded only under the NFL '
      + 'paper policy, with producer eligibility and a fresh price inside the decision window.'
  }
  const scored = scoredGames == null
    ? 'Scoring has not started'
    : `${scoredGames} game${scoredGames === 1 ? '' : 's'} scored so far — too early to establish performance`
  const calibration = status.identity?.calibration_authority?.minimumSettledGamesForPublishedCalibration
  return `NFL M1 production baseline · prospective monitoring. ${scored}. `
    + 'Forecasts are recorded before kickoff and scored afterwards against the official final. '
    + `The model is not calibrated${calibration ? ` (calibration review at ${calibration} settled games)` : ''}, `
    + 'and nothing here is a claim that it is profitable.'
}
