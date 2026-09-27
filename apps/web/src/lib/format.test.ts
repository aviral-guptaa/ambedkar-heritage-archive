import { describe, expect, it } from 'vitest'
import { PAGE_UNAVAILABLE, citationLocation, formatDate, formatNumber, hostOf, titleCase } from '../lib/format'

/**
 * These are the rules that stop the interface from telling a reader something
 * the archive does not know. They are small, pure functions, which makes them
 * the cheapest place in the project to prove that honesty survives a refactor.
 */
describe('formatDate', () => {
  it('shows a year-only record as a year, not a made-up day', () => {
    expect(formatDate('1948-01-01', 'year', 1948)).toBe('1948')
    expect(formatDate('1916-11-16', 'year', 1916)).toBe('1916')
  })

  it('shows a full date when the source gives one', () => {
    expect(formatDate('1916-11-16', 'day', 1916)).toBe('16 November 1916')
  })

  it('falls back to the year when the source has no date', () => {
    expect(formatDate(null, null, 1956)).toBe('1956')
    expect(formatDate(undefined, undefined, 1956)).toBe('1956')
  })

  it('says so when no date is recorded at all', () => {
    expect(formatDate(null, null, null)).toBe('Date not recorded in the indexed source')
  })

  it('does not invent a date from a malformed value', () => {
    expect(formatDate('not-a-date', 'day', 1948)).toBe('1948')
  })
})

describe('citationLocation', () => {
  it('names the page when the source is paginated', () => {
    expect(citationLocation(12, false)).toBe('Page 12')
    expect(citationLocation(0, false)).toBe('Page 0')
  })

  it('declares a missing page rather than leaving it blank', () => {
    expect(citationLocation(null, true)).toBe(PAGE_UNAVAILABLE)
  })

  it('cannot be made to hide a missing page by passing the flag', () => {
    // The flag comes from the API and is not trustworthy input for a decision
    // about whether to explain a gap.
    expect(citationLocation(null, false)).toBe(PAGE_UNAVAILABLE)
    expect(citationLocation(undefined, false)).toBe(PAGE_UNAVAILABLE)
  })
})

describe('formatNumber', () => {
  it('groups thousands in the Indian numbering system', () => {
    expect(formatNumber(537)).toBe('537')
    expect(formatNumber(1234567)).toBe('12,34,567')
  })

  it('shows a dash rather than zero for an absent count', () => {
    expect(formatNumber(null)).toBe('—')
    expect(formatNumber(undefined)).toBe('—')
  })

  it('keeps a real zero visible', () => {
    expect(formatNumber(0)).toBe('0')
  })
})

describe('titleCase', () => {
  it('turns stored enum values into readable words', () => {
    expect(titleCase('government_primary')).toBe('Government Primary')
    expect(titleCase('unverified_secondary')).toBe('Unverified Secondary')
  })

  it('shows a dash for a missing value instead of a blank', () => {
    expect(titleCase(null)).toBe('—')
    expect(titleCase('')).toBe('—')
  })
})

describe('hostOf', () => {
  it('names the host so a reader knows whose source it is', () => {
    expect(hostOf('https://www.constitutionofindia.net/debates/')).toBe(
      'www.constitutionofindia.net',
    )
  })

  it('says when no link was recorded', () => {
    expect(hostOf(null)).toBe('no source link recorded')
  })

  it('returns a malformed value rather than throwing', () => {
    expect(hostOf('not a url')).toBe('not a url')
  })
})
