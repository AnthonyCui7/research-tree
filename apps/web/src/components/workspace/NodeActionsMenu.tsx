import { useState } from "react";
import { kickerClass } from "../../lib/controlClasses";
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
  const label = `Actions for ${node.title}`;
  const disabledTitle = disabled ? BUILD_RUNNING : undefined;

  if (node.kind === "branch") {
    return (
      <PopoverMenu anchor={anchor} onClose={onClose} label={label}>
        <MenuSection>
          <MenuItem
            icon={<PencilIcon className="size-4" />}
            onClick={() => onRename(node.branchNodeId)}
            disabled={disabled}
            title={disabledTitle}
          >
            Rename branch…
          </MenuItem>
        </MenuSection>
      </PopoverMenu>
    );
  }

  if (choosingDestination) {
    const destinations = moveDestinations(tree, node);
    return (
      <PopoverMenu anchor={anchor} onClose={onClose} label={`Move ${node.title} to`} width={260}>
        <MenuSection>
          <MenuItem
            icon={<ArrowLeftIcon className="size-4" />}
            onClick={() => setChoosingDestination(false)}
            keepsMenuOpen
          >
            <span className={kickerClass}>Move to</span>
          </MenuItem>
          {destinations.length === 0 ? (
            <p className="m-0 px-2.5 py-2 text-12 text-text-muted">
              No other branch to move it to.
            </p>
          ) : null}
          {destinations.map((destination) => (
            <MenuItem
              key={destination.branchNodeId}
              disabled={disabled}
              title={disabledTitle}
              onClick={() =>
                onApply([
                  {
                    op: "move",
                    entity_type: "paper_placement",
                    paper_id: node.paperId,
                    to_branch_id: destination.branchNodeId,
                  },
                ])
              }
            >
              {destination.title}
            </MenuItem>
          ))}
        </MenuSection>
      </PopoverMenu>
    );
  }

  return (
    <PopoverMenu anchor={anchor} onClose={onClose} label={label} width={240}>
      <MenuSection>
        <MenuItem
          icon={<ArrowRightIcon className="size-4" />}
          onClick={() => setChoosingDestination(true)}
          disabled={disabled}
          title={disabledTitle}
          keepsMenuOpen
        >
          Move to…
        </MenuItem>
      </MenuSection>
      <MenuSection>
        <MenuItem
          tone="danger"
          icon={<TrashIcon className="size-4" />}
          onClick={() =>
            onApply([{ op: "remove", entity_type: "paper_placement", paper_id: node.paperId }])
          }
          disabled={disabled}
          title={disabledTitle}
        >
          Remove from workspace
        </MenuItem>
      </MenuSection>
    </PopoverMenu>
  );
}

/**
 * Where a paper can go: every other branch that holds papers rather than
 * grouping other branches. The server places it on that branch's reading path
 * at the point its publication date gives it.
 */
function moveDestinations(tree: TreeViewModel, paper: PaperTreeNode): BranchTreeNode[] {
  return tree.nodes.filter(
    (candidate): candidate is BranchTreeNode =>
      candidate.kind === "branch" &&
      candidate.family !== "group" &&
      candidate.branchNodeId !== paper.branchId,
  );
}
