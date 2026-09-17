/**
 * "Next up": games whose scheduled start is still in the future.
 *
 * The comparison is between absolute instants (the ISO start with its offset
 * against the current clock), so it is correct across Eastern daylight-saving
 * changes; Eastern time is used only to label. A game's canonical status is not
 * consulted or changed: a game that has reached its start time is no longer
 * "next" even if its source status still reads SCHEDULED, and in-progress games
 * remain on the surfaces meant to show them.
 */

export function startsInFuture(startsAt: string | null | undefined, now: number): boolean {
  if (!startsAt) return false
  const start = Date.parse(startsAt)
  return Number.isFinite(start) && start > now
}

export function upcomingOnly<T extends { startsAt: string | null }>(games: T[], now: number, limit = 8): T[] {
  return games
    .filter((game) => startsInFuture(game.startsAt, now))
    .sort((a, b) => Date.parse(a.startsAt as string) - Date.parse(b.startsAt as string))
    .slice(0, limit)
}
