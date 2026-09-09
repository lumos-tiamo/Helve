/**
 * Turning a raw trace into the two things worth looking at: a waterfall of tool
 * calls, and what the prompt was actually made of.
 *
 * The raw event list is the wrong unit for both.  A run emits hundreds of
 * events, most of them text deltas, and scrolling that is how you fail to see
 * that one tool took nine seconds.
 */

import type { TraceEvent } from "./api";

export interface Span {
  name: string;
  startMs: number;
  durationMs: number;
  ok: boolean | null;
  /** Approval pauses are separated out: waiting on a human is not slow code. */
  waitedForApproval: boolean;
  detail: string;
}

export interface PromptLayerCost {
  name: string;
  tokens: number;
}

/** Events that carry no information a person reads, and would drown the ones that do. */
const NOISE = new Set(["text_delta", "raw_response_event", "provisional_text_delta", "thinking"]);

function parseTime(timestamp: string): number {
  const parsed = Date.parse(timestamp);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Pair tool_call_start with its result to get durations.
 *
 * Unpaired starts are kept with the duration they had when the trace ended
 * rather than dropped — a tool that never returned is the single most useful
 * thing a waterfall can show, and dropping it would render the run as if that
 * call never happened.
 */
export function buildWaterfall(events: TraceEvent[]): { spans: Span[]; totalMs: number } {
  const timed = events.filter((event) => !NOISE.has(event.type));
  if (timed.length === 0) return { spans: [], totalMs: 0 };

  const first = timed[0];
  const final = timed[timed.length - 1];
  if (!first || !final) return { spans: [], totalMs: 0 };
  const origin = parseTime(first.timestamp);
  const last = parseTime(final.timestamp);
  const totalMs = Math.max(1, last - origin);

  const open: { name: string; startMs: number; index: number; approval: boolean }[] = [];
  const spans: Span[] = [];
  let approvalPending = false;

  for (const event of timed) {
    const at = parseTime(event.timestamp) - origin;

    if (event.type === "tool_call_start") {
      open.push({
        name: asString(event.data.name) || "(unnamed tool)",
        startMs: at,
        index: spans.length,
        approval: false,
      });
      spans.push({
        name: asString(event.data.name) || "(unnamed tool)",
        startMs: at,
        durationMs: Math.max(0, totalMs - at),
        ok: null,
        waitedForApproval: false,
        detail: "no result in this trace — the call did not return",
      });
      continue;
    }

    if (event.type === "awaiting_input") {
      approvalPending = true;
      const current = open[open.length - 1];
      if (current) current.approval = true;
      continue;
    }

    if (event.type === "tool_call_result") {
      const name = asString(event.data.name);
      const index = findOpen(open, name);
      if (index < 0) continue;
      const [entry] = open.splice(index, 1);
      if (!entry) continue;
      const ok = event.data.ok === undefined ? !event.data.error : Boolean(event.data.ok);
      spans[entry.index] = {
        name: entry.name,
        startMs: entry.startMs,
        durationMs: Math.max(0, at - entry.startMs),
        ok,
        waitedForApproval: entry.approval || approvalPending,
        detail: asString(event.data.error) || asString(event.data.summary),
      };
      approvalPending = false;
    }
  }

  return { spans, totalMs };
}

function findOpen(open: { name: string }[], name: string): number {
  if (!name) return open.length - 1;
  for (let index = open.length - 1; index >= 0; index -= 1) {
    if (open[index]?.name === name) return index;
  }
  return open.length - 1;
}

/**
 * The prompt's layer composition, if the run recorded one.
 *
 * This is the view that makes memory retrieval visible: before it, the durable
 * memory layer grew without bound and nothing showed it.
 */
export function promptComposition(events: TraceEvent[]): PromptLayerCost[] {
  // The manifest is its own event type with `layers` at the top of `data`, and
  // each layer's cost is `token_estimate` — checked against a real trace, not
  // guessed.  The first version of this looked for `data.prompt_manifest.tokens`
  // and silently rendered "no manifest recorded" on every run that had one.
  for (const event of events) {
    if (event.type !== "prompt_manifest") continue;
    const layers = event.data.layers;
    if (!Array.isArray(layers)) continue;
    const costs: PromptLayerCost[] = [];
    for (const layer of layers) {
      if (!layer || typeof layer !== "object") continue;
      const record = layer as Record<string, unknown>;
      const tokens = record.token_estimate;
      // A redacted value arrives as the string "[REDACTED]"; skipping it keeps
      // an old trace from charting as a zero-cost layer.
      if (typeof tokens !== "number") continue;
      costs.push({ name: asString(record.id) || asString(record.name) || "layer", tokens });
    }
    if (costs.length > 0) return costs.sort((a, b) => b.tokens - a.tokens);
  }
  return [];
}

/** Whatever the run recorded about memory retrieval, for the detail panel. */
export function retrievalTrace(events: TraceEvent[]): Record<string, unknown> | null {
  for (const event of events) {
    const retrieval = event.data.memory_retrieval ?? event.data.retrieval;
    if (retrieval && typeof retrieval === "object") return retrieval as Record<string, unknown>;
  }
  return null;
}

export function eventHistogram(events: TraceEvent[]): { type: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const event of events) {
    counts.set(event.type, (counts.get(event.type) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count);
}

export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
}
