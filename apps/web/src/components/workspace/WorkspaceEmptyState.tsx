type WorkspaceEmptyStateProps = {
  title: string;
  detail: string;
  tone?: "neutral" | "error";
  actionLabel?: string;
  onAction?: () => void;
};

export function WorkspaceEmptyState({
  title,
  detail,
  tone = "neutral",
  actionLabel,
  onAction,
}: WorkspaceEmptyStateProps) {
  return (
    <div className="workspace-empty-state" data-tone={tone}>
      <div>
          <h2>{title}</h2>
          <p>{detail}</p>
        {actionLabel && onAction ? <button className="primary-action" type="button" onClick={onAction}>{actionLabel}</button> : null}
      </div>
    </div>
  );
}
