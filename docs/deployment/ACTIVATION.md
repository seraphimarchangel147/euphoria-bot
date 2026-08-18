# Euphoria deployment and rollback

This runbook activates only the read-only extension/bridge/control-room software. It does not authorize live-money trading. Keep `EUPHORIA_DRY_RUN=1`; the control room has no production submit hook.

## Immutable inputs

- Deploy from a clean, reviewed full commit SHA, never a branch name or dirty checkout.
- Build the release with `git archive <SHA>` and record the archive SHA-256.
- Keep secret-bearing state under WSL/ext4, not the Windows/NTFS checkout.
- Preserve the currently loaded unpacked extension before replacing any file.

## Preflight

1. Verify exact commit and clean tree: `git rev-parse HEAD && git status --porcelain`.
2. Run `.venv/bin/python -m pytest -q`.
3. Run `node --test tests/*.test.cjs`.
4. Run Python compile, JavaScript syntax, manifest JSON, and `git diff --check`.
5. Confirm `extension/manifest.json` reports `0.4.2` and `content.js` reports `0.4.2-deployment-ready`.
6. Confirm `EUPHORIA_DRY_RUN=1`; do not provide a submit hook.
7. Record existing `:18901` PID/source/hash and extension file hashes for rollback.

## Bridge cutover

1. Extract the immutable release under a SHA-named directory.
2. Create a release-local virtual environment and install `.[dev]` from that release.
3. Preserve the existing bridge-token file on ordinary upgrades. On first install or explicit recovery only, retrieve the token from the active extension's local storage and stream it directly on standard input to `scripts/euphoria-bridge-server.py token-rotate`. Never put the token in command arguments, environment variables, logs, screenshots, or chat. The server does not trust the first HTTP client.
4. Verify the token file is on WSL/ext4 with mode `0600` and its containing data directory is `0700`.
5. Start `scripts/euphoria-bridge-server.py` with:
   - `EUPHORIA_BRIDGE_PORT=18901`
   - `EUPHORIA_BRIDGE_DATA=~/.legion-trading-bot/data/euphoria-bridge`
   - umask `0077`
6. Verify it binds only `127.0.0.1:18901`.
7. Before extension connection, `/euphoria/status` must report no state.
8. Unauthenticated state and command requests must return HTTP 401.
9. During an explicit token rotation, require the old token to return 401 and the replacement token to succeed without restarting the bridge.

## Extension cutover

1. Copy only the committed `extension/` directory into a SHA-named staging directory on Windows.
2. Compare every staged file hash with the committed tree.
3. Back up the currently loaded extension directory, including a complete file-hash manifest.
4. Replace files inside the same currently loaded unpacked-extension path, then compare those bytes with the SHA staging directory. Do not load the SHA staging path as the live extension: changing the unpacked path changes Chrome's extension identity and storage namespace.
5. Reload the existing unpacked extension once. Preserving its path preserves its pinned token. If Chrome storage was already lost, stop and use the explicit stdin-only recovery procedure above—never re-enable trust-on-first-use.
6. Query `read_page` through the authenticated bridge and require:
   - manifest `0.4.2`;
   - content marker `0.4.2-deployment-ready`;
   - one current Euphoria tab;
   - fresh page-source price;
   - authoritative NATS grid;
   - real multipliers and break-even probabilities for 5/10/15 seconds;
   - bounded page history;
   - no content-script/service-worker error.
7. Stop the control-room loop and verify grid/multipliers continue to advance through the bridge.
8. Let a grid age beyond five seconds and verify it loses authoritative status.
9. Keep mode manual/stopped or dry-run throughout QA.

## Activation gate

Before restarting or replacing any live process, present the exact SHA, release hash, operator, affected processes, UTC checkpoint, current PID/source, and rollback hashes. An approval to build or stage is not approval to restart or trade.

## Rollback

1. Stop only the newly started bridge process.
2. Restore the backed-up extension files into the original loaded unpacked-extension path and reload it once.
3. Verify restored file hashes match the backup manifest and Chrome retained the prior extension identity/storage.
4. Restore/start the prior bridge artifact if it was running before cutover.
5. Verify prior manifest/build marker, PID/boot UTC, bridge freshness, and `dry_run:true`.
6. Retain both release directories and the execution receipt; never roll back by copying from a mutable dirty tree.
