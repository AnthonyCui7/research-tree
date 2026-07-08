type WorkspaceEmptyStateProps = {
  title: string;
  detail: string;
  tone?: "neutral" | "error";
};

export function WorkspaceEmptyState({
  title,
  detail,
  tone = "neutral",
}: WorkspaceEmptyStateProps) {
  return (
    <div className="workspace-empty-state" data-tone={tone}>
      <div>
        <h2>{title}</h2>
        <p>{detail}</p>
      </div>
    </div>
  );
}
