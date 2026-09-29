import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Keeps a part of the page that fails to render from taking the app with it.
 *
 * React unmounts the whole tree when nothing catches a render error. The canvas
 * renders a document a model wrote, where one field of the wrong shape throws;
 * the assistant and the reader load their code when first opened, and a tab
 * opened before a deploy asks for files the deploy replaced. Everything a
 * reader needs to get out of either lives outside these boundaries.
 */
type Props = { children: ReactNode; fallback: ReactNode };
type State = { failed: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("part of the page failed to render", error, info.componentStack);
  }

  componentDidUpdate(previous: Props): void {
    // New content (another workspace, say) deserves its own attempt.
    if (this.state.failed && previous.children !== this.props.children) {
      this.setState({ failed: false });
    }
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
