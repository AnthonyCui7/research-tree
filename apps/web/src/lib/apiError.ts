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

  constructor(message: string, status: number, detail = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    const detail = await readDetail(response);
    return new ApiError(messageForStatus(response.status, detail), response.status, detail);
  }

  static fromNetworkFailure(error: unknown): ApiError {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return new ApiError(
      timedOut
        ? "That request took too long. Check the backend and try again."
        : "We could not reach the server. Check that the backend is running.",
      0,
    );
  }

  /** True when the workspace changed underneath this request. */
  get isVersionConflict(): boolean {
    return this.status === 409;
  }
}

/** Message for any thrown value, so callers never have to guess a shape. */
export function messageFrom(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong. Please try again.";
}

async function readDetail(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    const detail = (payload as { detail?: unknown })?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
    return "";
  } catch {
    return "";
  }
}

function messageForStatus(status: number, detail: string): string {
  switch (status) {
    case 400:
    case 422:
      return detail || "That request was not valid.";
    case 404:
      return "That workspace no longer exists.";
    case 409:
      return "This workspace changed since you opened it. Refresh and try again.";
    case 429:
      return "Too many requests right now. Wait a moment and try again.";
    default:
      if (status >= 500) return detail || "The server hit an error. Please try again.";
      return detail || "We could not complete that request. Please try again.";
  }
}
