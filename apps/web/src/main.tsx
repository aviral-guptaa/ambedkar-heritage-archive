import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { LanguageProvider } from './lib/LanguageContext'
import { registerServiceWorker } from './lib/offline'
import './index.css'

const container = document.getElementById('root')
if (!container) throw new Error('The #root element is missing from index.html.')

createRoot(container).render(
  <StrictMode>
    <LanguageProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </LanguageProvider>
  </StrictMode>,
)

registerServiceWorker()
