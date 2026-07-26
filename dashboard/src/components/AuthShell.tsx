"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { closeShift, fetchCurrentShift, fetchCurrentUser, logout, openShift } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/time";
import type { CashierShiftDetail, CurrentUser, UserRole } from "@/lib/types";

const ROLE_RANK: Record<UserRole, number> = {
  cashier: 1,
  admin: 2,
  owner: 3
};

function canAccess(user: CurrentUser | null, minimumRole: UserRole): boolean {
  return user !== null && ROLE_RANK[user.role] >= ROLE_RANK[minimumRole];
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
  const isLoginPage = pathname === "/login";

  useEffect(() => {
    let isMounted = true;

    async function loadUser() {
      try {
        const currentUser = await fetchCurrentUser();
        if (!isMounted) {
          return;
        }
        setUser(currentUser);
        const currentShift = await fetchCurrentShift();
        if (!isMounted) {
          return;
        }
        setShift(currentShift);
        if (isLoginPage) {
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
      const closed = await closeShift(shift.shift.shift_id, {
        actualCash,
        closingNotes,
        forceCloseReason
      });
      setShift(null);
      setShiftMessage(
        `Shift closed. Difference: ${formatMoney(closed.shift.difference ?? "0.00")}.`
      );
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
    <>
      <nav className="appShell" aria-label="Main navigation">
        <Link className="appShell__brand" href="/">
          <span className="appShell__mark" aria-hidden="true">
            SG
          </span>
          <span>Secret Grounds</span>
        </Link>
        <div className="appShell__links">
          <Link className={pathname === "/" ? "appShell__linkActive" : ""} href="/">Dashboard</Link>
          <Link className={pathname === "/history" ? "appShell__linkActive" : ""} href="/history">History</Link>
          <Link className={pathname === "/remote-commands" ? "appShell__linkActive" : ""} href="/remote-commands">Commands</Link>
          <Link className={pathname === "/members" ? "appShell__linkActive" : ""} href="/members">Members</Link>
          <Link className={pathname === "/shifts" ? "appShell__linkActive" : ""} href="/shifts">Shifts</Link>
          {canAccess(user, "admin") ? <Link className={pathname === "/rates" ? "appShell__linkActive" : ""} href="/rates">Rates</Link> : null}
          {canAccess(user, "owner") ? <Link className={pathname === "/users" ? "appShell__linkActive" : ""} href="/users">Users</Link> : null}
        </div>
        <div className="appShell__account">
          {user ? (
            <>
              <span>
                {user.display_name} - {user.role}
              </span>
              {shift ? (
                <span className="appShell__shift">
                  Shift Open - {formatDateTime(shift.shift.opened_at)}
                </span>
              ) : null}
              <button disabled={isLoggingOut} onClick={handleLogout} type="button">
                {isLoggingOut ? "Signing out..." : "Sign out"}
              </button>
            </>
          ) : (
            <span>Checking access...</span>
          )}
        </div>
      </nav>
      {isChecking ? (
        <main className="authLoading" aria-live="polite">
          Loading Secret Grounds Manager...
        </main>
      ) : user?.role === "cashier" && shift === null && pathname !== "/shifts" ? (
        <StartShiftGate message={shiftMessage} onOpened={handleShiftOpened} />
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
    </>
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
        <p>
          Enter the opening drawer cash before recording payments or managing sessions.
        </p>
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
