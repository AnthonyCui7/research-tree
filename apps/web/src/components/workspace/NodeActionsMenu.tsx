import { useState } from "react";
import { cx } from "../../lib/cx";
import { MenuItem, MenuSection, PopoverMenu, type MenuAnchor } from "../ui/PopoverMenu";
import { ArrowLeftIcon, ArrowRightIcon, PencilIcon, TrashIcon } from "../ui/icons";
import type {
  BranchTreeNode,
  PaperTreeNode,
  TreeViewModel,
  WorkspaceEditOperation,
} from "../../lib/types";

type NodeActionsMenuProps = {
  node: BranchTreeNode | PaperTreeNode;
  tree: TreeViewModel;
  anchor: MenuAnchor;
  /** True while a build owns the workspace; edits would race its publish. */
  disabled: boolean;
  onClose: () => void;
  onRename: (branchId: string) => void;
  onApply: (operations: WorkspaceEditOperation[]) => void;
};

type Destination = {
  key: string;
  label: string;
  branchId: string;
};

const BUILD_RUNNING = "Wait for the build to finish";

/**
 * What can be done to a card by hand. A branch can be renamed; a paper can be
 * moved onto another branch's reading path or taken out of the workspace. The
 * same menu opens from a right-click on the canvas and from the inspector.
 */
export function NodeActionsMenu({
  node,
  tree,
  anchor,
  disabled,
  onClose,
  onRename,
  onApply,
}: NodeActionsMenuProps) {
  const [choosingDestination, setChoosingDestination] = useState(false);
  const title = node.title;

  if (node.kind === "branch") {
    return (
      <PopoverMenu anchor={anchor} onClose={onClose} label={`Actions for ${title}`}>
        <MenuSection>
          <MenuItem
            icon={<PencilIcon className="h-[13px] w-[13px]" />}
            onClick={() => onRename(node.branchNodeId)}
            disabled={disabled}
            title={disabled ? BUILD_RUNNING : undefined}
          >
            Rename branch…
          </MenuItem>
        </MenuSection>
      </PopoverMenu>
    );
  }

  const destinations = moveDestinations(tree, node);
  return (
    <PopoverMenu anchor={anchor} onClose={onClose} label={`Actions for ${title}`} width={252}>
      {choosingDestination ? (
        <MenuSection>
          <button
            className={cx(rowClass, "text-text-secondary")}
            type="button"
            onClick={() => setChoosingDestination(false)}
            aria-label="Back"
          >
            <ArrowLeftIcon className="h-[13px] w-[13px] flex-none text-text-muted" />
            <span className="min-w-0 flex-1 truncate text-[11.5px] font-semibold tracking-[0.02em] uppercase">
              Move to
            </span>
          </button>
          {destinations.length === 0 ? (
            <p className="m-0 px-[9px] py-[7px] text-[12px] text-text-muted">
              No other branch to move it to.
            </p>
          ) : null}
          {destinations.map((destination) => (
            <MenuItem
              key={destination.key}
              disabled={disabled}
              title={disabled ? BUILD_RUNNING : undefined}
              onClick={() =>
                onApply([
                  {
                    op: "move",
                    entity_type: "paper_placement",
                    paper_id: node.paperId,
                    to_branch_id: destination.branchId,
                  },
                ])
              }
            >
              {destination.label}
            </MenuItem>
          ))}
        </MenuSection>
      ) : (
        <>
          <MenuSection>
            <button
              className={cx(rowClass, "text-text-primary enabled:hover:bg-surface-subtle disabled:cursor-not-allowed disabled:text-text-muted")}
              type="button"
              onClick={() => setChoosingDestination(true)}
              disabled={disabled}
              title={disabled ? BUILD_RUNNING : undefined}
              aria-haspopup="menu"
            >
              <span className="flex-none text-text-muted">
                <ArrowRightIcon className="h-[13px] w-[13px]" />
              </span>
              <span className="min-w-0 flex-1 truncate">Move to…</span>
            </button>
          </MenuSection>
          <MenuSection>
            <MenuItem
              tone="danger"
              icon={<TrashIcon className="h-[13px] w-[13px]" />}
              onClick={() =>
                onApply([{ op: "remove", entity_type: "paper_placement", paper_id: node.paperId }])
              }
              disabled={disabled}
              title={disabled ? BUILD_RUNNING : undefined}
            >
              Remove from workspace
            </MenuItem>
          </MenuSection>
        </>
      )}
    </PopoverMenu>
  );
}

const rowClass =
  "flex w-full items-center gap-[9px] rounded-[6px] border-0 bg-transparent px-[9px] py-[7px] text-left text-[13px] transition-[background-color] duration-150";

/**
 * Where a paper can go: every other branch that holds papers rather than
 * grouping other branches. The server places it at the end of that branch's
 * reading path.
 */
function moveDestinations(tree: TreeViewModel, paper: PaperTreeNode): Destination[] {
  const destinations: Destination[] = [];
  for (const candidate of tree.nodes) {
    if (candidate.kind !== "branch" || candidate.family === "group") continue;
    if (candidate.branchNodeId === paper.branchId) continue;
    destinations.push({
      key: candidate.branchNodeId,
      label: candidate.title,
      branchId: candidate.branchNodeId,
    });
  }
  return destinations;
}
