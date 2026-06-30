import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import App from './app.jsx'
import { ResponsiveShell } from './ios-frame.jsx'

createRoot(document.getElementById('root')).render(
  <ResponsiveShell>
    <App />
  </ResponsiveShell>
)
