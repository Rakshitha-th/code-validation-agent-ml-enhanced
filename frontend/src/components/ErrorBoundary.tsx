import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  readonly children: ReactNode;
}

interface ErrorBoundaryState {
  readonly hasError: boolean;
}

export class ErrorBoundary extends Component<Readonly<ErrorBoundaryProps>, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Keep stack traces in developer tooling only; never render them to users.
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return <main className="page page--error"><h1>Something went wrong</h1><p>The page could not be displayed. Reload the application and try again.</p><button className="btn" onClick={() => this.setState({ hasError: false })}>Try again</button></main>;
    }
    return this.props.children;
  }
}
