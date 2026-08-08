import { useEffect, useRef, useState } from "react";
import { LogIn, LogOut, UserRound } from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import { IDLE_LIMIT_MS } from "../lib/idleSignOut";

/**
 * Signed-in indicator for the public nav.
 *
 * The landing page carries its own palette (lp-* classes) rather than the
 * shadcn tokens the dashboard uses, so this component styles itself inline —
 * it can then be dropped into either surface without inheriting the wrong
 * theme. Signed out it is a plain "Sign in" button; signed in it becomes an
 * avatar that opens a small menu with the account and a sign-out action.
 */

const IDLE_MINUTES = Math.round(IDLE_LIMIT_MS / 60_000);

export function AccountMenu() {
  const { user, loading, signInWithGoogle, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // Avoid flashing "Sign in" before Firebase restores an existing session.
  if (loading) {
    return <div style={{ width: 34, height: 34 }} aria-hidden="true" />;
  }

  if (!user) {
    return (
      <button
        type="button"
        onClick={() => void signInWithGoogle()}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 14px",
          fontSize: 13,
          fontWeight: 500,
          color: "rgba(255,255,255,0.85)",
          background: "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.14)",
          borderRadius: 8,
          cursor: "pointer",
          whiteSpace: "nowrap",
        }}
      >
        <LogIn style={{ width: 14, height: 14 }} />
        Sign in
      </button>
    );
  }

  const label = user.displayName || user.email || "Account";
  const initial = label.trim().charAt(0).toUpperCase() || "?";

  return (
    <div ref={rootRef} style={{ position: "relative", flexShrink: 0 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account: ${label}`}
        title={label}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 34,
          height: 34,
          padding: 0,
          borderRadius: "50%",
          overflow: "hidden",
          background: "rgba(255,255,255,0.08)",
          border: "1px solid rgba(124,58,237,0.55)",
          color: "#fff",
          cursor: "pointer",
        }}
      >
        {user.photoURL ? (
          <img
            src={user.photoURL}
            alt=""
            referrerPolicy="no-referrer"
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          <span style={{ fontSize: 13, fontWeight: 600 }}>{initial}</span>
        )}
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            minWidth: 220,
            padding: 8,
            borderRadius: 10,
            background: "rgba(17,18,24,0.98)",
            border: "1px solid rgba(255,255,255,0.12)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
            zIndex: 100,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 8px 10px",
              borderBottom: "1px solid rgba(255,255,255,0.08)",
            }}
          >
            <UserRound style={{ width: 14, height: 14, color: "rgba(255,255,255,0.5)" }} />
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: 13,
                  color: "#fff",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {user.displayName || "Signed in"}
              </div>
              {user.email && (
                <div
                  style={{
                    fontSize: 11,
                    color: "rgba(255,255,255,0.45)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {user.email}
                </div>
              )}
            </div>
          </div>

          <p
            style={{
              margin: "8px 8px 6px",
              fontSize: 10.5,
              lineHeight: 1.4,
              color: "rgba(255,255,255,0.4)",
            }}
          >
            Signed out automatically after {IDLE_MINUTES} minutes of inactivity.
          </p>

          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void logout();
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              width: "100%",
              padding: "8px",
              fontSize: 13,
              color: "rgba(255,255,255,0.85)",
              background: "transparent",
              border: "none",
              borderRadius: 6,
              cursor: "pointer",
              textAlign: "left",
            }}
          >
            <LogOut style={{ width: 14, height: 14 }} />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
