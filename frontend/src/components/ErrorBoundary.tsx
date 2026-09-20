import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

// Top-level fallback for any unexpected render-time exception (e.g.
// a vertical's decision detail having a genuinely unexpected shape
// that slips past the formatter's defensive checks). Without this,
// React unmounts the entire page on any uncaught error, leaving a
// blank screen with no indication anything went wrong — exactly what
// happened with the missing action_name crash found while building
// this UI pass, before this boundary existed.
class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: unknown) {
    console.error("Unhandled error in page render:", error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="bg-white rounded shadow p-6 max-w-md mx-auto mt-12 text-center">
          <p className="text-slate-700 font-medium mb-2">Something went wrong on this page.</p>
          <p className="text-sm text-slate-500 mb-4">
            Try refreshing, or go back and try a different page.
          </p>
          <a
            href="/"
            className="inline-block bg-slate-900 text-white text-sm px-4 py-2 rounded-lg hover:bg-slate-700 transition-colors"
          >
            Back to Dashboard
          </a>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;