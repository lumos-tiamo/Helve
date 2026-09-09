/** The run list: the landing view, ordered newest first, with the columns you
 *  would actually sort by when something looks wrong. */

import { useEffect, useState } from "react";
import {
  compactNumber,
  Empty,
  outcomeTone,
  Panel,
  Pill,
  relativeTime,
  Tile,
} from "../components/pieces";
import { api, type RunSummary } from "../lib/api";

export function Runs({ onSelect }: { onSelect: (runId: string) => void }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .runs(100)
      .then((value) => {
        if (!cancelled) setRuns(value);
      })
      .catch((exc: Error) => {
        if (!cancelled) setError(exc.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <Empty title="Could not load runs">{error}</Empty>;
  if (runs === null) return <Empty title="Loading runs…" />;
  if (runs.length === 0) {
    return (
      <Empty title="No runs recorded yet">
        Start the shell with <code>helve</code> and ask it something; every run lands here.
      </Empty>
    );
  }

  const failed = runs.filter((run) => outcomeTone(run.outcome) === "bad").length;
  const tokens = runs.reduce((total, run) => total + run.total_tokens, 0);
  const toolCalls = runs.reduce((total, run) => total + run.tool_call_count, 0);
  const approvals = runs.reduce((total, run) => total + run.approval_required_count, 0);

  return (
    <div className="stack">
      <div className="panel">
        <div className="tiles">
          <Tile label="runs" value={runs.length} />
          <Tile label="unsuccessful" value={failed} />
          <Tile label="tool calls" value={toolCalls} />
          <Tile label="approvals" value={approvals} />
          <Tile label="tokens" value={compactNumber(tokens)} />
        </div>
      </div>

      <Panel title="Runs" note={`${runs.length} most recent`} bodyless>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>when</th>
                <th>outcome</th>
                <th>identity</th>
                <th>route</th>
                <th className="num">tools</th>
                <th className="num">approvals</th>
                <th className="num">backtracks</th>
                <th className="num">tokens</th>
                <th>run</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  key={run.run_id}
                  className="clickable"
                  onClick={() => onSelect(run.run_id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") onSelect(run.run_id);
                  }}
                  tabIndex={0}
                >
                  <td className="small" title={run.created_at}>
                    {relativeTime(run.finished_at || run.created_at)}
                  </td>
                  <td>
                    <Pill tone={outcomeTone(run.outcome)}>{run.outcome ?? "unknown"}</Pill>
                  </td>
                  <td className="mono small">{run.identity_id ?? "—"}</td>
                  <td className="mono small">{run.route_id ?? run.pipeline_id ?? "—"}</td>
                  <td className="num">{run.tool_call_count}</td>
                  <td className="num">{run.approval_required_count}</td>
                  <td className="num">{run.backtrack_count}</td>
                  <td className="num">{compactNumber(run.total_tokens)}</td>
                  <td className="mono small muted">{run.run_id.slice(0, 8)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
