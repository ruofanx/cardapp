import type { CapacitorConfig } from '@capacitor/cli'

const PROD_URL = process.env.POKECOLLECT_API_URL || 'https://pokecollect.up.railway.app'

const config: CapacitorConfig = {
  appId: 'com.pokecollect.app',
  appName: 'PokeCollect',
  // webDir is required by Capacitor but server.url takes precedence at runtime.
  // Run `npm run build` before `npx cap sync` to keep it up to date.
  webDir: '../static/dist',
  server: {
    // Loads the app directly from Railway — web changes deploy instantly
    // without requiring an App Store update.
    url: PROD_URL,
    cleartext: false,
    androidScheme: 'https',
  },
  ios: {
    contentInset: 'always',
  },
  plugins: {
    SplashScreen: {
      launchAutoHide: true,
      backgroundColor: '#111214',
      showSpinner: false,
    },
  },
}

export default config
