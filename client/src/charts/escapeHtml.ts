/**
 * ECharts tooltip formatters return HTML, so any value from data (team names, selections, labels, details) is
 * escaped before it is interpolated (security review 10-27, echarts advisory GHSA-fgmj-fm8m-jvvx).
 */
export function esc(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
