/**
 * RELEASE SWITCHES (owner 10-28, batch 1): features that stay built but hidden on the live site until James turns
 * them on. Owner 10-29 ("I don't like the reduced version"): ON unless the build sets the variable to "off".
 * - VITE_FEATURE_GAMES: The Arena, Player Card, XP / levels / trophies / quests and the sound pack.
 * - VITE_FEATURE_WELCOME: the beta welcome page and its BETA menu link (waits for the domain + feedback API).
 */
const on = (v: unknown) => String(v ?? 'on').toLowerCase() !== 'off'

export const GAMES_ON = on(import.meta.env.VITE_FEATURE_GAMES)
export const WELCOME_ON = on(import.meta.env.VITE_FEATURE_WELCOME)

/** Paths that exist only while the games layer is on. */
export const GAMES_PATHS = ['/arena', '/player']
