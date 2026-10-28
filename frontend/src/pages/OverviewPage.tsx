/**
 * Overview (F10, AGENTS.md §2.2): the caller's balances across all groups,
 * one row per currency. No currency conversion — each currency is summed only
 * over the groups in that currency. Updates live via the WS bus.
 */

import { Link } from 'react-router-dom'

import { balanceClass, Card, CardHeader, EmptyState, Spinner } from '../components/ui'
import { useI18n } from '../i18n'
import { formatSigned } from '../lib/money'
import { useGroups, useOverview } from '../query/queries'

export function OverviewPage() {
  const overview = useOverview()
  const groups = useGroups()
  const { t, locale } = useI18n()

  if (overview.isPending) return <Spinner label={t('overview.loading')} />

  const rows = overview.data ?? []
  const groupCount = groups.data?.length ?? 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-50">{t('overview.title')}</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{t('overview.sub')}</p>
      </div>

      {groupCount === 0 ? (
        <Card>
          <EmptyState title={t('overview.emptyTitle')} hint={t('overview.emptyHint')} />
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState title={t('overview.noBalancesTitle')} hint={t('overview.noBalancesHint')} />
        </Card>
      ) : (
        <Card>
          <CardHeader title={t('overview.byCurrency')} />
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {rows.map((r) => (
              <li key={r.currency} className="flex items-center justify-between px-4 py-3">
                <span className="text-sm font-medium text-gray-800 dark:text-gray-100">{r.currency}</span>
                <span className={`text-base font-semibold tabular-nums ${balanceClass(r.balance)}`}>
                  {formatSigned(r.balance, r.currency, locale)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <p className="text-xs text-gray-400 dark:text-gray-500">
        <Link to="/" className="text-indigo-600 dark:text-indigo-400 hover:underline">
          {t('overview.back')}
        </Link>
      </p>
    </div>
  )
}
