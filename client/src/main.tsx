import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'
import './visual.css'
import { bootApp } from './features/focus/pwa'

bootApp()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
