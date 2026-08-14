# Authentication

This is the piece that blocked the bot. Short version: **stop chasing the
identity token, capture the refresh token instead.**

## Why the first approach stalled

The Euphoria API wants a Privy **identity token**:

```
Authorization: Bearer <identity token>
```

The obvious move — read it out of `localStorage` — fails, because:

* `localStorage` holds the **access token**, which the API rejects.
* The identity token lives in the SDK's in-memory store, so it is not in
  `localStorage` at all.
* Even when scraped from a live tab, it expires in roughly an hour. A bot built
  on a hand-copied identity token stops working before you finish lunch.

`window.__privy.getIdentityToken()` is not reliable either — the SDK is bundled
and the object is not consistently exposed on `window`.

## What actually works

Privy mints identity tokens from a **refresh token**, and the refresh token is
persistent. One capture gives the bot indefinite self-renewing auth:

```
POST https://auth.privy.io/api/v1/sessions
privy-app-id: <app id>
privy-client: react-auth:<version>
Origin: https://euphoria.finance
Content-Type: application/json

{"refresh_token": "<refresh token>"}
```

Response contains `identity_token`, `token` (access) and a **rotated**
`refresh_token`. `src/auth/privy.py` implements this, persists the rotated
token to `~/.euphoria/tokens.json` (mode 0600), and refreshes automatically
5 minutes before expiry.

Endpoint behaviour verified against the live service:

| Request | Response |
|---|---|
| no `Origin` header | `403 {"error":"Must specify origin","code":"missing_origin"}` |
| `Origin` + bogus refresh token | `400 {"error":"Missing refresh token","code":"missing_or_invalid_token"}` |

So the app-id/origin/client header triple is correct; only a genuine refresh
token is missing.

## Capturing the refresh token (once)

Do this in a browser logged into the Creator's own Euphoria account, on a
non-US IP.

1. Open `https://euphoria.finance` and log in.
2. Open DevTools → **Application** → **Storage**.
3. Look in **Cookies** for `privy-refresh-token`, and in **Local Storage** for
   a key containing `refresh` (naming varies by SDK version).
4. Copy the value into `.env`:

```
EUPHORIA_PRIVY_REFRESH_TOKEN=<value>
```

Alternative, DevTools console on the Euphoria tab:

```js
document.cookie.split('; ').find(c => c.startsWith('privy-refresh-token'))
```

Then verify without trading anything:

```bash
python3 scripts/doctor.py
```

It prints `[ ok ] Privy identity token ...` once the refresh loop works.

## Fallback: a one-off identity token

For a single short session you can paste an identity token directly:

```
EUPHORIA_PRIVY_IDENTITY_TOKEN=<token>
```

To grab one, open DevTools → **Network** on the Euphoria tab, interact with the
page, click any request to `api.mainnet.euphoria.finance`, and copy the
`Authorization` header's Bearer value. This expires in ~1h and the bot cannot
renew it — it will tell you so explicitly rather than failing obscurely.

## Security notes

* `.env`, `tokens.json` and `session.json` are gitignored; both stores are
  written 0600.
* A refresh token is a full account credential. Treat it like a password.
* Logging into Euphoria in a browser may rotate/revoke the captured token; if
  the bot reports `Privy rejected the refresh token`, capture a fresh one.
* `botSignature` / `deviceFingerprint` / `blob` are session artefacts from
  *your* browser. Treat them like credentials; do not share them.

## Live submit: pass-through of browser artefacts

Auth is solved. A live `executeTrade` still needs artefacts the frontend
produces in a real browser (see `REVERSE_ENGINEERING.md`). The bot **passes
them through** — it does not solve Cloudflare Turnstile, does not mint a
trade-key signature, and does not generate or spoof a device fingerprint.

| Artefact | Why | How the bot gets it |
|---|---|---|
| `botSignature` | signed by a registered trade key | you capture it after *you* solve Turnstile in your own Euphoria tab |
| `deviceFingerprint` | client fingerprint your browser already produced | same capture; pass-through only |
| `blob` | encoded fingerprint data (optional) | same capture, if the frontend sent it |
| `approvalPermit` | EIP-2612 USDM permit | unchanged; not part of the session store |

`EuphoriaAPI.execute_trade` still refuses to submit until `signature`,
`botSignature`, `deviceFingerprint` and `approvalPermit` are present, and
names the missing ones.

`EuphoriaTrader.prepare` / `trade` / `_main()` auto-attach whatever
artefacts are available from env or `~/.euphoria/session.json`. Env vars
win over the file. Missing keys stay missing.

```
EUPHORIA_BOT_SIGNATURE=
EUPHORIA_DEVICE_FINGERPRINT=
EUPHORIA_BLOB=
```

Or `~/.euphoria/session.json` (mode 0600):

```json
{
  "botSignature": "0x...",
  "deviceFingerprint": "...",
  "blob": "..."
}
```

Turnstile still has to be solved by you, in a real browser, on your own
logged-in session. Recapture when the server starts rejecting the values
(they are short-lived and bound to that browser challenge).

### Capture from your own Euphoria tab

Do this only in a browser where **you** are logged into **your** Euphoria
account. Complete the Turnstile challenge yourself when the page asks.

**Network panel (simplest)**

1. Open `https://euphoria.finance` and log in.
2. DevTools → **Network**, filter `executeTrade`.
3. Place a trade as you normally would — solve Turnstile in the page.
4. Open that request → Payload / Request. Copy `botSignature`,
   `deviceFingerprint`, and `blob` if present.

**DevTools console hook (optional)**

Paste this on the Euphoria tab, then place a trade. It only *reads* the
payload your tab already sends; it does not solve anything or talk to
another origin.

```js
(function captureEuphoriaSession() {
  const KEYS = ["botSignature", "deviceFingerprint", "blob"];
  function pick(obj, acc, depth) {
    if (!obj || typeof obj !== "object" || depth > 6) return acc;
    for (const k of KEYS) {
      if (typeof obj[k] === "string" && obj[k]) acc[k] = obj[k];
    }
    const kids = Array.isArray(obj) ? obj : Object.values(obj);
    for (const v of kids) pick(v, acc, depth + 1);
    return acc;
  }
  function report(src, raw) {
    let parsed = raw;
    if (typeof raw === "string") {
      try { parsed = JSON.parse(raw); } catch (e) { return; }
    }
    const found = pick(parsed, {}, 0);
    if (!found.botSignature && !found.deviceFingerprint) return;
    const text = JSON.stringify(found, null, 2);
    console.log("Euphoria session artefacts from " + src + " (pass-through only):");
    console.log(text);
    if (typeof copy === "function") copy(text);
  }
  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    if (init && init.body) report("fetch", init.body);
    return origFetch.apply(this, arguments);
  };
  const origWS = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    report("websocket", data);
    return origWS.apply(this, arguments);
  };
  const origXHR = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (body) {
    if (body) report("xhr", body);
    return origXHR.apply(this, arguments);
  };
  console.log("Hook ready. Place a trade in this tab (solve Turnstile yourself).");
})();
```

Then persist what you copied:

```bash
python3 scripts/save_session.py <<'EOF'
{
  "botSignature": "0x...",
  "deviceFingerprint": "...",
  "blob": "..."
}
EOF
```

`save_session.py` writes `~/.euphoria/session.json` mode 0600 and strips
any private-key fields if you accidentally included them. Verify with
`python3 scripts/doctor.py` — it reports whether artefacts are present
and never invents them.
