/** Formatting helpers. Kept in one place so dates read the same on every page. */

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

/** The phrase the interface shows instead of inventing a page number. */
export const PAGE_UNAVAILABLE = 'Page information unavailable in indexed source.'

/**
 * Render a document's date honestly.
 *
 * A record may be known only to the year, or may have a full date. Showing
 * "1 January 1916" for a year-only record would invent a day, so the precision
 * of the source record decides what is printed.
 */
export function formatDate(
  iso: string | null | undefined,
  precision: string | null | undefined,
  year?: number | null,
): string {
  if (iso) {
    const date = new Date(`${iso}T00:00:00Z`)
    if (!Number.isNaN(date.getTime())) {
      if (precision === 'year') return String(date.getUTCFullYear())
      return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`
    }
  }
  if (year) return String(year)
  return 'Date not recorded in the indexed source'
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('en-IN').format(value)
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return '—'
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

/**
 * Refer to a citation's location.
 *
 * When the source has no pagination there is no page to name, and the interface
 * says so instead of pointing at page 1.
 */
export function citationLocation(
  pageNumber: number | null | undefined,
  unavailable: boolean,
): string {
  if (pageNumber !== null && pageNumber !== undefined) return `Page ${pageNumber}`
  // Whether or not the caller flagged it, a missing page is reported as
  // missing. The flag is accepted for callers that pass the API's field
  // straight through, but it can never suppress the explanation.
  void unavailable
  return PAGE_UNAVAILABLE
}

export function hostOf(url: string | null | undefined): string {
  if (!url) return 'no source link recorded'
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}
