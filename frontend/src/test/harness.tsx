/**
 * Minimal component-test harness (AGENTS.md §5.6 / §6.2).
 *
 * The project has no `@testing-library/react`, so this hand-rolls the tiny
 * slice of render/assert we need on top of React 18's `createRoot` + `act`
 * (the same approach as `Avatar.test.ts`). Every test:
 *   - uses a FRESH `QueryClient` (no cross-test cache leakage) and seeds the
 *     query keys a component reads with `setQueryData`, so the data is present
 *     and *fresh* (30s staleTime) — no `queryFn` ever fires, no network;
 *   - renders into a `div` appended to `document.body` (modals portal there
 *     too, so `body` is exposed for asserting portal content);
 *   - is unmounted + removed in `afterEach`.
 *
 * i18n: components call `useI18n()`, which defaults to EN outside a provider.
 * Pass `lang: 'ru'` to wrap the tree in an `I18nProvider` seeded from a RU
 * browser language, so Russian strings/word-order are exercised.
 */

import { act, createElement, type ReactElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { I18nProvider } from '../i18n'
import { ThemeProvider } from '../lib/theme'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

export interface RenderOptions {
  /** Seed the fresh query client's cache before the first render. */
  seed?: (qc: QueryClient) => void
  /** Wrap in `I18nProvider` with a RU browser language (default EN). */
  lang?: 'en' | 'ru'
  /** Wrap in `ThemeProvider` (components that call `useTheme`). */
  theme?: boolean
}

export interface RenderResult {
  /** The element the tree rendered into (modals are NOT here). */
  container: HTMLElement
  /** `document.body` — modals `createPortal` here. */
  body: HTMLElement
  queryClient: QueryClient
  unmount: () => void
}

export function render(ui: ReactElement, opts: RenderOptions = {}): RenderResult {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false },
    },
  })
  opts.seed?.(qc)

  const el = document.createElement('div')
  document.body.appendChild(el)
  const root: Root = createRoot(el)

  let prevLang: string | undefined
  if (opts.lang === 'ru') {
    prevLang = (navigator as { language?: string }).language
    Object.defineProperty(navigator, 'language', { value: 'ru-RU', configurable: true })
  }

  let node: ReactElement = createElement(QueryClientProvider, { client: qc }, ui)
  if (opts.lang === 'ru') node = createElement(I18nProvider, null, node)
  if (opts.theme) node = createElement(ThemeProvider, null, node)

  act(() => {
    root.render(node)
  })

  return {
    container: el,
    body: document.body,
    queryClient: qc,
    unmount: () => {
      act(() => root.unmount())
      el.remove()
      if (opts.lang === 'ru') {
        Object.defineProperty(navigator, 'language', { value: prevLang ?? 'en', configurable: true })
      }
    },
  }
}

// --- DOM helpers -------------------------------------------------------------------

// The framework's `value` setters live on the prototype descriptor; calling the
// extracted function as a plain function would run it with `this === undefined`
// (strict mode), so it must be invoked with `.call(el, value)`.
const nativeInputSetter: ((this: HTMLInputElement, value: string) => void) | undefined =
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
const nativeSelectSetter: ((this: HTMLSelectElement, value: string) => void) | undefined =
  Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set

/** Set a controlled `<input>` value the way a user types it (fires React onChange). */
export function typeInput(input: HTMLInputElement, value: string): void {
  act(() => {
    if (nativeInputSetter) nativeInputSetter.call(input, value)
    else input.value = value
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

/** Set a controlled `<select>` value the way a user picks an option (fires React onChange). */
export function selectValue(select: HTMLSelectElement, value: string): void {
  act(() => {
    if (nativeSelectSetter) nativeSelectSetter.call(select, value)
    else select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

/** Find the `<select>` inside the `<label>` whose first text span is `label`. */
export function labeledSelect(root: ParentNode, label: string): HTMLSelectElement {
  const labels = Array.from(root.querySelectorAll('label'))
  const hit = labels.find((l) => (l.querySelector('span')?.textContent ?? '').trim() === label)
  const select = hit?.querySelector('select') as HTMLSelectElement | null
  if (!select) throw new Error(`labeledSelect: no select under label "${label}"`)
  return select
}

/** Click an element. */
export function click(el: Element): void {
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

/**
 * Let in-flight work settle before asserting: the timer yields to the
 * microtask queue (stubbed fetch promise → onSuccess → setState, including
 * the query-invalidation refetch), then `act` flushes the resulting React
 * work. Call it after triggering a mutation; repeat if the next state only
 * appears after a refetch (the badge-clear case in NotificationsBell.test).
 */
export async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms))
  })
}

export interface FetchStub {
  /** Every request that hit the stub, in order. */
  calls: Array<{ url: string; init?: RequestInit }>
  /** Restore the original `globalThis.fetch`. */
  restore: () => void
}

/**
 * Replace `globalThis.fetch` with a stub that routes by URL substring
 * (checked in insertion order — register the most specific fragment first,
 * e.g. `/api/notifications/read` before `/api/notifications`). Every hit
 * returns 200 with the given JSON body; a miss returns 404. `apiFetch`
 * (client.ts) only reads `.ok`, `.status` and `.json()`.
 */
export function stubFetch(routes: Record<string, unknown>): FetchStub {
  const real = globalThis.fetch
  const calls: FetchStub['calls'] = []
  const fake = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    for (const [frag, body] of Object.entries(routes)) {
      if (url.includes(frag)) {
        return { ok: true, status: 200, json: async () => body } as unknown as Response
      }
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({ detail: `no stub for ${url}` }),
    } as unknown as Response
  }) as unknown as typeof fetch
  globalThis.fetch = fake
  return { calls, restore: () => { globalThis.fetch = real } }
}

/** Click an element (a button, etc.) by its accessible text content. */
export function clickByText(root: ParentNode, text: string): void {
  const el = Array.from(root.querySelectorAll('button, a, [role="button"]')).find((b) =>
    (b.textContent ?? '').trim().includes(text),
  )
  if (!el) throw new Error(`clickByText: no button/link with text "${text}"`)
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

/** Find the `<input>` inside the `<label>` whose first text span is `label`. */
export function labeledInput(root: ParentNode, label: string): HTMLInputElement {
  const labels = Array.from(root.querySelectorAll('label'))
  const hit = labels.find((l) => (l.querySelector('span')?.textContent ?? '').trim() === label)
  const input = hit?.querySelector('input') as HTMLInputElement | null
  if (!input) throw new Error(`labeledInput: no input under label "${label}"`)
  return input
}

/**
 * Find the `<button>` inside the `<label>` whose first text span is `label`.
 * Used when several fields share the same button label (e.g. three "Save"
 * buttons) and the one next to a specific field must be targeted.
 */
export function labeledButton(root: ParentNode, label: string): HTMLButtonElement {
  const labels = Array.from(root.querySelectorAll('label'))
  const hit = labels.find((l) => (l.querySelector('span')?.textContent ?? '').trim() === label)
  const btn = hit?.querySelector('button') as HTMLButtonElement | null
  if (!btn) throw new Error(`labeledButton: no button under label "${label}"`)
  return btn
}

/** All button/link elements whose text contains `text`. */
export function buttonsWithText(root: ParentNode, text: string): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>('button, a, [role="button"]')).filter((b) =>
    (b.textContent ?? '').trim().includes(text),
  )
}

/** The `disabled` state of the first button/link whose text contains `text` (null when absent). */
export function buttonDisabled(root: ParentNode, text: string): boolean | null {
  const el = buttonsWithText(root, text)[0]
  return el ? (el as HTMLButtonElement).disabled : null
}

/** True when any element's text content contains `text`. */
export function hasText(root: ParentNode, text: string): boolean {
  return (root.textContent ?? '').includes(text)
}

/** How many times `text` occurs in the root's full text content. */
export function countText(root: ParentNode, text: string): number {
  const full = root.textContent ?? ''
  if (!text) return 0
  let count = 0
  for (let i = full.indexOf(text); i >= 0; i = full.indexOf(text, i + text.length)) count++
  return count
}
