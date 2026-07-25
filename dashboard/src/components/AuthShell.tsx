"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { fetchCurrentUser, logout } from "@/lib/api";
import type { CurrentUser, UserRole } from "@/lib/types";

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
  const [isChecking, setIsChecking] = useState(true);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
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

  if (isLoginPage) {
    return <>{children}</>;
  }

  return (
    <>
      <nav className="appShell" aria-label="Main navigation">
        <div className="appShell__links">
          <Link href="/">Dashboard</Link>
          <Link href="/history">History</Link>
          {canAccess(user, "admin") ? <Link href="/rates">Rates</Link> : null}
          {canAccess(user, "owner") ? <Link href="/users">Users</Link> : null}
        </div>
        <div className="appShell__account">
          {user ? (
            <>
              <span>
                {user.display_name} - {user.role}
              </span>
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
      ) : (
        children
      )}
    </>
  );
}
