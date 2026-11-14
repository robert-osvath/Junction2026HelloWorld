# Vimo Desktop

This Electron shell launches the local Vimo backend on port 8001 when needed,
opens the buyer cockpit in a desktop window, and grants the app microphone
permission. It does not expose Gemini or ElevenLabs keys to the renderer.

## Start

From the repository root:

```bash
cd desktop
npm install
npm start
```

The root `.env` must contain the Gemini and ElevenLabs settings used by the
FastAPI backend.

## Wake word

Electron keeps Vimo open as a desktop app and removes browser-tab friction.
For a production-grade OS-level "Hey Vimo" wake word while the app is in the
background, add a native wake-word engine (for example Porcupine) and a signed
macOS microphone entitlement. That is separate from Gemini Live and ElevenLabs.
