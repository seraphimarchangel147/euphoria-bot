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
3. Start `scripts/euphoria-bridge-server.py` with:
   - `EUPHORIA_BRIDGE_PORT=18901`
   - `EUPHORIA_BRIDGE_DATA=~/.legion-trading-bot/data/euphoria-bridge`
   - umask `0077`
4. Verify it binds only `127.0.0.1:18901`.
5. Before extension connection, `/euphoria/status` must report no state.
6. Unauthenticated state and command requests must return HTTP 401.

## Extension cutover

1. Copy only the committed `extension/` directory into a new SHA-named staging directory on Windows.
2. Compare every staged file hash with the committed tree.
3. Load/reload that exact unpacked directory once in Chrome.
4. Query `read_page` through the authenticated bridge and require:
   - manifest `0.4.2`;
   - content marker `0.4.2-deployment-ready`;
   - one current Euphoria tab;
   - fresh page-source price;
   - authoritative NATS grid;
   - real multipliers and break-even probabilities for 5/10/15 seconds;
   - bounded page history;
   - no content-script/service-worker error.
5. Stop the control-room loop and verify grid/multipliers continue to advance through the bridge.
6. Let a grid age beyond five seconds and verify it loses authoritative status.
7. Keep mode manual/stopped or dry-run throughout QA.

## Activation gate

Before restarting or replacing any live process, present the exact SHA, release hash, operator, affected processes, UTC checkpoint, current PID/source, and rollback hashes. An approval to build or stage is not approval to restart or trade.

## Rollback

1. Stop only the newly started bridge process.
2. Restore the prior SHA-named extension directory and reload it once.
3. Restore/start the prior bridge artifact if it was running before cutover.
4. Verify prior manifest/build marker, PID/boot UTC, bridge freshness, and `dry_run:true`.
5. Retain both release directories and the execution receipt; never roll back by copying from a mutable dirty tree.
