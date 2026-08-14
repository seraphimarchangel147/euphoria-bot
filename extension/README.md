# Euphoria helper overlay (Chrome MV3)

A small helper that sits in a normal logged-in Chrome tab. Three jobs:

1. **Prices** — listen to the tab’s own `prices.onPriceUpdate` / WebSocket frames (and a DOM fallback). Does not open a second Euphoria socket.
2. **Session** — POST `privy-token`, `privy-id-token`, `privy-session` (and `privyUserId` if present) to `http://127.0.0.1:<port>/session` so you do not need Copy-as-cURL. If the page already produced `botSignature` / `deviceFingerprint` / `blob` on a tap you made, those are forwarded too.
3. **Overlay** — on `/trade`, draw on the real canvas tiles: pink on nearby candidates, blue on the selected pick, plus looking-at, the 1m/5m/1h/4h/D/M lean, a short why, and last-window hit/miss. A small card stays for start/stop. Manual mode is an indicator — you tap. The live grid is canvas-drawn (`gridX`/`gridY`); there are no `data-cell-*` attributes.

## Load unpacked

1. Stay in a normal Chrome profile already logged into [euphoria.finance](https://euphoria.finance).
2. Start the local helper: `python -m src.ui` (listens on `127.0.0.1:8765`).
3. Open `chrome://extensions` → enable Developer mode → **Load unpacked** → select this `extension/` folder.
4. Pin it. Open `/trade`. After pulling this branch, click **Reload** on the extension card so the canvas overlay loads.

Default port is `8765`. To change it, from the service-worker console:

```js
chrome.storage.local.set({ controlPort: 8765 })
```

or set `EUPHORIA_CONTROL_PORT` on the Python side to match.
