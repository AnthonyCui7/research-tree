import { useEffect, useState } from "react";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { primaryActionClass, textInputClass } from "../../lib/controlClasses";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { AccountDialog, AccountNotice, AccountSection } from "./AccountDialog";
import type { ApiKeyStatus } from "../../lib/types";

/**
 * OpenAI is the only key this product asks for: retrieval uses Semantic Scholar
 * unauthenticated, and construction, TLDRs and the assistant all run on one key.
 *
 * The key shown is the one the server process is using, reported as its last
 * four characters — a whole key never leaves the backend. Saving a new one needs
 * somewhere to put it that is encrypted and scoped to an account, so the field
 * is here and the action is not: see PROJECT.md "Next Steps".
 */
export function ApiKeysDialog({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<ApiKeyStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    let cancelled = false;
    repositoryWorkspaceGateway
      .getApiKeys()
      .then((keys) => {
        if (!cancelled) setStatus(keys.openai);
      })
      .catch((requestError: unknown) => {
        if (!cancelled) setError(messageFrom(requestError));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AccountDialog
      title="API keys"
      onClose={onClose}
      footer={
        <button className={cx(primaryActionClass, "ml-auto")} type="button" disabled>
          Save key
        </button>
      }
    >
      <AccountSection title="OpenAI">
        <div className="flex items-center gap-2.5 rounded-lg border border-border bg-surface-subtle px-3.5 py-2.5">
          <span
            className={cx(
              "h-1.5 w-1.5 flex-none rounded-full",
              status?.configured ? "bg-accent" : "bg-text-muted",
            )}
            aria-hidden="true"
          />
          <span className="min-w-0 flex-1 text-[12.5px] text-text-primary">
            {status === null
              ? "Checking…"
              : status.configured
                ? "A key is configured on the server"
                : "No key is configured"}
          </span>
          {status?.masked ? (
            <code className="flex-none font-mono text-[12px] text-text-muted">{status.masked}</code>
          ) : null}
        </div>
        {status && !status.configured ? (
          <AccountNotice>
            Set <code className="font-mono text-[11.5px]">OPENAI_API_KEY</code> in the server's{" "}
            <code className="font-mono text-[11.5px]">.env</code> and restart it. Without a key,
            topic review, workspace builds and the assistant all fail.
          </AccountNotice>
        ) : null}
      </AccountSection>

      <AccountSection title="Replace the key">
        <input
          className={textInputClass}
          type="password"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="sk-…"
          autoComplete="off"
          spellCheck={false}
          aria-label="New OpenAI API key"
        />
        <AccountNotice>
          Saving is off in this build. Nothing you type here is sent anywhere. Change the key in the
          server's{" "}
          <code className="font-mono text-[11.5px]">.env</code> for now.
        </AccountNotice>
      </AccountSection>

      {error ? (
        <p
          className="mt-4 mb-0 rounded-lg border border-error-border bg-error-surface px-3.5 py-3 text-xs leading-[1.5] text-error"
          role="alert"
        >
          {error}
        </p>
      ) : null}
    </AccountDialog>
  );
}
