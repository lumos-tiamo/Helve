/** Agent health and open incidents — the "is anything wrong right now" view. */

import { useEffect, useState } from "react";
import { Bars, Empty, Panel, Pill, relativeTime, severityTone } from "../components/pieces";
import { type AgentHealth, api, type Incident } from "../lib/api";

export function Health({ onSelectRun }: { onSelectRun: (runId: string) => void }) {
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.agentHealth(), api.incidents()])
      .then(([healthValue, incidentValue]) => {
        if (cancelled) return;
        setHealth(healthValue);
        setIncidents(incidentValue);
      })
      .catch((exc: Error) => {
        if (!cancelled) setError(exc.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <Empty title="Could not load health">{error}</Empty>;
  if (health === null || incidents === null) return <Empty title="Loading…" />;

  return (
    <div className="stack">
      <Panel title="Agent" note={health.agent_id} bodyless>
        {health.run_count === 0 ? (
          <div className="panel-body">
            <p className="muted small">No agent has finished a run yet.</p>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>agent</th>
                  <th className="num">runs</th>
                  <th className="num">success</th>
                  <th className="num">tool success</th>
                  <th className="num">avg backtracks</th>
                  <th className="num">tokens / run</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="mono small">{health.agent_id}</td>
                  <td className="num">{health.run_count}</td>
                  <td className="num">
                    <Pill
                      tone={
                        health.success_rate >= 0.9
                          ? "ok"
                          : health.success_rate >= 0.7
                            ? "warn"
                            : "bad"
                      }
                    >
                      {(health.success_rate * 100).toFixed(0)}%
                    </Pill>
                  </td>
                  <td className="num">
                    {health.tool_success_rate === null
                      ? "—"
                      : `${(health.tool_success_rate * 100).toFixed(0)}%`}
                  </td>
                  <td className="num">{health.average_backtracks.toFixed(2)}</td>
                  <td className="num">{Math.round(health.tokens_per_run)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {health.run_count > 0 ? (
        <Panel title="Run outcomes" note={`${health.run_count} runs`}>
          <Bars
            rows={[
              { label: "completed", value: health.completed_count },
              { label: "unsuccessful", value: health.unsuccessful_count },
            ]}
          />
        </Panel>
      ) : null}

      <Panel title="Incidents" note={`${incidents.length} recorded`} bodyless>
        {incidents.length === 0 ? (
          <div className="panel-body">
            <p className="muted small">
              Nothing has gone wrong that the engine thought worth recording.
            </p>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>when</th>
                  <th>severity</th>
                  <th>category</th>
                  <th>what happened</th>
                  <th>run</th>
                </tr>
              </thead>
              <tbody>
                {incidents.map((incident) => (
                  <tr
                    key={`${incident.run_id}-${incident.category}-${incident.occurred_at}`}
                    className="clickable"
                    onClick={() => onSelectRun(incident.run_id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") onSelectRun(incident.run_id);
                    }}
                    tabIndex={0}
                  >
                    <td className="small" title={incident.occurred_at}>
                      {relativeTime(incident.occurred_at)}
                    </td>
                    <td>
                      <Pill tone={severityTone(incident.severity)}>{incident.severity}</Pill>
                    </td>
                    <td className="mono small">{incident.category}</td>
                    <td className="small">{incident.message}</td>
                    <td className="mono small muted">{incident.run_id.slice(0, 8)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
