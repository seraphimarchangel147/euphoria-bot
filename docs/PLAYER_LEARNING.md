# Manual player learning (capture + advice only)

This extension records trusted human pointer releases on the Euphoria trade canvas,
joins them to terminal `executeTrade`/settlement frames for at most 10 seconds, and
shows statistical advice. It never synthesizes a click, pointer event, trade, or
order.

## Data flow

1. `extension/inject.js` resolves a trusted canvas pointer with the live grid
   geometry and the authoritative `quotesFeed.getMultiplierForCell` snapshot.
   Unresolvable pointers are retained with `cell: null`; they are never guessed.
2. `player-capture.js` emits `player-tap`, then a joined `player-settle` carrying
   the same `tap_id`, capture-time quote, multiplier, and price history.
3. The service worker POSTs events to the localhost-only bridge at
   `/euphoria/player-event`. `scripts/euphoria-bridge-server.py` appends private
   JSONL at `~/.legion-trading-bot/data/euphoria-bridge/player-events.jsonl`.
4. `scripts/player-learning.py` creates `player-model.json`, consuming Yahcob's
   live grader ledger at `~/.legion-trading-bot/data/euphoria-grader/ledger.jsonl`
   by default. The overlay polls the bridge every 15 seconds and renders only
   proven hot contexts plus tilt advice.
5. The page-world projector emits bounded 5/10/15-second cone points from the
   last 120 seconds of ticks. The UI always shows its accuracy badge and keeps
   cone/hot-square rendering off until at least 20 grades exist and hit rate is
   strictly above 60%.
6. `scripts/player-bandit.py` normalizes grader and player rows into offline
   contextual-bandit episodes and writes `rl-episodes.jsonl` plus
   `rl-policy.json`. Its state is volatility, compression, timeframe lean, cone
   direction, and UTC hour; actions are `sit` or `tap:<side>:<distance>`.

## Offline RL / dry-run policy

```bash
python3 scripts/player-bandit.py
```

The policy uses profit-normalized rewards. Sitting in a learned +EV state is
penalized by missed EV; sitting in a non-positive-EV state earns a small
+0.01 discipline reward. Contextual UCB adds optimism to uncertain observed
actions. Every Sunday UTC is a frozen weekly holdout by default and is recorded
separately from the training dates in the artifact.

Promotion is only to grader-backed dry-run and requires all of: at least 500
training episodes, holdout EV above the think-signal baseline, and a sit rate
below 50% in +EV contexts. `live_money_authorized` is hard-coded false; this
module has no browser, network, wallet, order, or trade-submission dependency.
Stage 3 requires a separate Creator-authorized card.

## Run the bridge

```bash
python3 scripts/euphoria-bridge-server.py
```

## Regenerate the model

```bash
python3 scripts/player-learning.py
```

A 15-minute cron entry is sufficient for the v1 freshness contract:

```cron
*/15 * * * * cd /path/to/euphoria-bot && /usr/bin/python3 scripts/player-learning.py >>/tmp/euphoria-player-learning.log 2>&1
```

The learner requires at least 20 joined outcomes in the exact
`distance × side × volatility × UTC-hour-bucket` context. Before that, status is
`INSUFFICIENT`. A square is `HOT` only when its Wilson-lower-bound expected value
is positive. Tilt compares the rolling last 10 outcomes with the session's first
10 and flags a decline of at least 25 percentage points.

## Tests

```bash
node --test tests/grid-context.test.cjs tests/player-capture.test.cjs tests/player-advice.test.cjs
pytest -q
```
