/**
 * A render error must not blank the page.
 *
 * Written after a real one did exactly that: the health endpoint returns a
 * single object, the client typed it as a list, and the resulting `.map` of
 * undefined unmounted the whole tree with nothing on screen and nothing in the
 * console.  A dashboard that fails invisibly is worse than one that fails
 * loudly — the reader cannot tell "no data" from "broken".
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Remounts the boundary when it changes, so navigating away clears the error. */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("console render failed", error, info.componentStack);
  }

  componentDidUpdate(previous: Props): void {
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="state">
        <h3>This view failed to render</h3>
        <p className="small">
          The data did not have the shape the console expected. The details are in the browser
          console.
        </p>
        <p className="mono small muted">{error.message}</p>
      </div>
    );
  }
}
