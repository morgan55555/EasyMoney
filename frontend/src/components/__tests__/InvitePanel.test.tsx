/**
 * InvitePanel (AGENTS.md §2.10, §6.2): active link/email invites render with
 * their kind badge, addressed email and the copyable URL; used/revoked invites
 * drop into the "Used / revoked" section with a status badge. Creating an
 * email invite with an empty address is rejected client-side — no request is
 * sent. (Copy and revoke are deliberately not exercised: the first uses the
 * real clipboard, the second the network.)
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'

import type { InviteOut } from '../../types'
import { keys } from '../../query/queries'
import { InvitePanel } from '../InvitePanel'
import { render, clickByText, hasText, stubFetch, type RenderResult } from '../../test/harness'

const GROUP = 1

function invite(p: Partial<InviteOut> & Pick<InviteOut, 'id' | 'kind' | 'status'>): InviteOut {
  return {
    token: `tok${p.id}`,
    url: `http://localhost:8081/invite/tok${p.id}`,
    email_to: null,
    created_by: 1,
    expires_at: '2026-11-04T12:00:00Z',
    used_by: null,
    used_at: null,
    ...p,
  }
}

const seed = (qc: QueryClient): void => {
  qc.setQueryData(keys.invites(GROUP), [
    invite({ id: 1, kind: 'link', status: 'active' }),
    invite({ id: 2, kind: 'email', status: 'active', email_to: 'bob@example.com' }),
    invite({ id: 3, kind: 'link', status: 'used', used_by: 2, used_at: '2026-01-05T10:00:00Z' }),
  ])
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('InvitePanel', () => {
  it('renders the active invites with kind badges, the addressed email and the URL', () => {
    r = render(createElement(InvitePanel, { groupId: GROUP }), { seed })
    expect(hasText(r.container, 'Active invites (2)')).toBe(true)
    expect(hasText(r.container, 'link')).toBe(true)
    expect(hasText(r.container, 'email')).toBe(true)
    expect(hasText(r.container, 'bob@example.com')).toBe(true)
    // The link URL is shown for copying (in a <code> element).
    const code = Array.from(r.container.querySelectorAll('code')).find((c) =>
      c.textContent?.includes('http://localhost:8081/invite/tok1'),
    )
    expect(code).toBeTruthy()
  })

  it('moves used/revoked invites into the "Used / revoked" section with a status badge', () => {
    r = render(createElement(InvitePanel, { groupId: GROUP }), { seed })
    expect(hasText(r.container, 'Used / revoked')).toBe(true)
    expect(hasText(r.container, 'used')).toBe(true)
    // The used link invite (no email_to) shows its URL.
    expect(hasText(r.container, 'http://localhost:8081/invite/tok3')).toBe(true)
  })

  it('rejects an empty email invite client-side without sending a request', () => {
    r = render(createElement(InvitePanel, { groupId: GROUP }), { seed })
    const stub = stubFetch({})
    try {
      clickByText(r.container, 'Email invite')
      expect(hasText(r.container, 'Enter the email to invite.')).toBe(true)
      expect(stub.calls.length).toBe(0)
    } finally {
      stub.restore()
    }
  })
})
