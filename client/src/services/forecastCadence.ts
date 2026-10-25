/**
 * When each sport's forecast is (re)issued, said truthfully per sport.
 *
 * The site used to promise one rule for every sport ("refreshes about 1 h before
 * kickoff"). That was true of none of them exactly and false for College Football,
 * whose forecasts are published days ahead and re-issued only when an input changes.
 * These are the owners' actual cadences:
 *
 *   CFB  the producer re-checks inputs every 15 minutes and re-issues a forecast only
 *        when they change (otherwise the published one stands through kickoff)
 *   MLB  V18 final forecast about 2-3 hours before first pitch
 *   NFL  final pregame forecast about 1 hour before kickoff
 *   NHL  AISP's in-process Gen1 run, daily at 9:20 AM ET (re-checked 3:20 PM ET)
 */
const CADENCE: Record<string, string> = {
  CFB: 'inputs re-checked every 15 min; forecast re-issued only when inputs change',
  MLB: 'final forecast about 2–3 h before first pitch',
  NFL: 'final forecast about 1 h before kickoff',
  NHL: 'forecast run daily at 9:20 AM ET',
  NBA: 'refresh time set by the producer',
}

/** The refresh rule for one sport, or a neutral statement for an unknown one. */
export function forecastRefreshText(sport: string | null | undefined): string {
  return CADENCE[(sport ?? '').toUpperCase()] ?? 'refresh time differs by sport'
}

/** Every sport's rule in one line, for a surface that is not about one game. */
export const FORECAST_TIMING_SUMMARY = 'MLB about 2–3 h before first pitch · NFL final about 1 h before kickoff · '
  + 'NHL daily at 9:20 AM ET · College Football published days ahead, re-issued only when inputs change'
