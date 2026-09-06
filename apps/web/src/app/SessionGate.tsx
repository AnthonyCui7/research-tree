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
        <div className="w-[380px] max-w-full rounded-[14px] border border-border bg-surface px-7 py-6 shadow-dialog">
          <div className="text-sm font-bold tracking-[-0.01em] text-text-primary">Research Tree</div>
          <p className="mt-3 mb-0 text-[13px] leading-[1.55] text-text-secondary">{session.message}</p>
          <button
            className={`${primaryActionClass} mt-4`}
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
    <div className="grid min-h-screen place-items-center bg-background" aria-busy="true">
      <span className="text-[12.5px] text-text-muted">Loading…</span>
    </div>
  );
}
