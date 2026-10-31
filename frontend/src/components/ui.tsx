/**
 * Small shared UI primitives. Kept dependency-free (plain Tailwind classes).
 */

import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes, useEffect } from 'react'
import { createPortal } from 'react-dom'

// --- Spinner ------------------------------------------------------------------

/**
 * Icon-sized loading glyph, used by the page-level `Spinner` below (which
 * adds its own `py-8` for centered page loading states). Busy buttons no
 * longer show a spinner — they keep their label and animate moving 45°
 * stripes instead (`.btn-stripes` in index.css).
 */
export function SpinnerIcon() {
  return (
    <svg className="animate-spin h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 text-sm py-8 justify-center">
      <SpinnerIcon />
      {label}
    </div>
  )
}

// --- Error / empty states -------------------------------------------------------

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null
  return <div className="text-sm text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 rounded-md px-3 py-2">{children}</div>
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="text-center py-10 text-gray-500 dark:text-gray-400">
      <p className="font-medium text-gray-600 dark:text-gray-300">{title}</p>
      {hint ? <p className="text-sm mt-1">{hint}</p> : null}
    </div>
  )
}

// --- Buttons -----------------------------------------------------------------------

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'

const buttonStyles: Record<ButtonVariant, string> = {
  primary: 'bg-indigo-600 hover:bg-indigo-700 text-white border border-transparent',
  secondary:
    'bg-white hover:bg-gray-50 text-gray-800 border border-gray-300 dark:bg-gray-800 dark:hover:bg-gray-700 dark:text-gray-100 dark:border-gray-600',
  danger: 'bg-red-600 hover:bg-red-700 text-white',
  ghost: 'bg-transparent hover:bg-gray-100 text-gray-700 dark:hover:bg-gray-800 dark:text-gray-200',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  busy?: boolean
}

export function Button({ variant = 'primary', busy = false, disabled, className = '', children, ...rest }: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${buttonStyles[variant]} ${busy ? 'btn-stripes' : ''} ${className}`}
      disabled={disabled || busy}
      aria-busy={busy}
      {...rest}
    >
      {children}
    </button>
  )
}

// --- Icons ------------------------------------------------------------------------
// Inline feather-style SVGs (stroke = currentColor) so row actions can be
// icon-only without adding an icon-library dependency.

function IconSvg({ children, className = 'h-4 w-4 shrink-0' }: { children: ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export function IconTrash() {
  return (
    <IconSvg>
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </IconSvg>
  )
}

export function IconEdit() {
  return (
    <IconSvg>
      <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
    </IconSvg>
  )
}

export function IconPaperclip() {
  return (
    <IconSvg>
      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </IconSvg>
  )
}

export function IconEye() {
  return (
    <IconSvg>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </IconSvg>
  )
}

export function IconUpload() {
  return (
    <IconSvg>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </IconSvg>
  )
}

export function IconCopy() {
  return (
    <IconSvg>
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </IconSvg>
  )
}

export function IconCheck() {
  return (
    <IconSvg>
      <polyline points="20 6 9 17 4 12" />
    </IconSvg>
  )
}

export function IconLogOut({ className }: { className?: string }) {
  return (
    <IconSvg className={className}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </IconSvg>
  )
}

export function IconUserMinus() {
  return (
    <IconSvg>
      <path d="M15 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="8" cy="7" r="3.5" />
      <line x1="21.5" y1="8" x2="15.5" y2="8" />
    </IconSvg>
  )
}

export function IconSearch({ className }: { className?: string }) {
  return (
    <IconSvg className={className ?? 'h-4 w-4 shrink-0'}>
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </IconSvg>
  )
}

export function IconChevronDown({ className }: { className?: string }) {
  return (
    <IconSvg className={className ?? 'h-4 w-4 shrink-0'}>
      <polyline points="6 9 12 15 18 9" />
    </IconSvg>
  )
}

// --- Icon button -------------------------------------------------------------------

type IconButtonVariant = 'secondary' | 'danger'

const iconButtonStyles: Record<IconButtonVariant, string> = {
  secondary:
    'bg-white hover:bg-gray-50 text-gray-600 hover:text-gray-900 border border-gray-300 dark:bg-gray-800 dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100 dark:border-gray-600',
  danger:
    'bg-white hover:bg-red-50 text-red-600 hover:text-red-700 border border-red-300 dark:bg-gray-800 dark:hover:bg-red-950/40 dark:text-red-400 dark:hover:text-red-300 dark:border-red-900',
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Shown as the tooltip and the accessible name. */
  title: string
  variant?: IconButtonVariant
  busy?: boolean
}

export function IconButton({
  title,
  variant = 'secondary',
  busy = false,
  disabled,
  className = '',
  children,
  ...rest
}: IconButtonProps) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled || busy}
      className={`inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${iconButtonStyles[variant]} ${busy ? 'btn-stripes' : ''} ${className}`}
      aria-busy={busy}
      {...rest}
    >
      {children}
    </button>
  )
}

// --- Form fields -----------------------------------------------------------------

const fieldBase =
  'block w-full rounded-md border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-sm text-gray-900 dark:text-gray-50 shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 bg-white dark:bg-gray-800'

export function Field({
  label,
  children,
  hint,
  trailing,
}: {
  label: string
  children: ReactNode
  hint?: string
  /** Optional element (e.g. an action button) rendered on the same row, to the right of the control. */
  trailing?: ReactNode
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{label}</span>
      {trailing ? (
        <span className="flex items-center gap-2">
          <span className="min-w-0 flex-1">{children}</span>
          <span className="shrink-0">{trailing}</span>
        </span>
      ) : (
        children
      )}
      {hint ? <span className="block text-xs text-gray-500 dark:text-gray-400 mt-1">{hint}</span> : null}
    </label>
  )
}

/**
 * Refs are forwarded so uncontrolled form libraries (e.g. react-hook-form, which
 * reads the field value from its ref at submit time) work with these wrappers.
 * Without `forwardRef` the ref is dropped and submit reads `undefined` for every
 * field, failing validation even when the user typed valid values.
 */
export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput(props, ref) {
    return <input {...props} ref={ref} className={`${fieldBase} ${props.className ?? ''}`} />
  },
)

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select(props, ref) {
    return <select {...props} ref={ref} className={`${fieldBase} ${props.className ?? ''}`} />
  },
)

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea(props, ref) {
    return <textarea {...props} ref={ref} className={`${fieldBase} ${props.className ?? ''}`} />
  },
)

/**
 * Filter bar for lists (expenses / members / groups): a search input with a
 * magnifier icon and a clear button that appears while it has a value.
 * `placeholder` doubles as the accessible name; `clearLabel` names the ×.
 */
export const SearchInput = forwardRef<
  HTMLInputElement,
  {
    value: string
    onChange: (value: string) => void
    placeholder: string
    clearLabel?: string
    className?: string
  }
>(function SearchInput({ value, onChange, placeholder, clearLabel = 'Clear search', className = '' }, ref) {
  return (
    <div className={`relative ${className}`}>
      <IconSearch className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400 dark:text-gray-500" />
      <input
        type="search"
        value={value}
        ref={ref}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className={`${fieldBase} pl-8 [&::-webkit-search-cancel-button]:hidden ${value ? 'pr-8' : ''}`}
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label={clearLabel}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-lg leading-none text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
        >
          ×
        </button>
      ) : null}
    </div>
  )
})

// --- Badge ------------------------------------------------------------------------

export function Badge({
  children,
  tone = 'gray',
  className = '',
}: {
  children: ReactNode
  tone?: 'gray' | 'green' | 'red' | 'amber' | 'indigo'
  className?: string
}) {
  const tones: Record<string, string> = {
    gray: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
    green: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
    red: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
    amber: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
    indigo: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300',
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]} ${className}`}>{children}</span>
  )
}

// --- Card -------------------------------------------------------------------------

export function Card({
  children,
  className = '',
  id,
}: {
  children: ReactNode
  className?: string
  /** Optional DOM id (deep-link focus targets use `expense-<id>`). */
  id?: string
}) {
  return (
    <div id={id} className={`bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg shadow-sm ${className}`}>
      {children}
    </div>
  )
}

export function CardHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
      <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{title}</h2>
      {actions}
    </div>
  )
}

// --- Modal --------------------------------------------------------------------------

export function Modal({
  title,
  onClose,
  children,
  wide = false,
  closeOnBackdrop = false,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
  /** When true, a click on the dark backdrop (outside the box) closes the dialog. */
  closeOnBackdrop?: boolean
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Portaled to <body>: rendered in place, the `fixed inset-0` overlay would
  // inherit the spacing margin (margin-top) that `space-y-*` ancestors apply to
  // non-first children, offsetting the backdrop and leaving a gap at the top.
  // The box is height-capped and its *content* scrolls, so the header and the
  // footer buttons stay reachable without the whole dialog drifting.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-black/40 p-4 sm:p-8"
      onClick={() => {
        if (closeOnBackdrop) onClose()
      }}
    >
      <div
        // min-w-0: without it the box (a flex item of the centered overlay) keeps
        // its automatic min-width = the min-content width of the form inside, so
        // on narrow screens it refused to shrink below that and overflowed the
        // viewport. Now it is exactly min(viewport − padding, max-w-*).
        className={`bg-white dark:bg-gray-900 rounded-lg shadow-xl w-full min-w-0 ${wide ? 'max-w-2xl' : 'max-w-md'} max-h-full flex flex-col`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 text-xl leading-none" aria-label="Close">
            ×
          </button>
        </div>
        <div className="min-w-0 overflow-y-auto p-4">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

// --- Balance tone -------------------------------------------------------------------

export function balanceTone(value: string): 'positive' | 'negative' | 'zero' {
  const s = value.trim()
  if (s.startsWith('-')) return 'negative'
  // "0" or "0.00" / "0.000" → zero
  const numeric = Number(s.replace(/,/g, ''))
  if (!Number.isNaN(numeric) && numeric === 0) return 'zero'
  return 'positive'
}

export function balanceClass(value: string): string {
  const tone = balanceTone(value)
  if (tone === 'positive') return 'text-emerald-700 dark:text-emerald-400'
  if (tone === 'negative') return 'text-red-600 dark:text-red-400'
  return 'text-gray-500 dark:text-gray-400'
}
