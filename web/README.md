# Helve console

The observability backend already recorded runs, traces, diagnoses and
incidents. Nothing read them. This does.

```bash
cd web && npm install
npm run build          # the server then serves it at /console
npm run dev            # or Vite on :5173, proxying /api to :8000
```

Open <http://127.0.0.1:8000/console> once the backend is up.

## What it shows

| View | The question it answers |
| --- | --- |
| Runs | What has the agent been doing, and which runs went wrong? |
| Run detail → waterfall | Where did the time go — and how much of it was waiting for a human? |
| Run detail → prompt composition | What is the prompt actually made of, layer by layer? |
| Run detail → memory retrieval | How much memory was injected, and how much was skipped? |
| Run detail → diagnosis | The engine's own account of why the run failed. |
| Health | Success rate, tool success, backtracks, open incidents. |

Approval waits are coloured separately from execution in the waterfall. A tool
that blocked for two minutes on a human is not slow code, and a chart that
conflates them sends you optimising the wrong thing.

## Auth

The API needs the bearer token from `~/.helve/auth_token`, which a browser
cannot read. Served from the backend, the token is injected into the page; run
under Vite, the console asks for it once and keeps it in `localStorage`.

Injecting it is not a weakened posture: CORS already restricts the API to
localhost origins so no other site can read a response, and any process running
as this user could read the token file directly. What the injection removes is a
paste step, not a barrier.

## Working on it without a provider

The interesting views are empty until an agent has run, and a real run costs a
provider call. Seed plausible ones instead:

```bash
uv run --project server python web/scripts/seed_demo_runs.py
uv run --project server python web/scripts/seed_demo_runs.py --clear
```

Every number it writes is invented — it is a layout fixture, not evidence. It
writes through `RunEventRecorder`, the same path the engine uses, so a change to
the storage format breaks the script instead of quietly producing runs the real
reader cannot parse.

## Two bugs this console found

Building a reader for data nobody had read turned up two real defects:

- **Every prompt-cost figure was `[REDACTED]` in every trace.** The trace store
  redacts keys matching `token|secret|password|...`, with an allowlist for the
  numeric usage counters. `token_estimate`, `token_budget`, `source_tokens`,
  `required_tokens` and `rendered_tokens` were not on it, so the entire
  prompt-budget picture was unreadable. Fixed, with a test that asks the
  producers what they emit rather than pinning today's five keys.
- **A shape mismatch blanked the page.** `/observability/health` returns one
  object; the client typed it as a list, and `.map` of undefined unmounted the
  whole tree with nothing on screen and nothing in the console. Fixed, and
  wrapped in an error boundary so the next one shows a message instead.
