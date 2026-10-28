/**
 * Settings modal (AGENTS.md §2.13, §3.6): change display name, avatar
 * (upload / clear), email (with the confirm-to-old-address flow), password
 * (with the confirmation-link flow), preferred language (the only place it
 * is changed server-side) and the UI theme (a client-only preference,
 * separate from preferred language).
 *
 * Name / language save through PATCH /me; email through POST /me/email/change
 * and password through POST /me/password/change (each returns "changed" or
 * "confirmation_sent"); avatar through the multipart /me/avatar endpoints.
 * Theme uses the ThemeManager context and is never sent to the server.
 */

import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import { useChangePassword, useClearAvatar, useChangeEmail, usePatchMe, useSetAvatar, useMe } from '../query/queries'
import { useTheme, type Theme } from '../lib/theme'
import { Avatar } from './Avatar'
import { Button, ErrorText, Field, Modal, Select, TextInput } from './ui'

type EmailMsg = { kind: 'info' | 'success' | 'error'; text: string } | null

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useMe()
  const { t } = useI18n()
  const { theme, setTheme } = useTheme()

  const patchMe = usePatchMe()
  const changeEmail = useChangeEmail()
  const changePassword = useChangePassword()
  const setAvatar = useSetAvatar()
  const clearAvatar = useClearAvatar()

  const [name, setName] = useState('')
  const [lang, setLang] = useState<'en' | 'ru'>('en')
  const [newEmail, setNewEmail] = useState('')
  const [emailMsg, setEmailMsg] = useState<EmailMsg>(null)
  const [newPassword, setNewPassword] = useState('')
  const [passwordMsg, setPasswordMsg] = useState<EmailMsg>(null)
  const [nameMsg, setNameMsg] = useState<EmailMsg>(null)
  const [avatarMsg, setAvatarMsg] = useState<EmailMsg>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const initializedRef = useRef(false)

  // Reset the transient fields each time the modal opens.
  useEffect(() => {
    if (!open) {
      initializedRef.current = false
      return
    }
    setNewEmail('')
    setEmailMsg(null)
    setNewPassword('')
    setPasswordMsg(null)
    setNameMsg(null)
    setAvatarMsg(null)
  }, [open])

  // Populate name / language once, when me is available for this open session.
  useEffect(() => {
    if (open && me.data && !initializedRef.current) {
      initializedRef.current = true
      setName(me.data.display_name)
      setLang(me.data.preferred_language === 'ru' ? 'ru' : 'en')
    }
  }, [open, me.data])

  const saveName = () => {
    if (!name.trim()) return
    patchMe.mutate(
      { display_name: name.trim() },
      {
        onSuccess: () => setNameMsg({ kind: 'success', text: t('settings.saved') }),
        onError: (e) =>
          setNameMsg({ kind: 'error', text: e instanceof ApiError ? e.message : t('settings.saveError') }),
      },
    )
  }

  const saveLang = (next: 'en' | 'ru') => {
    setLang(next)
    patchMe.mutate(
      { preferred_language: next },
      {
        onSuccess: () => setNameMsg({ kind: 'success', text: t('settings.saved') }),
        onError: (e) =>
          setNameMsg({ kind: 'error', text: e instanceof ApiError ? e.message : t('settings.saveError') }),
      },
    )
  }

  const submitEmail = () => {
    if (!newEmail.trim()) return
    changeEmail.mutate(
      newEmail.trim(),
      {
        onSuccess: (res) =>
          setEmailMsg(
            res.status === 'changed'
              ? { kind: 'success', text: t('settings.emailChanged') }
              : { kind: 'info', text: t('settings.emailConfirmSent') },
          ),
        onError: (e) =>
          setEmailMsg({ kind: 'error', text: e instanceof ApiError ? e.message : t('settings.emailError') }),
      },
    )
  }

  const submitPassword = () => {
    if (newPassword.length < 8) return
    changePassword.mutate(
      newPassword,
      {
        onSuccess: (res) =>
          setPasswordMsg(
            res.status === 'changed'
              ? { kind: 'success', text: t('settings.passwordChanged') }
              : { kind: 'info', text: t('settings.passwordConfirmSent') },
          ),
        onError: (e) =>
          setPasswordMsg({ kind: 'error', text: e instanceof ApiError ? e.message : t('settings.passwordError') }),
      },
    )
  }

  const onPickAvatar = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setAvatar.mutate(file, {
      onSuccess: () => setAvatarMsg({ kind: 'success', text: t('settings.saved') }),
      onError: (err) =>
        setAvatarMsg({ kind: 'error', text: err instanceof ApiError ? err.message : t('settings.avatarUploadError') }),
    })
    e.target.value = ''
  }

  const meData = me.data
  const themeOptions: Theme[] = ['light', 'dark', 'system']

  return (
    <Modal title={t('settings.title')} onClose={onClose}>
      <div className="space-y-6">
        {/* Profile ---------------------------------------------------------- */}
        <section className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('settings.sectionProfile')}</h3>
          <Field
            label={t('settings.displayName')}
            trailing={
              <Button onClick={saveName} busy={patchMe.isPending} disabled={!name.trim()}>
                {t('settings.save')}
              </Button>
            }
          >
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              onKeyDown={(e) => {
                if (e.key === 'Enter') saveName()
              }}
            />
          </Field>
          {nameMsg ? (
            <p
              className={`text-sm ${
                nameMsg.kind === 'success' ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
              }`}
            >
              {nameMsg.text}
            </p>
          ) : null}
        </section>

        {/* Avatar ----------------------------------------------------------- */}
        <section className="space-y-3 border-t border-gray-200 pt-4 dark:border-gray-700">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('settings.sectionAvatar')}</h3>
          <div className="flex items-center gap-4">
            <Avatar url={meData?.avatar_url ?? null} name={meData?.display_name ?? '…'} size={56} />
            <div className="space-y-2">
              <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden" onChange={onPickAvatar} />
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => fileRef.current?.click()} busy={setAvatar.isPending}>
                  {t('settings.avatarUpload')}
                </Button>
                {meData?.avatar_url ? (
                  <Button variant="danger" onClick={() => clearAvatar.mutate()} busy={clearAvatar.isPending}>
                    {t('settings.avatarRemove')}
                  </Button>
                ) : null}
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400">{t('settings.avatarHint')}</p>
            </div>
          </div>
          {avatarMsg ? (
            <p className={`text-sm ${avatarMsg.kind === 'error' ? 'text-red-600 dark:text-red-400' : 'text-emerald-700 dark:text-emerald-400'}`}>
              {avatarMsg.text}
            </p>
          ) : null}
        </section>

        {/* Account / email --------------------------------------------------- */}
        <section className="space-y-3 border-t border-gray-200 pt-4 dark:border-gray-700">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('settings.sectionAccount')}</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {t('settings.email')}: <span className="font-medium text-gray-700 dark:text-gray-200">{meData?.email ?? '…'}</span>
          </p>
          <Field
            label={t('settings.emailChange')}
            trailing={
              <Button onClick={submitEmail} busy={changeEmail.isPending} disabled={!newEmail.trim()}>
                {t('settings.emailChange')}
              </Button>
            }
          >
            <TextInput
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitEmail()
              }}
            />
          </Field>
          {emailMsg ? (
            <p
              className={`text-sm ${
                emailMsg.kind === 'error'
                  ? 'text-red-600 dark:text-red-400'
                  : emailMsg.kind === 'info'
                    ? 'text-amber-700 dark:text-amber-400'
                    : 'text-emerald-700 dark:text-emerald-400'
              }`}
            >
              {emailMsg.text}
            </p>
          ) : null}
          <Field
            label={t('settings.passwordChange')}
            hint={t('auth.passwordHint')}
            trailing={
              <Button onClick={submitPassword} busy={changePassword.isPending} disabled={newPassword.length < 8}>
                {t('settings.passwordChange')}
              </Button>
            }
          >
            <TextInput
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitPassword()
              }}
            />
          </Field>
          {passwordMsg ? (
            <p
              className={`text-sm ${
                passwordMsg.kind === 'error'
                  ? 'text-red-600 dark:text-red-400'
                  : passwordMsg.kind === 'info'
                    ? 'text-amber-700 dark:text-amber-400'
                    : 'text-emerald-700 dark:text-emerald-400'
              }`}
            >
              {passwordMsg.text}
            </p>
          ) : null}
        </section>

        {/* Appearance --------------------------------------------------------- */}
        <section className="space-y-4 border-t border-gray-200 pt-4 dark:border-gray-700">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('settings.sectionAppearance')}</h3>
          <Field label={t('settings.language')} hint={t('settings.languageHint')}>
            <Select value={lang} onChange={(e) => saveLang(e.target.value as 'en' | 'ru')}>
              <option value="en">{t('lang.en')}</option>
              <option value="ru">{t('lang.ru')}</option>
            </Select>
          </Field>
          <div className="space-y-1">
            <span className="block text-sm font-medium text-gray-700">{t('settings.theme')}</span>
            <div className="flex w-full rounded-md border border-gray-300 bg-white p-0.5 dark:border-gray-600 dark:bg-gray-800">
              {themeOptions.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => setTheme(opt)}
                  aria-pressed={theme === opt}
                  className={`flex-1 rounded px-3 py-1 text-sm font-medium transition-colors ${
                    theme === opt
                      ? 'bg-indigo-600 text-white'
                      : 'text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white'
                  }`}
                >
                  {t(`theme.${opt}`)}
                </button>
              ))}
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400">{t('settings.themeHint')}</p>
          </div>
        </section>

        <ErrorText>{nameMsg?.kind === 'error' && nameMsg.text}</ErrorText>
      </div>
    </Modal>
  )
}
