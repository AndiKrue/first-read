import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { legalPage } from './legal'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {legalPage(window.location.pathname) ?? <App />}
  </React.StrictMode>,
)
