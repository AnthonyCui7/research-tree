type TopBarProps = {
  workspaceTitle: string;
};

export function TopBar({ workspaceTitle }: TopBarProps) {
  return (
    <header className="top-bar">
      <div className="top-bar-title">
        <span className="eyebrow">Workspace</span>
        <strong>{workspaceTitle}</strong>
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
