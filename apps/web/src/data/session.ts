import { useSyncExternalStore } from "react";
import { ApiError } from "../lib/apiError";
import type { SessionInfo } from "../lib/types";
import { API_BASE_URL, onUnauthenticated, requestJson, requestRaw } from "./workspaceApi";

/**
 * The one signed-in state the whole app reads.
 *
 * `loading` until the first `/account/me` answers; `ready` with the session
 * after that; `signed-out` when there is none or one expires (every 401 lands
 * here); `unreachable` when the server could not be asked at all, which is a
 * different problem from not being signed in and gets a different screen.
 */
export type SessionState =
  | { status: "loading" }
  | { status: "ready"; session: SessionInfo }
  | { status: "signed-out"; notice: string | null }
  | { status: "unreachable"; message: string };

let state: SessionState = { status: "loading" };
const listeners = new Set<() => void>();

function setState(next: SessionState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useSession(): SessionState {
  return useSyncExternalStore(subscribe, () => state);
}

/** The store's current value, for code that runs outside React's render. */
export function sessionState(): SessionState {
  return state;
}

/** The single implicit local user, as opposed to an account behind sign-in. */
export function isLocalSession(session: SessionInfo): boolean {
  return session.auth_mode === "none";
}

/** The session when signed in, or null; for components that render either way. */
export function useSessionInfo(): SessionInfo | null {
  const current = useSession();
  return current.status === "ready" ? current.session : null;
}

onUnauthenticated(() => {
  if (state.status === "ready" && state.session.auth_mode === "accounts") {
    setState({ status: "signed-out", notice: "Your session ended. Sign in again to continue." });
  }
});

/**
 * Read who is signed in.
 *
 * `recheck` is for the one caller that asks again after the event stream gave
 * up, where the question is only ever "has the session expired". There, a
 * failure that is not a 401 or a 403 leaves the app exactly as it is: the
 * unreachable screen unmounts everything, so a single transient error used to
 * cost the reader their selection, their open panels and anything they had
 * typed, for a session that was fine.
 */
export async function loadSession(options: { recheck?: boolean } = {}): Promise<void> {
  // Read and cleared whatever the outcome: a reader who already holds a
  // session should not carry a stale `auth_error` in the address bar.
  const urlNotice = consumeAuthErrorFromUrl();
  try {
    const session = await requestJson<SessionInfo>("/account/me", { method: "GET" });
    setState({ status: "ready", session });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      // A notice already on the screen (the stream saw the session end, the
      // 401 listener said so) is the better explanation; keep it.
      const current = state.status === "signed-out" ? state.notice : null;
      setState({ status: "signed-out", notice: urlNotice ?? current });
      return;
    }
    if (error instanceof ApiError && error.status === 403) {
      setState({ status: "signed-out", notice: error.message });
      return;
    }
    if (options.recheck) {
      return;
    }
    setState({
      status: "unreachable",
      message:
        error instanceof Error && error.message
          ? error.message
          : "We could not reach the server.",
    });
  }
}

export async function signIn(email: string, password: string): Promise<void> {
  const form = new URLSearchParams({ username: email.trim(), password });
  await requestRaw("/auth/login", { method: "POST", body: form });
  await loadSession();
}

export async function register(email: string, password: string): Promise<void> {
  await requestRaw("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email: email.trim(), password }),
  });
  await signIn(email, password);
}

export async function signInWithGoogle(): Promise<void> {
  const payload = await requestJson<{ authorization_url: string }>("/auth/google/authorize", {
    method: "GET",
  });
  window.location.assign(payload.authorization_url);
}

export async function signOut(): Promise<void> {
  try {
    await requestRaw("/auth/logout", { method: "POST" });
  } catch (error) {
    // A session the server no longer has is already over. Anything else
    // leaves the cookie valid, and a sign-in screen over a live session would
    // only look signed out: the caller says so and the app stays.
    if (!(error instanceof ApiError && error.status === 401)) throw error;
  }
  setState({ status: "signed-out", notice: null });
}

/** Where the Google round trip lands when it fails: a reason in the URL. */
function consumeAuthErrorFromUrl(): string | null {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("auth_error");
  if (!code) return null;
  url.searchParams.delete("auth_error");
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  switch (code) {
    case "not_allowed":
      return "Research Tree is in a private preview. Ask the person who invited you to add your email, then sign in again.";
    case "google_sign_in_failed":
      return "Google sign-in did not complete. Try again, or use your email and password.";
    default:
      return "Sign-in did not complete. Please try again.";
  }
}

/** Absolute URL of the API, for links that leave the app (none today). */
export const apiBaseUrl = API_BASE_URL;
