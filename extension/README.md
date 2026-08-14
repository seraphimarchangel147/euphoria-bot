# Euphoria helper overlay (Chrome MV3)

A small helper that sits in a normal logged-in Chrome tab. Three jobs:

1. **Prices** — listen to the tab’s own `prices.onPriceUpdate` / WebSocket frames (and a DOM fallback). Does not open a second Euphoria socket.
2. **Session** — POST `privy-token`, `privy-id-token`, `privy-session` (and `privyUserId` if present) to `http://127.0.0.1:<port>/session` so you do not need Copy-as-cURL. If the page already produced `botSignature` / `deviceFingerprint` / `blob` on a tap you made, those are forwarded too.
3. **Overlay** — on `/trade`, show what the helper is thinking: nearby square (or no trade), confidence, start/stop. Manual mode is advisory — you tap.

## Load unpacked

1. Stay in a normal Chrome profile already logged into [euphoria.finance](https://euphoria.finance).
2. Start the local helper: `python -m src.ui` (listens on `127.0.0.1:8765`).
3. Open `chrome://extensions` → enable Developer mode → **Load unpacked** → select this `extension/` folder.
4. Pin it. Open `/trade`.

Default port is `8765`. To change it, from the service-worker console:

```js
chrome.storage.local.set({ controlPort: 8765 })
```

or set `EUPHORIA_CONTROL_PORT` on the Python side to match.
