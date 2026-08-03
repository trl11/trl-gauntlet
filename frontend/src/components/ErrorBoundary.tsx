import { Button } from "@trl11/components/ui";
import { Component } from "react";
import type { ReactNode } from "react";

import "./ErrorBoundary.scss";

/** Props for {@link ErrorBoundary}. */
export interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/** Catches render errors below it so one broken view cannot blank the app. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  private readonly retry = () => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;

    return (
      <div className="error-boundary" role="alert">
        <h2 className="error-boundary__title">Something broke while rendering this view</h2>
        <pre className="error-boundary__message">{error.message}</pre>
        <Button color="blue" onClick={this.retry}>
          Try again
        </Button>
      </div>
    );
  }
}

export default ErrorBoundary;
