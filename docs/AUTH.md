# Authentication

Live Euphoria API auth is **cookie-based**. A valid Privy identity JWT sent
as `Authorization: Bearer …` still returns **401**. The working client is
Python (urllib / this bot's httpx), not PowerShell.

Verified from a logged-in Chrome request (Copy as cURL → Python urllib →
200 on `users.getProfile`):

1. No `Authorization` header is sent.
2. These cookies on `api.mainnet.euphoria.finance` are what matter:
   - `privy-id-token` — identity JWT (this is the one that counts)
   - `privy-token` — access JWT
   - `privy-session` — `privy.euphoria.finance`
3. `users.getProfile` is the whoami call. Its tRPC input is **not** null:
   `{"privyUserId":"did:privy:<id>"}`.
4. `users.getTier` / `users.getGameState` are not the auth probe.

The bot never scrapes a browser. You copy the cookies (or a refresh token
that mints them).

## Capture the three Privy cookies

Do this in **your** logged-in Euphoria tab, on a non-US IP.

**From a logged-in request (simplest)**

1. Open `https://euphoria.finance` and log in.
2. DevTools → **Network**. Click any call to `api.mainnet.euphoria.finance`
   (for example `users.getProfile`).
3. Copy `privy-id-token`, `privy-token`, and `privy-session` from the
   request **Cookie** header.

**Or save Copy as cURL and extract the Cookie header**

Chrome → the same request → Copy → Copy as cURL. Pull the `-H 'Cookie: …'`
(or `--cookie`) value. Do not commit that file.

Put them in `.env` (never in git):

```
EUPHORIA_PRIVY_ID_TOKEN=
EUPHORIA_PRIVY_TOKEN=
EUPHORIA_COOKIE=
EUPHORIA_PRIVY_USER_ID=
```

`EUPHORIA_COOKIE` can be the full Cookie header from Copy as cURL. The
dedicated `EUPHORIA_PRIVY_ID_TOKEN` / `EUPHORIA_PRIVY_TOKEN` vars win over
it. `privy-session` defaults to `privy.euphoria.finance` if omitted.

You can also drop the same keys into `~/.euphoria/session.json` or
`~/.euphoria/tokens.json` (mode 0600). Env wins over the files.

`privyUserId` is `did:privy:<id>`. If you skip `EUPHORIA_PRIVY_USER_ID`,
the bot reads the `sub` claim of `privy-id-token` when it is a JWT.

Then:

```bash
python3 scripts/doctor.py
```

It reports cookie **names and lengths** (not values) and calls
`users.getProfile`. `[ ok ] users.getProfile -- authenticated` means
whoami worked.

## Refresh token (mints the cookies)

Privy still mints identity + access JWTs from a **refresh token**. The bot
keeps that flow and then sends the minted JWTs as `privy-id-token` and
`privy-token` cookies — not as Bearer.

```
POST https://auth.privy.io/api/v1/sessions
privy-app-id: <app id>
privy-client: react-auth:<version>
Origin: https://euphoria.finance
Content-Type: application/json

{"refresh_token": "<refresh token>"}
```

Response contains `identity_token`, `token` (access) and a rotated
`refresh_token`. Those are written to `~/.euphoria/tokens.json` (0600).

Capture the refresh token once from **your** tab (cookie or localStorage
key containing `refresh`), then:

```
EUPHORIA_PRIVY_REFRESH_TOKEN=
```

| Request | Response |
|---|---|
| no `Origin` header | `403 {"error":"Must specify origin","code":"missing_origin"}` |
| `Origin` + bogus refresh token | `400 {"error":"Missing refresh token","code":"missing_or_invalid_token"}` |

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
| `approvalPermit` | EIP-2612 MegaUSD permit | signed in Python with `EUPHORIA_PRIVATE_KEY` (spender = exchange, verified on-chain) |

`EuphoriaAPI.execute_trade` still refuses to submit until `signature`,
`botSignature`, `deviceFingerprint` and `approvalPermit` are present, and
names the missing ones. `prepare()` signs `approvalPermit` itself (EIP-2612
on official MegaUSD, spender = the exchange). The remaining gaps are the
browser artefacts above.

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
