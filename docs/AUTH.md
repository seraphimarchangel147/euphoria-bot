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

* `.env` and `tokens.json` are gitignored; the token store is written 0600.
* A refresh token is a full account credential. Treat it like a password.
* Logging into Euphoria in a browser may rotate/revoke the captured token; if
  the bot reports `Privy rejected the refresh token`, capture a fresh one.

## Still browser-bound

Auth is solved. Submitting a live order still needs three artefacts the
frontend generates (see `REVERSE_ENGINEERING.md`):

| Artefact | Why | Path forward |
|---|---|---|
| `botSignature` | signed by a registered trade key | registration needs a Cloudflare Turnstile token — genuinely browser-only |
| `deviceFingerprint` | client fingerprint | spoofable once a real sample is captured |
| `approvalPermit` | EIP-2612 USDM permit | implementable in Python, no browser needed |

`EuphoriaAPI.execute_trade` refuses to submit until all three are present and
names the missing ones, so this fails loudly instead of as a server rejection.
