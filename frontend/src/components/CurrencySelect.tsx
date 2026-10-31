/**
 * Currency picker for the create-group form: a dropdown combobox whose popup
 * carries a search/filter bar *inside* the selector (not a separate input).
 * The trigger shows the picked ISO code like a Select; opening focuses the
 * filter, which narrows the list by code (case- and diacritic-insensitive,
 * via matchesAny). An outside click or Escape closes it; picking a row sets
 * the value and closes. The popup is anchored right-0 (grows leftward) so it
 * never overflows the form/card's right edge.
 */

import { useEffect, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { matchesAny } from '../lib/search'
import { IconCheck, IconChevronDown, SearchInput } from './ui'

export function CurrencySelect({
  value,
  onChange,
  currencies,
  top = null,
}: {
  value: string
  onChange: (value: string) => void
  currencies: string[]
  /** A currency to pin to the top of the list (e.g. the user's preference). */
  top?: string | null
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  // While open: reset the filter, focus it, and listen for the dismiss
  // triggers (mirrors the UserMenu outside-click / Escape idiom).
  useEffect(() => {
    if (!open) return
    setQuery('')
    searchRef.current?.focus()
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Pin the preferred currency to the top (deduping it from its normal slot),
  // then apply the filter. If `top` is not in the list it is still shown first.
  const ordered = top ? [top, ...currencies.filter((c) => c !== top)] : currencies
  const visible = ordered.filter((c) => matchesAny(query, c))

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t('groups.currency')}
        className="flex w-full items-center justify-between gap-2 rounded-md border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-sm text-gray-900 dark:text-gray-50 shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 bg-white dark:bg-gray-800"
      >
        <span className={value ? 'font-medium' : 'text-gray-400 dark:text-gray-500'}>{value || '—'}</span>
        <IconChevronDown className="h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500" />
      </button>
      {open ? (
        <div className="absolute right-0 mt-2 w-full rounded-md border border-gray-200 bg-white shadow-lg z-50 dark:border-gray-700 dark:bg-gray-900">
          <div className="p-2 border-b border-gray-200 dark:border-gray-700">
            <SearchInput
              ref={searchRef}
              value={query}
              onChange={setQuery}
              placeholder={t('groups.currencySearch')}
              clearLabel={t('common.clearSearch')}
            />
          </div>
          <ul role="listbox" className="max-h-56 overflow-y-auto py-1">
            {visible.length === 0 ? (
              <li className="px-3 py-2 text-sm text-gray-500 dark:text-gray-400">{t('groups.currencyNoMatch')}</li>
            ) : (
              visible.map((c) => {
                const selected = c === value
                return (
                  <li key={c} role="option" aria-selected={selected}>
                    <button
                      type="button"
                      onClick={() => {
                        onChange(c)
                        setOpen(false)
                      }}
                      className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-sm ${
                        selected
                          ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300'
                          : 'text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800'
                      }`}
                    >
                      <span>{c}</span>
                      {selected ? <IconCheck /> : null}
                    </button>
                  </li>
                )
              })
            )}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
