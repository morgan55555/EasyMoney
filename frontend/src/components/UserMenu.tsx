/**
 * Header user menu (AGENTS.md §2.13): an avatar chip that opens a small
 * dropdown with "Settings" and "Log out". Replaces the old standalone name
 * span + Log Out button in the Layout header. The avatar is the user's own
 * (initials fallback while the image loads or when none is set).
 */

import { useEffect, useRef, useState } from 'react'

import type { UserOut } from '../types'
import { useI18n } from '../i18n'
import { Avatar } from './Avatar'
import { IconLogOut } from './ui'

export function UserMenu({
  me,
  onSettings,
  onLogout,
}: {
  me: UserOut | undefined
  onSettings: () => void
  onLogout: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const { t } = useI18n()
  const name = me?.display_name ?? '…'

  useEffect(() => {
    if (!open) return
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

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={me?.email}
        className="flex items-center gap-2 rounded-full p-1 pr-2 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:hover:bg-gray-800"
      >
        <Avatar url={me?.avatar_url ?? null} name={name} size={32} />
        <span className="text-sm text-gray-700 hidden sm:block max-w-[8rem] truncate dark:text-gray-200">
          {name}
        </span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 mt-2 w-44 rounded-md border border-gray-200 bg-white py-1 shadow-lg z-50 dark:border-gray-700 dark:bg-gray-900"
        >
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false)
              onSettings()
            }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            {t('nav.settings')}
          </button>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false)
              onLogout()
            }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            <IconLogOut className="h-3.5 w-3.5 shrink-0" />
            {t('nav.logOut')}
          </button>
        </div>
      ) : null}
    </div>
  )
}
