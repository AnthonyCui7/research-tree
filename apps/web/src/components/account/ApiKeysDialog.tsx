import { useCallback, useEffect, useState } from "react";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { longDateLabel, pluralize } from "../../lib/format";
import {
  compactActionClass,
  errorNoticeClass,
  primaryActionClass,
  textInputClass,
} from "../../lib/controlClasses";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { isLocalSession, useSessionInfo } from "../../data/session";
import { AccountDialog, AccountNotice, AccountSection } from "./AccountDialog";
import type { AllowanceSummary, ApiKeysResult, UsageSummary } from "../../lib/types";

const RECENT_USAGE_ROWS = 5;

/**
 * OpenAI is the only key this product asks for: retrieval uses Semantic Scholar
 * unauthenticated, and construction, TLDRs and the assistant all run on one key.
 *
 * Behind sign-in the key belongs to the account: checked with OpenAI, stored
 * sealed on the server, named here by its last four characters, and spent
 * only by that account's own work. An account without one may instead hold a
 * sponsored allowance on the operator's key. The local profile reads the
 * server's key and cannot save one.
 */
export function ApiKeysDialog({ onClose }: { onClose: () => void }) {
  const session = useSessionInfo();
  const local = session === null || isLocalSession(session);
  const [keys, setKeys] = useState<ApiKeysResult | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<"save" | "remove" | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    repositoryWorkspaceGateway
      .getApiKeys()
      .then((next) => {
        if (!cancelled) setKeys(next);
      })
      .catch((requestError: unknown) => {
        if (!cancelled) setError(messageFrom(requestError));
      });
    if (!local) {
      repositoryWorkspaceGateway
        .getUsage()
        .then((next) => {
          if (!cancelled) setUsage(next);
        })
        // The key section stands on its own; a usage read that fails leaves it out.
        .catch(() => {});
    }
    return () => {
      cancelled = true;
    };
  }, [local]);

  useEffect(() => load(), [load]);

  async function save() {
    const value = draft.trim();
    if (!value || busy) return;
    setBusy("save");
    setError(null);
    try {
      const result = await repositoryWorkspaceGateway.saveApiKey(value);
      if (result.stored) {
        setDraft("");
      } else {
        setError(result.detail);
      }
      load();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (busy) return;
    setBusy("remove");
    setError(null);
    try {
      await repositoryWorkspaceGateway.removeApiKey();
      load();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy(null);
    }
  }

  const openai = keys?.openai ?? null;
  const allowance = keys?.allowance ?? null;
  const canSave = !local && Boolean(keys?.saving_enabled);

  return (
    <AccountDialog
      title="API keys"
      onClose={onClose}
      footer={
        <button
          className={cx(primaryActionClass, "ml-auto")}
          type="submit"
          form="api-key-form"
          disabled={!canSave || busy !== null || !draft.trim()}
        >
          {busy === "save" ? "Checking…" : "Save key"}
        </button>
      }
    >
      <AccountSection title="OpenAI">
        <div className="flex min-h-12 items-center gap-2.5 rounded-xl border border-hairline px-4 py-2.5">
          <span
            className={cx(
              "h-1.5 w-1.5 flex-none rounded-full",
              openai?.configured ? "bg-accent" : "bg-text-muted",
            )}
            aria-hidden="true"
          />
          <span className="min-w-0 flex-1 text-13 text-text-primary">
            {keys ? keyStatusText(keys, local) : error ? "Key status unavailable" : "Checking…"}
          </span>
          {openai?.masked ? (
            <code className="flex-none font-mono text-12 text-text-muted">{openai.masked}</code>
          ) : null}
          {!local && openai?.configured ? (
            <button
              className={compactActionClass}
              type="button"
              onClick={() => void remove()}
              disabled={busy !== null}
            >
              {busy === "remove" ? "Removing…" : "Remove"}
            </button>
          ) : null}
        </div>
        {local && keys && !openai?.configured ? (
          <AccountNotice>
            Set <code className="font-mono text-12">OPENAI_API_KEY</code> in the server's{" "}
            <code className="font-mono text-12">.env</code> and restart it. Without a key,
            topic review, workspace builds and the assistant all fail.
          </AccountNotice>
        ) : null}
        {!local && keys && !openai?.configured && !allowance ? (
          <AccountNotice>
            {keys.platform_key
              ? "Builds, topic review and the assistant are refused until you add a key or the operator grants you an allowance."
              : "Builds, topic review and the assistant are refused until you add a key."}
          </AccountNotice>
        ) : null}
      </AccountSection>

      {allowance ? (
        <AccountSection title="Sponsored allowance" detail={allowanceText(allowance)}>
          <div className="h-1.5 overflow-hidden rounded-full bg-track" aria-hidden="true">
            <div
              className={cx("h-full rounded-full", allowance.exhausted ? "bg-error" : "bg-accent")}
              style={{
                width: `${Math.min(100, Math.round((allowance.spent_usd / Math.max(allowance.limit_usd, 0.01)) * 100))}%`,
              }}
            />
          </div>
        </AccountSection>
      ) : null}

      <AccountSection title={!local && openai?.configured ? "Replace the key" : "Add a key"}>
        <form
          id="api-key-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <input
            className={textInputClass}
            type="password"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="sk-…"
            autoComplete="off"
            spellCheck={false}
            aria-label="OpenAI API key"
            disabled={!canSave || busy !== null}
          />
        </form>
        {local ? (
          <AccountNotice>
            Saving is off in this build. Nothing you type here is sent anywhere. Change the key in
            the server's <code className="font-mono text-12">.env</code> for now.
          </AccountNotice>
        ) : keys && !keys.saving_enabled ? (
          <AccountNotice>Saving keys is not enabled on this server.</AccountNotice>
        ) : (
          <AccountNotice>
            Checked with OpenAI, then stored encrypted; only your own workspaces use it. The
            operator could decrypt it, so use a project key with a spending limit.
          </AccountNotice>
        )}
      </AccountSection>

      {!local && usage ? (
        <AccountSection title={`Usage, last ${usage.days} days`} detail={usageText(usage)}>
          {usage.recent.length > 0 ? (
            <ul className="m-0 grid list-none gap-1.5 p-0">
              {usage.recent.slice(0, RECENT_USAGE_ROWS).map((event, index) => (
                <li
                  className="flex items-baseline gap-3 text-12 text-text-secondary"
                  key={`${index}:${event.created_at ?? ""}`}
                >
                  <span className="flex-none text-text-muted">
                    {longDateLabel(event.created_at) ?? "Unknown date"}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{event.label ?? event.feature ?? event.model}</span>
                  <span className="flex-none tabular-nums">{usd(event.cost_usd)}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </AccountSection>
      ) : null}

      {error ? (
        <p className={cx(errorNoticeClass, "mt-5 mb-0")} role="alert">
          {error}
        </p>
      ) : null}
    </AccountDialog>
  );
}

function keyStatusText(keys: ApiKeysResult, local: boolean): string {
  if (local) {
    return keys.openai.configured ? "A key is configured on the server" : "No key is configured";
  }
  if (keys.openai.configured) {
    const added = longDateLabel(keys.openai.created_at);
    return added ? `Your key, added ${added}` : "Your key";
  }
  return "No key saved";
}

function allowanceText(allowance: AllowanceSummary): string {
  const period = allowance.period === "monthly" ? " this month" : "";
  const until = allowance.expires_at ? `, until ${longDateLabel(allowance.expires_at)}` : "";
  const used = `${usd(allowance.spent_usd)} of ${usd(allowance.limit_usd)} used${period}${until}.`;
  return allowance.exhausted ? `${used} Used up: add your own key to keep going.` : used;
}

function usageText(usage: UsageSummary): string {
  if (usage.calls === 0) return "No model calls yet.";
  const total = `${usd(usage.total_usd)} across ${pluralize(usage.calls, "model call")}`;
  if (usage.byok_usd > 0 && usage.sponsored_usd > 0) {
    return `${total} (${usd(usage.byok_usd)} on your key, ${usd(usage.sponsored_usd)} sponsored).`;
  }
  if (usage.sponsored_usd > 0) return `${total}, on your sponsored allowance.`;
  return `${total}, on your key.`;
}

const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

function usd(value: number): string {
  if (value > 0 && value < 0.01) return "<$0.01";
  return USD.format(value);
}
