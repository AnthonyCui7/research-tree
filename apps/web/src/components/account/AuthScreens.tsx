import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { ApiError, messageFrom } from "../../lib/apiError";
import { register, signIn, signInWithGoogle } from "../../data/session";
import { SignInBackdrop } from "./SignInBackdrop";

export type AuthMode = "sign-in" | "create-account";

type AuthScreensProps = {
  /** Something the app wants the reader to know before they sign in. */
  notice: string | null;
  initialMode?: AuthMode;
};

const MIN_PASSWORD_LENGTH = 10;

/** A refused attempt: under the field it concerns, or above the form. */
type AuthError = { field: "email" | "password" | null; message: string };

/**
 * The sign-in and create-account screens, from design 1c ("floating over the
 * tree, the product is the backdrop"). The whole app sits behind them: a
 * session is required before anything else renders, so there is nothing to
 * close and nowhere else to go. One 400px column centred on the dotted
 * canvas: title and one line, the form on a card, the line that swaps modes.
 * Behind it, faded, the real Prompting workspace.
 */
export function AuthScreens({ notice, initialMode = "sign-in" }: AuthScreensProps) {
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState<"form" | "google" | null>(null);
  const [error, setError] = useState<AuthError | null>(null);
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  useEffect(() => {
    setError(null);
  }, [mode]);

  const signingIn = mode === "sign-in";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const address = email.trim();
    if (!address) {
      setError({ field: "email", message: "Enter your email address." });
      return;
    }
    if (!signingIn && password.length < MIN_PASSWORD_LENGTH) {
      setError({ field: "password", message: `Use at least ${MIN_PASSWORD_LENGTH} characters.` });
      return;
    }
    setBusy("form");
    setError(null);
    try {
      if (signingIn) {
        await signIn(address, password);
      } else {
        await register(address, password);
      }
    } catch (requestError) {
      setError(authError(requestError, mode));
    } finally {
      // A sign-in that succeeded but whose session was then refused (the
      // address is off the allowlist) resolves without throwing; the store
      // shows the reason above, and the form has to be usable under it.
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
      setError({ field: null, message: messageFrom(requestError) });
      setBusy(null);
    }
  }

  const fieldError = (field: "email" | "password") =>
    error && error.field === field ? error.message : null;
  const banner = error && error.field === null ? error.message : null;

  return (
    <div
      className="relative h-screen overflow-hidden bg-[#f7f8f8] text-text-primary"
      style={{
        backgroundImage: "radial-gradient(#d8dce0 1px, transparent 1px)",
        backgroundSize: "20px 20px",
      }}
    >
      <SignInBackdrop />

      <div className="absolute top-1/2 left-1/2 flex w-[400px] max-w-[calc(100%-32px)] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-6 animate-interface-center-enter">
        <div className="flex flex-col gap-2 text-center">
          <h1
            className="m-0 text-[23px] leading-[1.2] font-semibold tracking-[-0.02em]"
            id="auth-title"
          >
            {signingIn ? "Sign in to Research Tree" : "Create your account"}
          </h1>
          <p className="m-0 max-w-[40ch] text-[13px] leading-[1.45] text-text-secondary">
            {signingIn
              ? "A map of a research field: its branches, its key papers, and the order to read them in."
              : "Free to use with your own OpenAI key."}
          </p>
        </div>

        <form
          className="flex w-full flex-col gap-[18px] rounded-xl border border-border bg-surface px-8 py-7 shadow-[0_12px_32px_-12px_rgb(31_35_40/18%),0_1px_2px_rgb(31_35_40/6%)]"
          onSubmit={submit}
          aria-labelledby="auth-title"
          noValidate
        >
          {notice ? (
            <p
              className="m-0 rounded-[6px] border border-warning-border bg-warning-surface px-3 py-2.5 text-xs leading-[1.45] text-text-primary"
              role="status"
            >
              {notice}
            </p>
          ) : null}
          {banner ? (
            <p
              className="m-0 rounded-[6px] border border-[color-mix(in_srgb,var(--color-error)_30%,#fff)] bg-[color-mix(in_srgb,var(--color-error)_7%,#fff)] px-3 py-2.5 text-xs leading-[1.45] text-text-primary"
              role="alert"
            >
              {banner}
            </p>
          ) : null}

          <div className="flex flex-col gap-[18px]">
            <button
              className="flex h-9 w-full items-center justify-center gap-2.5 rounded-[6px] border border-border bg-surface px-3 text-[13px] font-medium text-text-primary transition-[background-color] duration-150 enabled:hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-60"
              type="button"
              onClick={() => void google()}
              disabled={busy !== null}
            >
              <GoogleMark />
              {busy === "google" ? "Opening Google…" : "Continue with Google"}
            </button>
            <div className="flex items-center gap-3 text-[11px] text-text-muted" role="presentation">
              <span className="h-px flex-1 bg-border" />
              or
              <span className="h-px flex-1 bg-border" />
            </div>
          </div>

          <div className="flex flex-col gap-3.5">
            <div className="flex flex-col gap-1.5">
              <label className={labelClass} htmlFor={emailId}>
                Email
              </label>
              <input
                className={inputClass(fieldError("email") !== null)}
                id={emailId}
                type="email"
                name="email"
                placeholder="you@university.edu"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={busy !== null}
                aria-invalid={fieldError("email") !== null || undefined}
                aria-describedby={fieldError("email") ? errorId : undefined}
                required
              />
              {fieldError("email") ? <FieldError id={errorId}>{fieldError("email")}</FieldError> : null}
            </div>
            <div className="flex flex-col gap-1.5">
              <label className={labelClass} htmlFor={passwordId}>
                Password
              </label>
              <span className="relative flex">
                <input
                  className={cx(inputClass(fieldError("password") !== null), "pr-[52px]")}
                  id={passwordId}
                  type={reveal ? "text" : "password"}
                  name="password"
                  autoComplete={signingIn ? "current-password" : "new-password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={busy !== null}
                  minLength={signingIn ? undefined : MIN_PASSWORD_LENGTH}
                  aria-invalid={fieldError("password") !== null || undefined}
                  aria-describedby={fieldError("password") ? errorId : undefined}
                  required
                />
                <button
                  className="absolute top-1.5 right-1.5 h-6 rounded-[4px] border-0 bg-transparent px-2 text-[11px] font-medium text-text-secondary transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
                  type="button"
                  onClick={() => setReveal((current) => !current)}
                  aria-pressed={reveal}
                  aria-controls={passwordId}
                >
                  {reveal ? "Hide" : "Show"}
                </button>
              </span>
              {fieldError("password") ? (
                <FieldError id={errorId}>{fieldError("password")}</FieldError>
              ) : signingIn ? null : (
                <span className="text-[11px] leading-[1.45] text-text-muted">
                  At least {MIN_PASSWORD_LENGTH} characters.
                </span>
              )}
            </div>
          </div>

          <button
            className="h-9 w-full rounded-[6px] border-0 bg-accent text-[13px] font-medium text-white transition-[background-color] duration-150 enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:bg-border-strong"
            type="submit"
            disabled={busy !== null}
          >
            {busy === "form"
              ? signingIn
                ? "Signing in…"
                : "Creating account…"
              : signingIn
                ? "Sign in"
                : "Create account"}
          </button>
        </form>

        <p className="m-0 text-xs text-text-secondary">
          {signingIn ? (
            <>
              New to Research Tree?{" "}
              <ToggleLink onClick={() => setMode("create-account")}>Create account</ToggleLink>
            </>
          ) : (
            <>
              Have an account? <ToggleLink onClick={() => setMode("sign-in")}>Sign in</ToggleLink>
            </>
          )}
        </p>
      </div>
    </div>
  );
}

const labelClass = "text-xs leading-[1.35] font-medium text-text-secondary";

function inputClass(invalid: boolean): string {
  return cx(
    "h-9 w-full rounded-[6px] border bg-surface px-[11px] text-[13px] text-text-primary outline-0 transition-[border-color,box-shadow] duration-150 placeholder:text-text-muted disabled:text-text-secondary",
    invalid
      ? "border-error shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-error)_10%,transparent)]"
      : "border-border focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-subtle)]",
  );
}

function FieldError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <span className="text-xs leading-[1.45] text-error" id={id} role="alert">
      {children}
    </span>
  );
}

function authError(error: unknown, mode: AuthMode): AuthError {
  const message = messageFrom(error);
  const code = error instanceof ApiError ? error.code : "";
  const status = error instanceof ApiError ? error.status : undefined;
  if (code === "LOGIN_BAD_CREDENTIALS" || (mode === "sign-in" && status === 400)) {
    return { field: "password", message: "Incorrect email or password." };
  }
  if (code === "REGISTER_USER_ALREADY_EXISTS") {
    return { field: "email", message: "An account with this email already exists. Sign in instead." };
  }
  if (code === "REGISTER_INVALID_PASSWORD") {
    return { field: "password", message: message || "Choose a longer password." };
  }
  return { field: null, message };
}

function ToggleLink({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      className="border-0 bg-transparent p-0 text-xs font-medium text-accent transition-[color] duration-150 hover:text-accent-deep"
      type="button"
      onClick={onClick}
    >
      {children}
    </button>
  );
}

/** Google's own mark, at the size its sign-in guidance draws it. */
function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
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
