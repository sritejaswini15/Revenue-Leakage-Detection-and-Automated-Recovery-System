"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";
import { useRequiredSession } from "../hooks/use-session";
import { humanize, initials } from "../lib/format";
import { clearSession, saveSession } from "../lib/session";
import { api } from "../services/api/recoverx";

const NAV_ITEMS = [
  { label: "Command center", href: "/", icon: "⌁" },
  { label: "Cases", href: "/cases", icon: "◫" },
  { label: "Escalations", href: "/escalations", icon: "!" },
  { label: "Transactions", href: "/transactions", icon: "₹" },
  { label: "Customers", href: "/customers", icon: "◉" },
  { label: "Analytics", href: "/analytics", icon: "◒" },
  { label: "Policies", href: "/policies", icon: "⌘" },
  { label: "Integrations", href: "/integrations", icon: "◈" },
  { label: "Users", href: "/users", icon: "☰", adminOnly: true },
  { label: "Simulator", href: "/simulator", icon: "▷" },
  { label: "Audit trail", href: "/audit", icon: "≡" },
  { label: "Account", href: "/account", icon: "◌" },
];

// Confirm the cached profile against the session cookie once per page load. A 401 signs the
// browser out (see the API client); a changed role or name is picked up here.
let verification: Promise<void> | null = null;

function verifySession() {
  verification ??= api.auth.me().then(
    (user) => saveSession(user),
    () => undefined,
  );
  return verification;
}

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function AppShell({ title, children }: Readonly<{ title: string; children: ReactNode }>) {
  const pathname = usePathname();
  const router = useRouter();
  const session = useRequiredSession();
  const signedIn = Boolean(session);

  useEffect(() => {
    if (signedIn) void verifySession();
  }, [signedIn]);

  if (!session) {
    return (
      <main className="login-page">
        <div className="loading-state" role="status">
          Authenticating RecoverX control plane…
        </div>
      </main>
    );
  }

  const name = session.user.name;
  const signOut = async () => {
    try {
      await api.auth.logout();
    } catch {
      // The session is cleared locally even if the server is unreachable.
    }
    verification = null;
    clearSession();
    router.replace("/login");
  };

  const visible = NAV_ITEMS.filter((item) => !item.adminOnly || session.user.role === "ADMIN");
  const navLinks = visible.map((item) => (
    <Link key={item.href} href={item.href} className={`nav-item ${isActive(pathname, item.href) ? "active" : ""}`}>
      <span className="nav-icon" aria-hidden>
        {item.icon}
      </span>
      {item.label}
    </Link>
  ));

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/">
          <span className="brand-mark">R</span>
          <span>
            recover<span className="brand-x">X</span>
          </span>
        </Link>
        <nav className="nav-list" aria-label="Main navigation">
          {navLinks}
        </nav>
        <div className="sidebar-bottom">
          <div className="user-chip">
            <span className="user-avatar">{initials(name)}</span>
            <span>
              <b>{name}</b>
              <small>{humanize(session.user.role)}</small>
            </span>
          </div>
        </div>
      </aside>
      <section className="content">
        <header className="topbar">
          <div className="mobile-brand">
            <span className="brand-mark">R</span> recover<span className="brand-x">X</span>
          </div>
          <div className="breadcrumb">
            Revenue operations <span>/</span> {title}
          </div>
          <div className="top-actions">
            <button type="button" onClick={signOut} className="text-button sign-out">
              Sign out
            </button>
            <Link className="top-avatar" href="/account" title={`${name}: account settings`}>
              {initials(name)}
            </Link>
          </div>
        </header>
        <nav className="mobile-nav" aria-label="Mobile navigation">
          {navLinks}
        </nav>
        <div className="page-wrap">{children}</div>
      </section>
    </main>
  );
}
