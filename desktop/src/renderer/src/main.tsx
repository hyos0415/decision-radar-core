import './assets/main.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { CaptureApp } from './CaptureApp'

// One renderer bundle, two windows: the quick-capture window loads it with #/capture.
const isCapture = window.location.hash.startsWith('#/capture')

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isCapture ? <CaptureApp /> : <App />}</StrictMode>
)
