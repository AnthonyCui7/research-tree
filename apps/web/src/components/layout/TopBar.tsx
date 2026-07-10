type TopBarProps = {
  workspaceTitle: string;
  versionHash: string | null;
  versionCount: number;
};

export function TopBar({ workspaceTitle, versionHash, versionCount }: TopBarProps) {
  return (
    <header className="top-bar">
      <div className="top-bar-title">
        <span className="eyebrow">Workspace</span>
        <strong>{workspaceTitle}</strong>
        <span className="version-line">
          {versionHash ? `current ${versionHash.slice(0, 10)}` : "unversioned"}
          {versionCount > 0 ? ` / ${versionCount} saved versions` : ""}
        </span>
      </div>
      <div className="top-bar-actions">
        <label className="search-control">
          <span className="sr-only">Search workspace</span>
          <input type="search" placeholder="Search papers, branches, paths" />
          <kbd>Ctrl K</kbd>
        </label>
        <button className="help-button" type="button" aria-label="Open help">
          ?
        </button>
      </div>
    </header>
  );
}
