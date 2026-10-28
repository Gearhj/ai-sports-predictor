import { getStoredToken } from './api'

/** The Booth answers signed-in users only (security review 10-27); send the session token when there is one. */
export function boothAuth(): Record<string, string> {
  try {
    const token = getStoredToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  } catch {
    return {}
  }
}
