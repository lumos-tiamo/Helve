/** One run, reconstructed: what it did, how long each step took, what the
 *  prompt was made of, and — when it went wrong — the engine's own diagnosis. */

import { useEffect, useState } from "react";
import { Bars, compactNumber, Empty, outcomeTone, Panel, Pill, Tile } from "../components/pieces";
import {
  ApiError,
  api,
  type Diagnosis,
  type ImprovementProposal,
  type RunSummary,
  type TraceEvent,
} from "../lib/api";
import {
  buildWaterfall,
  eventHistogram,
  formatMs,
  promptComposition,
  retrievalTrace,
} from "../lib/trace";

interface Props {
  runId: string;
  onBack: () => void;
}

export function RunDetail({ runId, onBack }: Props) {
  const [run, setRun] = useState<RunSummary | null>(null);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [proposal, setProposal] = useState<ImprovementProposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const [summary, events] = await Promise.all([api.run(runId), api.trace(runId)]);
        if (cancelled) return;
        setRun(summary);
        setTrace(events);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
        return;
      } finally {
        if (!cancelled) setLoading(false);
      }

      // Diagnosis and proposal are generated per run and legitimately absent
      // for a clean one, so a 404 here is information, not a failure — it must
      // not blank the page that already loaded.
      for (const [fetchOne, apply] of [
        [() => api.diagnosis(runId), (value: unknown) => setDiagnosis(value as Diagnosis)],
        [() => api.proposal(runId), (value: unknown) => setProposal(value as ImprovementProposal)],
      ] as const) {
        try {
          const value = await fetchOne();
          if (!cancelled) apply(value);
        } catch (exc) {
          if (!(exc instanceof ApiError) || exc.status !== 404) {
            // Anything that is not "there is none" is worth surfacing.
            if (!cancelled) setError((current) => current ?? (exc as Error).message);
          }
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) return <Empty title="Loading the run…" />;
  if (error && !run) return <Empty title="Could not load this run">{error}</Empty>;
  if (!run) return <Empty title="No such run" />;

  const { spans, totalMs } = buildWaterfall(trace);
  const layers = promptComposition(trace);
  const retrieval = retrievalTrace(trace);
  const histogram = eventHistogram(trace);

  return (
    <div className="stack">
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" className="back" onClick={onBack}>
          ← all runs
        </button>
        <code className="mono small">{run.run_id}</code>
        <Pill tone={outcomeTone(run.outcome)}>{run.outcome ?? "unknown"}</Pill>
        {run.reason ? <span className="muted small">{run.reason}</span> : null}
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      <div className="panel">
        <div className="tiles">
          <Tile label="tool calls" value={run.tool_call_count} />
          <Tile label="total tokens" value={compactNumber(run.total_tokens)} />
          <Tile
            label="in / out"
            value={`${compactNumber(run.input_tokens)}/${compactNumber(run.output_tokens)}`}
          />
          <Tile label="approvals" value={run.approval_required_count} />
          <Tile label="backtracks" value={run.backtrack_count} />
          <Tile label="events" value={run.event_count} />
        </div>
      </div>

      <Panel
        title="Tool call waterfall"
        note={
          spans.length > 0 ? `${spans.length} calls · ${formatMs(totalMs)} wall clock` : undefined
        }
      >
        {spans.length === 0 ? (
          <p className="muted small">This run called no tools.</p>
        ) : (
          <>
            <div className="waterfall">
              {spans.map((span) => {
                const left = (span.startMs / totalMs) * 100;
                const width = Math.max(0.6, (span.durationMs / totalMs) * 100);
                const tone =
                  span.ok === null
                    ? "pending"
                    : span.ok === false
                      ? "bad"
                      : span.waitedForApproval
                        ? "approval"
                        : "";
                return (
                  <div className="span-row" key={`${span.name}-${span.startMs}`}>
                    <span className="span-name" title={span.detail || span.name}>
                      {span.name}
                    </span>
                    <div className="span-track">
                      <div
                        className={`span-bar ${tone}`}
                        style={{ left: `${left}%`, width: `${width}%` }}
                        title={span.detail || `${span.name} — ${formatMs(span.durationMs)}`}
                      />
                    </div>
                    <span className="span-ms">{formatMs(span.durationMs)}</span>
                  </div>
                );
              })}
            </div>
            <div className="legend">
              <span>
                <i className="swatch" style={{ background: "var(--ok)" }} /> completed
              </span>
              <span>
                <i className="swatch" style={{ background: "var(--warn)" }} /> waited for approval
              </span>
              <span>
                <i className="swatch" style={{ background: "var(--bad)" }} /> failed
              </span>
              <span>
                <i className="swatch" style={{ background: "var(--rule-strong)" }} /> never returned
              </span>
            </div>
          </>
        )}
      </Panel>

      <div className="grid-2">
        <Panel title="Prompt composition" note={layers.length > 0 ? "tokens per layer" : undefined}>
          {layers.length === 0 ? (
            <p className="muted small">
              This run recorded no prompt manifest, so its layer costs are unknown.
            </p>
          ) : (
            <Bars
              rows={layers.map((layer) => ({ label: layer.name, value: layer.tokens }))}
              format={compactNumber}
            />
          )}
        </Panel>

        <Panel title="Memory retrieval">
          {retrieval === null ? (
            <p className="muted small">No retrieval decision recorded for this run.</p>
          ) : (
            <table>
              <tbody>
                {Object.entries(retrieval).map(([key, value]) => (
                  <tr key={key}>
                    <td className="mono small muted">{key}</td>
                    <td className="mono small">
                      {Array.isArray(value) ? value.join(", ") : String(value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      {diagnosis ? (
        <Panel title="Diagnosis" note={diagnosis.primary_category ?? diagnosis.status}>
          <p style={{ marginTop: 0 }}>{diagnosis.summary}</p>
          {diagnosis.failure_node ? (
            <p className="small">
              <span className="muted">failed at </span>
              <code>{diagnosis.failure_node}</code>
            </p>
          ) : null}
          {diagnosis.evidence.length > 0 ? (
            <ul className="small">
              {diagnosis.evidence.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
          {diagnosis.recommendation ? (
            <p className="small">
              <strong>Recommendation. </strong>
              {diagnosis.recommendation}
            </p>
          ) : null}
        </Panel>
      ) : null}

      {proposal ? (
        <Panel title="Improvement proposal" note={proposal.category ?? proposal.status}>
          <p style={{ marginTop: 0 }}>
            <strong>{proposal.title}</strong>
          </p>
          <p className="small">{proposal.rationale}</p>
        </Panel>
      ) : null}

      <Panel title="Event mix" note={`${trace.length} events`}>
        <Bars rows={histogram.map((row) => ({ label: row.type, value: row.count }))} />
      </Panel>
    </div>
  );
}
