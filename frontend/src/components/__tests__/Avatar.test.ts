/**
 * AGENTS.md §2.13 / req 25: the avatar renders the user's image when one is
 * set, otherwise an initials fallback — everywhere a username appears.
 *
 * `initialsOf` is pure and tested directly; the component is tested for the
 * fallback rendering (no fetch involved when `url` is null).
 */

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import { Avatar, initialsOf } from '../Avatar'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('initialsOf', () => {
  it('takes the first letter of the first and last word, uppercased', () => {
    expect(initialsOf('frank a.')).toBe('FA')
    expect(initialsOf('Alice Smith')).toBe('AS')
  })

  it('a single word yields its first letter', () => {
    expect(initialsOf('Frank')).toBe('F')
    expect(initialsOf('a')).toBe('A')
  })

  it('empty or whitespace-only names yield "?"', () => {
    expect(initialsOf('')).toBe('?')
    expect(initialsOf('   ')).toBe('?')
  })

  it('squeezes internal whitespace', () => {
    expect(initialsOf('  alice   smith  ')).toBe('AS')
  })

  it('works with non-ASCII display names', () => {
    expect(initialsOf('иван петров')).toBe('ИП')
  })
})

describe('Avatar', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    const r = root
    if (r) act(() => r.unmount())
    root = null
    container?.remove()
    container = null
  })

  function render(props: { url: string | null; name: string }): void {
    container = document.createElement('div')
    document.body.appendChild(container)
    const r = createRoot(container)
    root = r
    act(() => {
      r.render(createElement(Avatar, props))
    })
  }

  it('renders the initials fallback when no avatar url is set', () => {
    render({ url: null, name: 'Frank A.' })
    expect(container?.textContent).toBe('FA')
    const img = container?.querySelector('img')
    expect(img).toBeNull()
  })

  it('exposes the name as the accessible label on the fallback', () => {
    render({ url: null, name: 'Grace Hopper' })
    expect(container?.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe('Grace Hopper')
  })
})
