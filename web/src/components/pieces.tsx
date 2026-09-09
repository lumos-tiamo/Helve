/** Small shared pieces: the mark, status pills, bar rows, empty states. */

import type { ReactNode } from "react";

export function Mark({ size = 18 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} role="img" aria-label="Helve">
      <title>Helve</title>
      <g fill="var(--accent)">
        <path d="M13 6h31l8 3.2v7.6l-8 3.2h-31a2 2 0 0 1-2-2v-10a2 2 0 0 1 2-2z" />
        <path
          fillRule="evenodd"
          d="M22 20h8l1.2 36.6a2.2 2.2 0 0 1-2.2 2.3h-6a2.2 2.2 0 0 1-2.2-2.3z
             M22.3 37.5h7.5v2.8h-7.5z
             M22.2 44h7.7v2.8h-7.7z"
        />
      </g>
    </svg>
  );
}

type Tone = "ok" | "warn" | "bad" | "neutral";

/** Maps a run outcome to a tone.  Unknown outcomes stay neutral rather than
 *  being guessed into "ok", which would make a new failure state look fine. */
export function outcomeTone(outcome: string | null): Tone {
  switch (outcome) {
    case "completed":
    case "done":
    case "ok":
      return "ok";
    case "incomplete":
    case "awaiting_input":
    case "cancelled":
      return "warn";
    case "failed":
    case "blocked":
    case "error":
      return "bad";
    default:
      return "neutral";
  }
}

export function severityTone(severity: string): Tone {
  const value = severity.toLowerCase();
  if (value === "critical" || value === "high" || value === "error") return "bad";
  if (value === "medium" || value === "warning" || value === "warn") return "warn";
  if (value === "low" || value === "info") return "neutral";
  return "neutral";
}

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function Tile({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="tile">
      <span className="v">{value}</span>
      <span className="k">{label}</span>
    </div>
  );
}

export function Panel({
  title,
  note,
  children,
  bodyless,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
  bodyless?: boolean;
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {note ? <span className="note">{note}</span> : null}
      </div>
      {bodyless ? children : <div className="panel-body">{children}</div>}
    </section>
  );
}

export function Bars({
  rows,
  format,
}: {
  rows: { label: string; value: number }[];
  format?: (value: number) => string;
}) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  return (
    <div className="bars">
      {rows.map((row) => (
        <div className="bar-row" key={row.label}>
          <span className="bar-label" title={row.label}>
            {row.label}
          </span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(row.value / max) * 100}%` }} />
          </div>
          <span className="bar-value">{format ? format(row.value) : row.value}</span>
        </div>
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function relativeTime(timestamp: string): string {
  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) return timestamp;
  const seconds = Math.round((Date.now() - parsed) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86_400)}d ago`;
}

export function compactNumber(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}
