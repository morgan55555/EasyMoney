/**
 * Login / register (F2). On success the tokens are stored and the user is
 * redirected — to `?next=` when present (the invite flow), else home.
 *
 * The language switcher lives here too (top-right), so EN/RU works before
 * authentication. Validation messages are localized by mapping the zod error
 * type at render time — the schema itself carries no message strings.
 */

import { useState } from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { z } from 'zod'

import { ApiError, setTokens } from '../api/client'
import { authApi } from '../api/endpoints'
import { Button, Card, ErrorText, Field, Spinner, TextInput } from '../components/ui'
import { AppIcon } from '../components/AppIcon'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { useI18n } from '../i18n'
import { useAuthStatus } from '../query/queries'

const loginSchema = z.object({
  email: z.string().min(3).email(),
  password: z.string().min(1),
})

const registerSchema = loginSchema.extend({
  password: z.string().min(8),
  display_name: z.string().max(120).optional(),
})

type LoginForm = z.infer<typeof loginSchema>
type RegisterForm = z.infer<typeof registerSchema>

export function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = params.get('next')
  const { t, lang } = useI18n()
  const authStatus = useAuthStatus()
  // While the status is still loading we optimistically assume it is open; a
  // closed register page shows a spinner until we know either way.
  const registrationOpen = authStatus.data?.registration_open ?? true

  const login = useForm<LoginForm>({ resolver: zodResolver(loginSchema) })
  const register = useForm<RegisterForm>({ resolver: zodResolver(registerSchema) })

  const [serverError, setServerError] = useState('')

  const finish = (tokens: { access_token: string; refresh_token: string }) => {
    setTokens({ access: tokens.access_token, refresh: tokens.refresh_token })
    navigate(next ? next : '/', { replace: true })
  }

  const onLogin = login.handleSubmit(async (values) => {
    setServerError('')
    try {
      finish(await authApi.login(values))
    } catch (e) {
      setServerError(e instanceof ApiError ? e.message : t('auth.somethingWentWrong'))
    }
  })

  const onRegister = register.handleSubmit(async (values) => {
    setServerError('')
    try {
      finish(
        await authApi.register({
          email: values.email,
          password: values.password,
          display_name: values.display_name || undefined,
          // The public-page language selector becomes the new user's
          // preferred_language (§2.13).
          preferred_language: lang,
        }),
      )
    } catch (e) {
      setServerError(e instanceof ApiError ? e.message : t('auth.somethingWentWrong'))
    }
  })

  const isLogin = mode === 'login'
  const nextSuffix = next ? `?next=${encodeURIComponent(next)}` : ''

  return (
    <div className="relative min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center p-4">
      <div className="absolute right-4 top-4">
        <LanguageSwitcher />
      </div>
      <div className="w-full max-w-sm">
        <AppIcon className="h-24 w-24 mx-auto mb-4 rounded-2xl shadow-sm" />
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-50 text-center mb-1">
          Easy<span className="text-indigo-600 dark:text-indigo-400">Money</span>
        </h1>
        <p className="text-center text-sm text-gray-500 dark:text-gray-400 mb-6">{t('auth.tagline')}</p>
        <Card className="p-5 space-y-4">
          <h2 className="font-semibold text-gray-800 dark:text-gray-100">
            {isLogin ? t('auth.loginTitle') : t('auth.registerTitle')}
          </h2>
          <ErrorText>{serverError}</ErrorText>

          {isLogin ? (
            <form onSubmit={onLogin} className="space-y-3" noValidate>
              <Field label={t('auth.email')}>
                <TextInput
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  {...login.register('email')}
                />
                {login.formState.errors.email ? (
                  <span className="text-xs text-red-600 dark:text-red-400">
                    {login.formState.errors.email.type === 'too_small'
                      ? t('auth.errEmail')
                      : t('auth.errEmailInvalid')}
                  </span>
                ) : null}
              </Field>
              <Field label={t('auth.password')}>
                <TextInput
                  type="password"
                  autoComplete="current-password"
                  {...login.register('password')}
                />
                {login.formState.errors.password ? (
                  <span className="text-xs text-red-600 dark:text-red-400">{t('auth.errPassword')}</span>
                ) : null}
              </Field>
              <Button type="submit" busy={login.formState.isSubmitting} className="w-full">
                {t('auth.loginBtn')}
              </Button>
            </form>
          ) : authStatus.isPending ? (
            <div className="py-6">
              <Spinner />
            </div>
          ) : !registrationOpen ? (
            <div className="space-y-3">
              <p className="text-sm text-gray-600 dark:text-gray-300">{t('auth.registerClosed')}</p>
              <Link to={`/login${nextSuffix}`} className="block">
                <Button variant="secondary" className="w-full">
                  {t('auth.loginLink')}
                </Button>
              </Link>
            </div>
          ) : (
            <form onSubmit={onRegister} className="space-y-3" noValidate>
              <Field label={t('auth.email')}>
                <TextInput
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  {...register.register('email')}
                />
                {register.formState.errors.email ? (
                  <span className="text-xs text-red-600 dark:text-red-400">
                    {register.formState.errors.email.type === 'too_small'
                      ? t('auth.errEmail')
                      : t('auth.errEmailInvalid')}
                  </span>
                ) : null}
              </Field>
              <Field label={t('auth.password')} hint={t('auth.passwordHint')}>
                <TextInput
                  type="password"
                  autoComplete="new-password"
                  {...register.register('password')}
                />
                {register.formState.errors.password ? (
                  <span className="text-xs text-red-600 dark:text-red-400">
                    {register.formState.errors.password.type === 'too_small'
                      ? t('auth.errPasswordShort')
                      : t('auth.errPassword')}
                  </span>
                ) : null}
              </Field>
              <Field label={t('auth.displayName')} hint={t('auth.displayNameHint')}>
                <TextInput
                  autoComplete="name"
                  placeholder="Alex"
                  {...register.register('display_name')}
                />
              </Field>
              <Button type="submit" busy={register.formState.isSubmitting} className="w-full">
                {t('auth.registerBtn')}
              </Button>
            </form>
          )}

          <p className="text-sm text-gray-500 dark:text-gray-400 text-center">
            {isLogin ? (
              registrationOpen ? (
                <>
                  {t('auth.noAccount')}{' '}
                  <Link to={`/register${nextSuffix}`} className="text-indigo-600 dark:text-indigo-400 hover:underline">
                    {t('auth.registerLink')}
                  </Link>
                </>
              ) : (
                <span>{t('auth.registerClosed')}</span>
              )
            ) : (
              <>
                {t('auth.hasAccount')}{' '}
                <Link to={`/login${nextSuffix}`} className="text-indigo-600 dark:text-indigo-400 hover:underline">
                  {t('auth.loginLink')}
                </Link>
              </>
            )}
          </p>
        </Card>
      </div>
    </div>
  )
}
