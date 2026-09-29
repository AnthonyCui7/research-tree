import { useEffect, type ReactNode } from "react";
import { AuthScreens } from "../components/account/AuthScreens";
import { primaryActionClass } from "../lib/controlClasses";
import { loadSession, useSession } from "../data/session";

/**
 * Nothing in the app renders until the server has said who is looking at it.
 * With accounts off the answer is immediate (the local profile); with accounts
 * on, a missing or expired session shows the sign-in screen in place of the app.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const session = useSession();

  useEffect(() => {
    void loadSession();
  }, []);

  if (session.status === "ready") {
    return <>{children}</>;
  }
  if (session.status === "signed-out") {
    return <AuthScreens notice={session.notice} />;
  }
  if (session.status === "unreachable") {
    return (
      <div className="grid min-h-screen place-items-center bg-background p-6">
        <div className="w-[400px] max-w-full rounded-2xl border border-border bg-surface p-6">
          <h1 className="m-0 text-17 font-semibold text-text-primary text-trim">
            Research Tree
          </h1>
          <p className="mt-3 mb-0 text-14 text-text-secondary text-trim">{session.message}</p>
          <button
            className={`${primaryActionClass} mt-6`}
            type="button"
            onClick={() => void loadSession()}
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="grid min-h-screen place-items-center bg-surface" aria-busy="true">
      <span
        className="h-5 w-5 animate-progress-spin rounded-full border-2 border-accent-subtle border-t-accent"
        role="status"
        aria-label="Loading"
      />
    </div>
  );
}
