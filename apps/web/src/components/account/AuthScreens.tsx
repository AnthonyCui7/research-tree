import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { messageFrom } from "../../lib/apiError";
import { primaryActionClass, textInputClass } from "../../lib/controlClasses";
import { register, signIn, signInWithGoogle } from "../../data/session";

export type AuthMode = "sign-in" | "create-account";

type AuthScreensProps = {
  /** Something the app wants the reader to know before they sign in. */
  notice: string | null;
  initialMode?: AuthMode;
};

const MIN_PASSWORD_LENGTH = 10;

/**
 * The sign-in and create-account screens. The whole app sits behind them:
 * a session is required before anything else renders, so there is nothing to
 * close and nowhere else to go. The left column says what the product is for
 * a reader who arrived by link; the right column is the form.
 */
export function AuthScreens({ notice, initialMode = "sign-in" }: AuthScreensProps) {
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<"form" | "google" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const emailId = useId();
  const passwordId = useId();

  useEffect(() => {
    setError(null);
  }, [mode]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const address = email.trim();
    if (!address) {
      setError("Enter your email address.");
      return;
    }
    if (mode === "create-account" && password.length < MIN_PASSWORD_LENGTH) {
      setError(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    setBusy("form");
    setError(null);
    try {
      if (mode === "sign-in") {
        await signIn(address, password);
      } else {
        await register(address, password);
      }
    } catch (requestError) {
      setError(authMessage(requestError, mode));
      setBusy(null);
    }
  }

  async function google() {
    if (busy) return;
    setBusy("google");
    setError(null);
    try {
      await signInWithGoogle();
    } catch (requestError) {
      setError(messageFrom(requestError));
      setBusy(null);
    }
  }

  const title = mode === "sign-in" ? "Sign in" : "Create your account";

  return (
    <div className="grid min-h-screen grid-cols-[minmax(0,5fr)_minmax(0,6fr)] bg-background max-[900px]:grid-cols-1">
      <section
        className="flex flex-col justify-between border-r border-border bg-surface-subtle px-14 py-12 max-[900px]:hidden"
        aria-label="About Research Tree"
      >
        <div className="text-sm font-bold tracking-[-0.01em] text-text-primary">Research Tree</div>
        <div className="max-w-[46ch]">
          <h1 className="m-0 text-[28px] leading-[1.2] font-bold tracking-[-0.02em] text-text-primary">
            A map of a research field, built from its literature.
          </h1>
          <p className="mt-4 mb-0 text-[14px] leading-[1.62] text-text-secondary">
            Name a topic. Research Tree finds the papers that matter, arranges them into the
            field&rsquo;s lines of work, and orders each line by what you need to have read
            first.
          </p>
          <dl className="mt-8 mb-0 divide-y divide-hairline border-y border-hairline">
            <Point term="Branches, not a reading list">
              The field&rsquo;s lines of work, each with the papers that define it.
            </Point>
            <Point term="Paths in prerequisite order">
              Reading paths ordered by what builds on what, not by publication date.
            </Point>
            <Point term="Edits you review">
              An assistant proposes changes to the map; nothing lands until you approve it.
            </Point>
          </dl>
        </div>
        <p className="m-0 text-[11.5px] text-text-muted">
          Open source · Papers from Semantic Scholar · Reading with your own model key
        </p>
      </section>

      <section className="grid place-items-center px-6 py-10">
        <form
          className="w-[380px] max-w-full rounded-[14px] border border-border bg-surface px-8 pt-8 pb-7 shadow-dialog"
          onSubmit={submit}
          aria-labelledby="auth-title"
          noValidate
        >
          <div className="text-sm font-bold tracking-[-0.01em] text-text-primary min-[901px]:hidden">
            Research Tree
          </div>
          <h2
            className="mt-[18px] mb-0 text-[19px] font-bold tracking-[-0.015em] text-text-primary min-[901px]:mt-0"
            id="auth-title"
          >
            {title}
          </h2>
          <p className="mt-1.5 mb-0 text-[12.5px] leading-[1.5] text-text-secondary">
            {mode === "sign-in"
              ? "Your workspaces are waiting where you left them."
              : "Your workspaces belong to your account and follow you between devices."}
          </p>

          {notice ? (
            <p
              className="mt-4 mb-0 rounded-[8px] border border-warning-border bg-warning-surface px-3 py-2 text-[12px] leading-[1.5] text-warning"
              role="status"
            >
              {notice}
            </p>
          ) : null}

          <button
            className="mt-[18px] flex w-full items-center justify-center gap-2.5 rounded-[9px] border border-border-strong bg-surface px-3.5 py-[9px] text-[13px] font-semibold text-text-primary transition-[background-color] duration-150 enabled:hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-60"
            type="button"
            onClick={() => void google()}
            disabled={busy !== null}
          >
            <GoogleMark />
            {busy === "google" ? "Opening Google…" : "Continue with Google"}
          </button>

          <div className="my-[18px] flex items-center gap-2.5" role="presentation">
            <span className="h-px flex-1 bg-hairline" />
            <span className="text-[11px] text-text-muted">or</span>
            <span className="h-px flex-1 bg-hairline" />
          </div>

          <Field label="Email" htmlFor={emailId}>
            <input
              className={textInputClass}
              id={emailId}
              type="email"
              name="email"
              placeholder="you@university.edu"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={busy !== null}
              required
            />
          </Field>
          <div className="mt-3.5">
            <Field label="Password" htmlFor={passwordId}>
              <input
                className={textInputClass}
                id={passwordId}
                type="password"
                name="password"
                placeholder="••••••••••"
                autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy !== null}
                minLength={mode === "create-account" ? MIN_PASSWORD_LENGTH : undefined}
                required
              />
            </Field>
            {mode === "create-account" ? (
              <p className="mt-1.5 mb-0 text-[11px] text-text-muted">
                At least {MIN_PASSWORD_LENGTH} characters. A passphrase works well.
              </p>
            ) : null}
          </div>

          {error ? (
            <p
              className="mt-3.5 mb-0 rounded-[8px] border border-error-border bg-error-surface px-3 py-2 text-[12px] leading-[1.5] text-error"
              role="alert"
            >
              {error}
            </p>
          ) : null}

          <button
            className={cx(primaryActionClass, "mt-3.5 w-full")}
            type="submit"
            disabled={busy !== null}
          >
            {busy === "form"
              ? mode === "sign-in"
                ? "Signing in…"
                : "Creating account…"
              : mode === "sign-in"
                ? "Continue"
                : "Create account"}
          </button>

          <div className="mt-5 border-t border-hairline pt-4 text-xs text-text-secondary">
            {mode === "sign-in" ? (
              <>
                New here?{" "}
                <ToggleLink onClick={() => setMode("create-account")}>Create an account</ToggleLink>
              </>
            ) : (
              <>
                Have an account? <ToggleLink onClick={() => setMode("sign-in")}>Sign in</ToggleLink>
              </>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}

function authMessage(error: unknown, mode: AuthMode): string {
  const message = messageFrom(error);
  const code = (error as { code?: string })?.code ?? "";
  if (code === "LOGIN_BAD_CREDENTIALS") {
    return "That email and password do not match.";
  }
  if (code === "REGISTER_USER_ALREADY_EXISTS") {
    return "An account with this email already exists. Sign in instead.";
  }
  if (code === "REGISTER_INVALID_PASSWORD") {
    return message || "Choose a longer password.";
  }
  if (mode === "sign-in" && (error as { status?: number })?.status === 400) {
    return "That email and password do not match.";
  }
  return message;
}

function Point({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[180px_minmax(0,1fr)] gap-4 py-3 max-[1100px]:grid-cols-1 max-[1100px]:gap-1">
      <dt className="text-[12.5px] font-semibold text-text-primary">{term}</dt>
      <dd className="m-0 text-[12.5px] leading-[1.55] text-text-secondary">{children}</dd>
    </div>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div>
      <label className="mb-1.5 block text-xs font-semibold text-text-primary" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
    </div>
  );
}

function ToggleLink({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      className="border-0 bg-transparent p-0 text-xs font-semibold text-accent transition-[color] duration-150 hover:text-accent-deep hover:underline"
      type="button"
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"
      />
    </svg>
  );
}
