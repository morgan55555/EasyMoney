/**
 * Groups list (F3): balance chips per group, create-group form, links in.
 * The list is kept live by the WS bus (any group event invalidates it).
 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { Badge, balanceClass, Button, Card, CardHeader, EmptyState, ErrorText, Field, SearchInput, Select, Spinner, TextInput } from '../components/ui'
import { useI18n, pluralForm } from '../i18n'
import { MEMBER_PLURAL } from '../i18n/locales'
import { formatSigned } from '../lib/money'
import { matchesAny } from '../lib/search'
import { useCreateGroup, useGroups } from '../query/queries'

/** Common currencies offered in the create form (any ISO 4217 is accepted). */
const CURRENCIES = [
  'EUR',
  'USD',
  'GBP',
  'CHF',
  'PLN',
  'CZK',
  'HUF',
  'SEK',
  'NOK',
  'DKK',
  'CAD',
  'AUD',
  'CNY',
  'JPY',
  'KRW',
  'VND',
  'TWD',
  'HKD',
  'SGD',
  'THB',
  'INR',
  'AED',
  'SAR',
  'KWD',
  'BHD',
  'OMR',
  'TND',
  'EGP',
  'TRY',
  'UAH',
  'RUB',
  'RON',
  'BRL',
  'MXN',
  'ZAR',
]

export function GroupsPage() {
  const groups = useGroups()
  const createGroup = useCreateGroup()
  const navigate = useNavigate()
  const { t, locale, lang } = useI18n()
  const [name, setName] = useState('')
  const [currency, setCurrency] = useState('EUR')
  const [formError, setFormError] = useState('')
  const [query, setQuery] = useState('')

  const allGroups = groups.data ?? []
  // Filter by group name and currency code.
  const visible = allGroups.filter((g) => matchesAny(query, g.name, g.currency))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setFormError('')
    try {
      const group = await createGroup.mutateAsync({ name: name.trim(), currency })
      setName('')
      navigate(`/groups/${group.id}`)
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t('groups.createError'))
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-50">{t('groups.title')}</h1>
      </div>

      <Card>
        <CardHeader title={t('groups.newGroup')} />
        <form onSubmit={(e) => void submit(e)} className="p-4 flex flex-col sm:flex-row gap-3 sm:items-end">
          <div className="flex-1">
            <Field label={t('groups.name')}>
              <TextInput
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('groups.namePlaceholder')}
                maxLength={120}
              />
            </Field>
          </div>
          <div className="w-full sm:w-32">
            <Field label={t('groups.currency')}>
              <Select value={currency} onChange={(e) => setCurrency(e.target.value)}>
                {CURRENCIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Button type="submit" busy={createGroup.isPending}>
            {t('groups.create')}
          </Button>
        </form>
        <div className="px-4 pb-3 -mt-1">
          <ErrorText>{formError}</ErrorText>
        </div>
      </Card>

      {groups.isPending ? (
        <Spinner label={t('groups.loading')} />
      ) : allGroups.length === 0 ? (
        <Card>
          <EmptyState title={t('groups.emptyTitle')} hint={t('groups.emptyHint')} />
        </Card>
      ) : (
        <div className="space-y-3">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder={t('groups.searchPlaceholder')}
            clearLabel={t('common.clearSearch')}
          />
          {visible.length === 0 ? (
            <Card>
              <EmptyState title={t('groups.noResults')} />
            </Card>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {visible.map((g) => (
                <Link key={g.id} to={`/groups/${g.id}`} className="block group">
                  <Card className="p-4 h-full hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold text-gray-900 dark:text-gray-50 truncate group-hover:text-indigo-700 dark:group-hover:text-indigo-400">{g.name}</h3>
                          <Badge tone="gray" className="shrink-0">
                            {g.currency}
                          </Badge>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                          {t('groups.members', {
                            n: g.member_count,
                            plural: pluralForm(locale, g.member_count, MEMBER_PLURAL[lang]),
                          })}
                        </p>
                      </div>
                      {g.status === 'closed' ? (
                        <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded-full px-2 py-0.5 shrink-0">
                          {t('groups.closed')}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-3 text-right">
                      <span className={`text-lg font-semibold tabular-nums ${balanceClass(g.balance)}`}>
                        {formatSigned(g.balance, g.currency, locale)}
                      </span>
                      <span className="block text-[11px] text-gray-400 dark:text-gray-500">
                        {g.balance.startsWith('-')
                          ? t('groups.balanceOwe')
                          : g.balance === '0' || Number(g.balance.replace(/,/g, '')) === 0
                            ? t('groups.balanceSettled')
                            : t('groups.balanceOwed')}
                      </span>
                    </div>
                  </Card>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

      <p className="text-xs text-gray-400 dark:text-gray-500">{t('groups.footnote')}</p>
    </div>
  )
}
