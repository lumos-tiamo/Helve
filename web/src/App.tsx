import { useCallback, useEffect, useState } from "react";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Mark } from "./components/pieces";
import { ApiError, api, clearToken, readToken, storeToken } from "./lib/api";
import { Health } from "./views/Health";
import { RunDetail } from "./views/RunDetail";
import { Runs } from "./views/Runs";

type Tab = "runs" | "health";

/**
 * The token gate.
 *
 * In production the server injects the token into the page, so this never
 * appears.  It exists for `npm run dev`, where the console runs on Vite's own
 * port and has no way to read ~/.helve/auth_token.
 */
function TokenGate({ onSubmit }: { onSubmit: (token: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="token-gate panel">
      <div className="panel-head">
        <h2>Paste the local API token</h2>
      </div>
      <div className="panel-body">
        <p className="small muted" style={{ marginTop: 0 }}>
          The server writes one to <code>~/.helve/auth_token</code> on first start. Get it with:
        </p>
        <p>
          <code>cat ~/.helve/auth_token</code>
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (value.trim()) onSubmit(value.trim());
          }}
        >
          <input
            ref={(node) => node?.focus()}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="bearer token"
            aria-label="API token"
          />
          <button type="submit" disabled={!value.trim()}>
            Connect
          </button>
        </form>
      </div>
    </div>
  );
}

export function App() {
  const [tab, setTab] = useState<Tab>("runs");
  const [runId, setRunId] = useState<string | null>(null);
  const [hasToken, setHasToken] = useState(() => readToken() !== null);
  const [version, setVersion] = useState<string | null>(null);
  const [authFailed, setAuthFailed] = useState(false);

  // /api/health is unauthenticated, so it confirms the server is reachable
  // before any 401 can be blamed on the token.
  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((value) => {
        if (!cancelled) setVersion(value.version);
      })
      .catch(() => {
        if (!cancelled) setVersion(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A stored token that the server rejects has to be cleared, or the console
  // sits on a permanent 401 with no way back to the gate.
  useEffect(() => {
    if (!hasToken) return;
    let cancelled = false;
    api.runs(1).catch((exc) => {
      if (cancelled) return;
      if (exc instanceof ApiError && exc.status === 401) {
        clearToken();
        setHasToken(false);
        setAuthFailed(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [hasToken]);

  const accept = useCallback((token: string) => {
    storeToken(token);
    setAuthFailed(false);
    setHasToken(true);
  }, []);

  const openRun = useCallback((id: string) => {
    setRunId(id);
    setTab("runs");
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <Mark />
          Helve
        </span>
        <nav className="tabs">
          <button
            type="button"
            className="tab"
            aria-current={tab === "runs" && !runId ? "page" : undefined}
            onClick={() => {
              setTab("runs");
              setRunId(null);
            }}
          >
            Runs
          </button>
          <button
            type="button"
            className="tab"
            aria-current={tab === "health" ? "page" : undefined}
            onClick={() => {
              setTab("health");
              setRunId(null);
            }}
          >
            Health
          </button>
        </nav>
        <span className="spacer" />
        <span className="status">{version ? `server ${version}` : "server unreachable"}</span>
      </header>

      <main>
        {!hasToken ? (
          <>
            {authFailed ? (
              <div className="error-banner" style={{ maxWidth: 520, margin: "0 auto 12px" }}>
                That token was rejected. Paste the current one.
              </div>
            ) : null}
            <TokenGate onSubmit={accept} />
          </>
        ) : (
          <ErrorBoundary resetKey={`${tab}:${runId ?? ""}`}>
            {runId ? (
              <RunDetail runId={runId} onBack={() => setRunId(null)} />
            ) : tab === "runs" ? (
              <Runs onSelect={openRun} />
            ) : (
              <Health onSelectRun={openRun} />
            )}
          </ErrorBoundary>
        )}
      </main>
    </div>
  );
}
