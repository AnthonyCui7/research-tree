import { AccountDialog } from "./AccountDialog";

/**
 * Deliberately empty. The documentation this points at has not been written, and
 * a page of invented links would be worse than an honest blank.
 */
export function HelpDialog({ onClose }: { onClose: () => void }) {
  return (
    <AccountDialog title="Help & docs" onClose={onClose}>
      {/* Centred between the title and the foot: the header leaves 32px and
          the body 24px, so 8px more below evens them. */}
      <div className="grid place-items-center px-4 pt-14 pb-16 text-center">
        <p className="m-0 text-13 text-text-secondary text-trim">
          Documentation is not written yet.
        </p>
      </div>
    </AccountDialog>
  );
}
