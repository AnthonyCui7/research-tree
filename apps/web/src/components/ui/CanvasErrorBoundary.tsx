import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Keeps a workspace the canvas cannot render from taking the app with it.
 *
 * The tree is rendered from a document a model wrote, and one field of the
 * wrong shape throws during render. React unmounts the whole tree when nothing
 * catches that, which left a blank page: no sidebar, no way to open another
 * workspace, and a reload that reproduced it. Everything a reader needs to get
 * out of a damaged workspace lives outside this boundary.
 */
type Props = { children: ReactNode; fallback: ReactNode };
type State = { failed: boolean };

export class CanvasErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("workspace canvas failed to render", error, info.componentStack);
  }

  componentDidUpdate(previous: Props): void {
    // A different workspace deserves its own attempt.
    if (this.state.failed && previous.children !== this.props.children) {
      this.setState({ failed: false });
    }
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
