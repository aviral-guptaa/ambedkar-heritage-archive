import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { ProvenanceWarning, VerificationBadge } from './Provenance'
import { clearStoredLanguage, renderWithProviders } from '../test/providers'

/**
 * The badge is the component that decides whether text may be read as
 * Dr. Ambedkar's own words. It is tested directly because a regression here is
 * not a cosmetic bug: it would present a modern retelling as a primary source.
 */
beforeEach(clearStoredLanguage)

describe('VerificationBadge', () => {
  it('reads as verified only for verified_primary', () => {
    const { unmount } = renderWithProviders(<VerificationBadge status="verified_primary" />)
    expect(screen.getByText(/Checked against the original/)).toBeInTheDocument()
    unmount()

    renderWithProviders(<VerificationBadge status="unverified_secondary" />)
    expect(screen.getByText(/Unverified text/)).toBeInTheDocument()
  })

  it('says "not quotable" when the text is explicitly unverified', () => {
    renderWithProviders(<VerificationBadge status="unverified_secondary" quoteVerified={false} />)
    expect(screen.getByText(/Unverified — not quotable/)).toBeInTheDocument()
  })

  it('never presents an unknown status as verified', () => {
    renderWithProviders(<VerificationBadge status="something_new" />)
    expect(screen.getByText(/Unverified text/)).toBeInTheDocument()
    expect(screen.queryByText(/Checked against the original/)).not.toBeInTheDocument()
  })

  it('treats a missing status as unverified', () => {
    renderWithProviders(<VerificationBadge status={null} />)
    expect(screen.getByText(/Unverified text/)).toBeInTheDocument()
    expect(screen.getByTitle('Verification status unknown')).toBeInTheDocument()
  })

  it('labels every status the backend can return', () => {
    const cases: Array<[string, RegExp]> = [
      ['verified_primary', /Verified against the original/],
      ['unverified_secondary', /Unverified secondary text/],
      ['unverified_image', /Unverified image text/],
      ['machine_translation', /Machine translation/],
      ['pending_review', /Awaiting an archivist/],
    ]
    for (const [status, expected] of cases) {
      const { unmount } = renderWithProviders(<VerificationBadge status={status} />)
      expect(screen.getByTitle(expected)).toBeInTheDocument()
      unmount()
    }
  })

  it('uses the warning colour for anything unverified', () => {
    const { container } = renderWithProviders(<VerificationBadge status="unverified_secondary" />)
    expect(container.querySelector('.bg-amber-50')).toBeInTheDocument()

    const verified = renderWithProviders(<VerificationBadge status="verified_primary" />)
    expect(verified.container.querySelector('.bg-emerald-50')).toBeInTheDocument()
  })
})

describe('ProvenanceWarning', () => {
  it('states the warning text it is given', () => {
    renderWithProviders(<ProvenanceWarning>Not checked against the original.</ProvenanceWarning>)
    expect(screen.getByText('Not checked against the original.')).toBeInTheDocument()
  })
})
