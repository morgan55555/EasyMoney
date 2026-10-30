/**
 * PushDeepLink (AGENTS.md §2.14): the in-page half of a notification click.
 * The service worker routes a click with `Client.postMessage` (which reaches
 * controlled AND uncontrolled clients, unlike `Client.navigate`); this test
 * feeds the exact message the worker posts and asserts the react-router
 * navigation happens — and that off-shape, external, or cross-origin messages
 * are ignored.
 */

import { act, createElement } from 'react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { PushDeepLink } from '../PushDeepLink'
import { render, flush } from '../../test/harness'

function withProbe(initial: string) {
  let loc = ''
  const Probe = () => {
    const l = useLocation()
    loc = l.pathname + l.search
    return null
  }
  const res = render(
    createElement(
      MemoryRouter,
      { initialEntries: [initial] },
      createElement(PushDeepLink),
      createElement(Probe),
    ),
  )
  return { res, getLoc: () => loc }
}

/** Post the exact message the service worker sends on a notification click. */
function postNav(url: string, origin?: string): void {
  act(() => {
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'em:nav', url },
        origin,
      }),
    )
  })
}

describe('PushDeepLink', () => {
  it('navigates to the posted target when the worker sends a deep link', async () => {
    const { res, getLoc } = withProbe('/')
    await flush()
    postNav('/groups/5?tab=expenses&focus=expense:12', window.location.origin)
    await flush()
    expect(getLoc()).toBe('/groups/5?tab=expenses&focus=expense:12')
    res.unmount()
  })

  it('navigates even when the posted message carries no explicit origin (some browsers)', async () => {
    const { res, getLoc } = withProbe('/overview')
    await flush()
    postNav('/groups/9?tab=activity&focus=feed:expense.deleted', '')
    await flush()
    expect(getLoc()).toBe('/groups/9?tab=activity&focus=feed:expense.deleted')
    res.unmount()
  })

  it('ignores a message that is not the deep-link shape', async () => {
    const { res, getLoc } = withProbe('/')
    await flush()
    act(() => {
      window.dispatchEvent(new MessageEvent('message', { data: { type: 'other', url: '/x' } }))
    })
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })

  it('ignores a deep link whose url is not a same-origin relative path', async () => {
    const { res, getLoc } = withProbe('/')
    await flush()
    postNav('https://evil.example/x', window.location.origin)
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })

  it('ignores a deep link posted from a different origin', async () => {
    const { res, getLoc } = withProbe('/')
    await flush()
    postNav('/groups/5?tab=expenses&focus=expense:12', 'https://evil.example')
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })
})
