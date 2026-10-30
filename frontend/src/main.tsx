import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { I18nProvider } from './i18n'
import { BrowserNotifyProvider } from './lib/browserNotify'
import { ThemeProvider } from './lib/theme'
import { queryClient } from './query/queries'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserNotifyProvider>
        <QueryClientProvider client={queryClient}>
          <I18nProvider>
            <App />
          </I18nProvider>
        </QueryClientProvider>
      </BrowserNotifyProvider>
    </ThemeProvider>
  </React.StrictMode>,
)
