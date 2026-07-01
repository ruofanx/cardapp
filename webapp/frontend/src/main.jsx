import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import App from './app.jsx'
import { ResponsiveShell } from './ios-frame.jsx'
import PublicProfileView from './screens/PublicProfile.jsx'

const showId = new URLSearchParams(window.location.search).get('show')

createRoot(document.getElementById('root')).render(
  showId
    ? <PublicProfileView profileId={showId} />
    : <ResponsiveShell><App /></ResponsiveShell>
)
