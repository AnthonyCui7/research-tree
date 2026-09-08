/**
 * A failed API call, carrying enough detail to say what actually went wrong.
 *
 * Every failure used to surface as one generic sentence, which made a version
 * conflict indistinguishable from a dropped connection. Both need different
 * things from the user, so both need different messages.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** The server's machine-readable reason, when it gave one. */
  readonly code: string;

  constructor(message: string, status: number, detail = "", code = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    const { detail, code } = await readDetail(response);
    return new ApiError(messageForStatus(response.status, detail, code), response.status, detail, code);
  }

  static fromNetworkFailure(error: unknown): ApiError {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return new ApiError(
      timedOut
        ? "That request took too long. Try again."
        : "We could not reach the server.",
      0,
    );
  }

  /** True when the workspace changed underneath this request. */
  get isVersionConflict(): boolean {
    return this.status === 409;
  }
}

/**
 * Shown when a mutation lost a race with another change to the same workspace.
 * The caller refreshes alongside it, so the user is told what to look at rather
 * than told to refresh by hand.
 */
export const VERSION_CONFLICT_MESSAGE =
  "This workspace changed since you loaded it. It has been refreshed; review the latest version and try again.";

/** True when a failed mutation was rejected as stale, not as broken. */
export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.isVersionConflict;
}

/** Message for any thrown value, so callers never have to guess a shape. */
export function messageFrom(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong. Please try again.";
}

async function readDetail(response: Response): Promise<{ detail: string; code: string }> {
  try {
    const payload = (await response.json()) as { detail?: unknown; error_code?: unknown };
    const code = typeof payload?.error_code === "string" ? payload.error_code : "";
    const detail = payload?.detail;
    if (typeof detail === "string") return { detail, code: code || detail };
    if (detail && typeof detail === "object") {
      // fastapi-users answers `{code, reason}` for a refused password.
      const nested = detail as { message?: unknown; reason?: unknown; code?: unknown };
      const text = [nested.message, nested.reason].find((value) => typeof value === "string");
      return {
        detail: typeof text === "string" ? text : "",
        code: code || (typeof nested.code === "string" ? nested.code : ""),
      };
    }
    return { detail: "", code };
  } catch {
    return { detail: "", code: "" };
  }
}

function messageForStatus(status: number, detail: string, code: string): string {
  switch (status) {
    case 400:
    case 422:
      return detail || "That request was not valid.";
    case 401:
      return "Your session has ended. Sign in again to continue.";
    case 402:
      return detail || "This account has no way to pay for model calls yet.";
    case 403:
      return code === "csrf_origin_rejected"
        ? "That request did not come from Research Tree. Reload the page and try again."
        : detail || "This account is not allowed to do that.";
    case 404:
      // The server distinguishes a missing workspace from a missing version;
      // every other status here already prefers what it said.
      return detail || "That workspace no longer exists.";
    case 409:
      return "This workspace changed since you opened it. Refresh and try again.";
    case 413:
      return "That request is too large.";
    case 429:
      return detail || "Too many requests right now. Wait a moment and try again.";
    default:
      if (status >= 500) return detail || "The server hit an error. Please try again.";
      return detail || "We could not complete that request. Please try again.";
  }
}
