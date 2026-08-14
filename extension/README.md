# Euphoria Operator Bridge (Chrome MV3)

Unpacked extension. Three jobs, nothing else:

1. **Prices** — listen to the logged-in tab's own `prices.onPriceUpdate` / WebSocket frames (and a DOM fallback). Do not open a second unauthenticated Euphoria socket.
2. **Session** — POST `privy-token`, `privy-id-token`, `privy-session` (and `privyUserId` if present) to `http://127.0.0.1:<port>/session`. If the page itself emits `botSignature` / `deviceFingerprint` / `blob` on a user gesture, forward those too. Do not mint or fake them.
3. **Overlay** — on `/trade`, show bias / confidence / one-line reason plus start/stop. Manual mode is advisory only.

This extension does **not** enable remote debugging, inject webdriver, solve Turnstile, or scrape cookies from disk.

## Load unpacked

1. Keep a normal (non-debug) Chrome profile logged into [euphoria.finance](https://euphoria.finance).
2. Start the local control room: `python -m src.ui` (listens on `127.0.0.1:8765`).
3. Open `chrome://extensions` → enable Developer mode → **Load unpacked** → select this `extension/` folder.
4. Pin the extension. Open `/trade`. The overlay should connect to the control room.

Default port is `8765`. To change it, from the service-worker console:

```js
chrome.storage.local.set({ controlPort: 8765 })
```

or set `EUPHORIA_CONTROL_PORT` on the Python side to match.
