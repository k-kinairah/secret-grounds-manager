"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { closeShift, fetchCurrentShift, fetchCurrentUser, logout, openShift } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/time";
import type { CashierShiftDetail, CurrentUser, UserRole } from "@/lib/types";
import { ToastProvider } from "./ToastProvider";

const ROLE_RANK: Record<UserRole, number> = {
  cashier: 1,
  admin: 2,
  owner: 3
};
const ADMIN_ONLY_PATHS = [
  "/operator-activity",
  "/operators",
  "/promotions",
  "/sales",
  "/settings",
  "/system",
  "/shifts",
  "/remote-commands"
];
// System-admin tier: role >= admin is NOT enough -- these additionally
// require the protected Admin account (user.is_protected). Backend mirror:
// server/auth.require_system_admin.
const SYSTEM_ADMIN_PATHS = ["/system", "/remote-commands"];
const ADMIN_NAV_ITEMS = [
  { href: "/history", icon: HistoryNavIcon, label: "History" },
  { href: "/operators", icon: UsersNavIcon, label: "Staff" },
  { href: "/operator-activity", icon: HistoryNavIcon, label: "Operator Activity" },
  { href: "/promotions", icon: PromotionsNavIcon, label: "Promotions" },
  { href: "/remote-commands", icon: CommandsNavIcon, label: "Remote Commands" },
  { href: "/settings", icon: SettingsNavIcon, label: "Settings" },
  { href: "/system", icon: SystemNavIcon, label: "System" }
];

function canAccess(user: CurrentUser | null, minimumRole: UserRole): boolean {
  return user !== null && ROLE_RANK[user.role] >= ROLE_RANK[minimumRole];
}

function canManageSystem(user: CurrentUser | null): boolean {
  return user?.is_protected === true;
}

export function AuthShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [shift, setShift] = useState<CashierShiftDetail | null>(null);
  const [isChecking, setIsChecking] = useState(true);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [shiftMessage, setShiftMessage] = useState<string | null>(null);
  const [isClosingShift, setIsClosingShift] = useState(false);
  const [isAdminMenuOpen, setIsAdminMenuOpen] = useState(false);
  const adminMenuRef = useRef<HTMLDivElement | null>(null);
  const adminButtonRef = useRef<HTMLButtonElement | null>(null);
  const isLoginPage = pathname === "/login";
  // Admin-level UI = role rank >= admin (owner or admin). Matches the
  // backend's require_role("admin") gate (Phase 1); the literal "admin"
  // username is no longer an authorization condition.
  const isAdminOperator = canAccess(user, "admin");
  const isAdminNavPath = ADMIN_NAV_ITEMS.some((item) => item.href === pathname);

  useEffect(() => {
    let isMounted = true;

    async function loadUser() {
      try {
        const currentUser = await fetchCurrentUser();
        if (!isMounted) {
          return;
        }
        setUser(currentUser);
        fetchCurrentShift()
          .then((currentShift) => {
            if (isMounted) {
              setShift(currentShift);
            }
          })
          .catch(() => {
            if (isMounted) {
              setShift(null);
            }
          });
        if (isLoginPage) {
          router.replace("/");
        } else if (ADMIN_ONLY_PATHS.includes(pathname) && !canAccess(currentUser, "admin")) {
          router.replace("/");
        } else if (SYSTEM_ADMIN_PATHS.includes(pathname) && !canManageSystem(currentUser)) {
          router.replace("/");
        }
      } catch {
        if (!isMounted) {
          return;
        }
        setUser(null);
        if (!isLoginPage) {
          const nextUrl = `${pathname}${window.location.search}`;
          router.replace(`/login?next=${encodeURIComponent(nextUrl)}`);
        }
      } finally {
        if (isMounted) {
          setIsChecking(false);
        }
      }
    }

    void loadUser();
    return () => {
      isMounted = false;
    };
  }, [isLoginPage, pathname, router]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setIsAdminMenuOpen(false), 0);
    return () => window.clearTimeout(timeoutId);
  }, [pathname]);

  useEffect(() => {
    if (!isAdminMenuOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      if (adminMenuRef.current && !adminMenuRef.current.contains(event.target as Node)) {
        setIsAdminMenuOpen(false);
      }
    }

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setIsAdminMenuOpen(false);
        adminButtonRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isAdminMenuOpen]);

  useEffect(() => {
    if (user === null || isLoginPage) {
      return;
    }

    let isMounted = true;
    const intervalId = window.setInterval(() => {
      fetchCurrentShift()
        .then((currentShift) => {
          if (isMounted) {
            setShift(currentShift);
          }
        })
        .catch(() => {
          if (isMounted) {
            setShift(null);
          }
        });
    }, 5000);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, [isLoginPage, user]);

  async function handleLogout() {
    if (isLoggingOut) {
      return;
    }
    setIsLoggingOut(true);
    try {
      await logout();
    } finally {
      setUser(null);
      setIsLoggingOut(false);
      router.replace("/login");
    }
  }

  async function handleChangeOperator() {
    if (isLoggingOut) {
      return;
    }
    setIsLoggingOut(true);
    try {
      await logout();
    } finally {
      setUser(null);
      setIsLoggingOut(false);
      router.replace("/login");
    }
  }

  async function handleShiftOpened(openedShift: CashierShiftDetail) {
    setShift(openedShift);
    setShiftMessage("Shift opened. Dashboard is ready.");
  }

  async function handleCloseShift(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (shift === null || isClosingShift) {
      return;
    }

    const formData = new FormData(event.currentTarget);
    const actualCash = String(formData.get("actualCash") ?? "");
    const closingNotes = String(formData.get("closingNotes") ?? "");
    const forceCloseReason = String(formData.get("forceCloseReason") ?? "");
    setIsClosingShift(true);
    setShiftMessage(null);
    try {
      await closeShift(shift.shift.shift_id, {
        actualCash,
        closingNotes,
        forceCloseReason
      });
      // The backend has already revoked this operator's auth sessions and
      // cleared the cookie on End Shift -- reflect the logged-out state
      // immediately instead of waiting for a later request to 401.
      setShift(null);
      setUser(null);
      router.replace("/login");
    } catch (error) {
      setShiftMessage(error instanceof Error ? error.message : "Could not close shift.");
    } finally {
      setIsClosingShift(false);
    }
  }

  if (isLoginPage) {
    return <>{children}</>;
  }

  return (
    <ToastProvider>
      <div className="appShellFrame">
        <nav className="appShell" aria-label="Main navigation">
        <Link className="appShell__brand" href="/">
          <img
            alt=""
            aria-hidden="true"
            className="appShell__mark"
            height={34}
            src="/secret-grounds-logo.png"
            width={38}
          />
          <span>SECRET GROUNDS</span>
        </Link>
        <div className="appShell__links">
          <Link className={pathname === "/" ? "appShell__linkActive" : ""} href="/">
            <DashboardNavIcon />
            Dashboard
          </Link>
          <Link className={pathname === "/members" ? "appShell__linkActive" : ""} href="/members">
            <UsersNavIcon />
            Members
          </Link>
          {isAdminOperator ? (
            <Link className={pathname === "/sales" ? "appShell__linkActive" : ""} href="/sales">
              <SalesNavIcon />
              Sales
            </Link>
          ) : null}
          {isAdminOperator ? (
            <div
              className="appShell__adminMenu"
              onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                  setIsAdminMenuOpen(false);
                }
              }}
              ref={adminMenuRef}
            >
              <button
                aria-expanded={isAdminMenuOpen}
                aria-haspopup="menu"
                className={isAdminNavPath ? "appShell__linkActive" : ""}
                onClick={() => setIsAdminMenuOpen((open) => !open)}
                ref={adminButtonRef}
                type="button"
              >
                <AdminNavIcon />
                Admin
                <span className="appShell__chevron" aria-hidden="true" />
              </button>
              {isAdminMenuOpen ? (
                <div className="appShell__adminDropdown" role="menu">
                  {ADMIN_NAV_ITEMS.filter(
                    (item) => !SYSTEM_ADMIN_PATHS.includes(item.href) || canManageSystem(user)
                  ).map((item) => {
                    const Icon = item.icon;
                    return (
                      <Link
                        aria-current={pathname === item.href ? "page" : undefined}
                        className={pathname === item.href ? "appShell__linkActive" : ""}
                        href={item.href}
                        key={item.href}
                        onClick={() => setIsAdminMenuOpen(false)}
                        role="menuitem"
                      >
                        <Icon />
                        {item.label}
                      </Link>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="appShell__account">
          {user ? (
            <>
              <span>
                On Duty: {user.display_name}
                {canAccess(user, "admin") ? " · Admin" : ""}
              </span>
              {shift ? (
                <span className="appShell__shift">
                  Shift Open - {formatDateTime(shift.shift.opened_at)}
                </span>
              ) : null}
              <button disabled={isLoggingOut} onClick={handleLogout} type="button">
                <LogOutActionIcon />
                {isLoggingOut ? "Signing out..." : "Sign out"}
              </button>
              <button disabled={isLoggingOut} onClick={handleChangeOperator} type="button">
                <UsersActionIcon />
                Change Operator
              </button>
            </>
          ) : (
            <span>Checking access...</span>
          )}
        </div>
        </nav>
      </div>
      {isChecking ? (
        <main className="authLoading" aria-live="polite">
          <img
            alt=""
            aria-hidden="true"
            className="authLoading__logo"
            height={64}
            src="/secret-grounds-logo.png"
            width={72}
          />
          <span>Loading Secret Grounds Manager...</span>
        </main>
      ) : (
        <>
          {shiftMessage ? <div className="shiftNotice">{shiftMessage}</div> : null}
          {shift ? (
            <form className="shiftCloseBar" onSubmit={handleCloseShift}>
              <strong>End Shift</strong>
              <span>Expected now: {formatMoney(shift.totals.expected_cash)}</span>
              <input
                aria-label="Actual drawer cash"
                name="actualCash"
                placeholder="Actual cash"
                required
              />
              <input
                aria-label="Closing notes"
                name="closingNotes"
                placeholder="Closing notes"
              />
              {canAccess(user, "admin") ? (
                <input
                  aria-label="Force close reason"
                  name="forceCloseReason"
                  placeholder="Force-close reason if needed"
                />
              ) : null}
              <button disabled={isClosingShift} type="submit">
                {isClosingShift ? "Closing..." : "End Shift"}
              </button>
            </form>
          ) : null}
          {children}
        </>
      )}
    </ToastProvider>
  );
}

function DashboardNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M4 4h7v7H4z" />
      <path d="M13 4h7v7h-7z" />
      <path d="M4 13h7v7H4z" />
      <path d="M13 13h7v7h-7z" />
    </svg>
  );
}

function UsersNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M16 11a3 3 0 1 0-2.8-4" />
      <path d="M8.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" />
      <path d="M3 20c.7-3 2.6-5 5.5-5s4.8 2 5.5 5" />
      <path d="M14.5 15.3c2.8.2 4.6 1.9 5.5 4.7" />
    </svg>
  );
}

function HistoryNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M4 12a8 8 0 1 0 2.3-5.7" />
      <path d="M4 5v5h5" />
      <path d="M12 8v5l3 2" />
    </svg>
  );
}

function SalesNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M6 3h12v18l-2-1.2-2 1.2-2-1.2-2 1.2-2-1.2L6 21V3z" />
      <path d="M9 7h6" />
      <path d="M9 11h6" />
      <path d="M9 15h3" />
    </svg>
  );
}

function PromotionsNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M6 18 18 6" />
      <path d="M7.5 10a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z" />
      <path d="M16.5 19a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z" />
    </svg>
  );
}

function CommandsNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M4 5h16v10H4z" />
      <path d="M8 19h8" />
      <path d="M12 15v4" />
      <path d="m8 10 2-2-2-2" />
      <path d="M12 12h4" />
    </svg>
  );
}

function SettingsNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M4 6h16" />
      <path d="M9 8.1a2.1 2.1 0 1 0 0-4.2 2.1 2.1 0 0 0 0 4.2z" />
      <path d="M4 12h16" />
      <path d="M16 14.1a2.1 2.1 0 1 0 0-4.2 2.1 2.1 0 0 0 0 4.2z" />
      <path d="M4 18h16" />
      <path d="M12 20.1a2.1 2.1 0 1 0 0-4.2 2.1 2.1 0 0 0 0 4.2z" />
    </svg>
  );
}

function SystemNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M4 5h16v14H4z" />
      <path d="M8 9h8" />
      <path d="M8 13h5" />
      <path d="M17 13h.01" />
    </svg>
  );
}

function AdminNavIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M12 3 19 6v5c0 4.4-2.8 7.5-7 10-4.2-2.5-7-5.6-7-10V6l7-3z" />
      <path d="m9.5 12 1.8 1.8 3.7-4" />
    </svg>
  );
}

function LogOutActionIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

function UsersActionIcon() {
  return (
    <svg aria-hidden="true" className="appShell__navIcon" fill="none" viewBox="0 0 24 24">
      <path d="M16 11a3 3 0 1 0-2.8-4" />
      <path d="M8.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" />
      <path d="M3 20c.7-3 2.6-5 5.5-5s4.8 2 5.5 5" />
      <path d="M14.5 15.3c2.8.2 4.6 1.9 5.5 4.7" />
    </svg>
  );
}

function StartShiftGate({
  message,
  onOpened
}: {
  message: string | null;
  onOpened: (shift: CashierShiftDetail) => void;
}) {
  const [openingCash, setOpeningCash] = useState("0.00");
  const [error, setError] = useState<string | null>(null);
  const [isOpening, setIsOpening] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isOpening) {
      return;
    }

    setIsOpening(true);
    setError(null);
    try {
      onOpened(await openShift(openingCash));
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Could not open shift.");
    } finally {
      setIsOpening(false);
    }
  }

  return (
    <main className="shiftGate">
      <section className="shiftGate__panel" aria-labelledby="start-shift-title">
        <p className="shiftGate__eyebrow">Cash Drawer</p>
        <h1 id="start-shift-title">Start Shift</h1>
        <p>Enter the opening drawer cash to track optional shift totals.</p>
        {message ? <div className="shiftGate__message">{message}</div> : null}
        {error ? <div className="shiftGate__error">{error}</div> : null}
        <form onSubmit={handleSubmit}>
          <label>
            Opening cash
            <input
              inputMode="decimal"
              onChange={(event) => setOpeningCash(event.target.value)}
              placeholder="1000.00"
              required
              value={openingCash}
            />
          </label>
          <button disabled={isOpening} type="submit">
            {isOpening ? "Opening..." : "Open Shift"}
          </button>
        </form>
      </section>
    </main>
  );
}
