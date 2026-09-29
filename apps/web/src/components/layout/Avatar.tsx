import { useState } from "react";
import { cx } from "../../lib/cx";
import type { SessionUser } from "../../lib/types";

type AvatarProps = {
  user: SessionUser;
  size: number;
  className?: string;
};

/** A picture when the account has one, otherwise initials on the accent tint. */
export function Avatar({ user, size, className }: AvatarProps) {
  const [broken, setBroken] = useState(false);
  // Initials at 40% of the circle: 11px in the sidebar's 28px avatar and 13px
  // in the menu's 32px one, both sizes from the type scale.
  const style = { width: size, height: size, fontSize: Math.round(size * 0.4) };
  if (user.avatar_url && !broken) {
    return (
      <img
        className={cx("flex-none rounded-full object-cover", className)}
        style={style}
        src={user.avatar_url}
        alt=""
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <span
      className={cx(
        "grid flex-none place-items-center rounded-full bg-accent-subtle leading-none font-bold text-accent-deep",
        className,
      )}
      style={style}
      aria-hidden="true"
    >
      {initials(user)}
    </span>
  );
}

export function displayName(user: SessionUser): string {
  if (user.name && user.name.trim()) return user.name.trim();
  if (user.email) return user.email.split("@")[0] ?? user.email;
  return "Local profile";
}

/** The line under the name: the address, or that the local build has no accounts. */
export function accountDetail(user: SessionUser, local: boolean): string {
  return local ? "Runs without accounts" : user.email;
}

export function initials(user: SessionUser): string {
  const source = displayName(user);
  if (source === "Local profile") return "RT";
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "";
  const second = parts[1]?.[0] ?? "";
  const letters = first && second ? `${first}${second}` : source.slice(0, 2);
  return letters.toUpperCase();
}
