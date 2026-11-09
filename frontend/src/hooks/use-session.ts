"use client";

import { useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";
import { readSession, subscribe, type Session } from "../lib/session";

const unknownOnServer = () => undefined;

/**
 * The signed-in session: `undefined` while unknown (server render and hydration),
 * `null` when signed out. Hydration-safe because the server snapshot is fixed.
 */
export function useSession(): Session | null | undefined {
  return useSyncExternalStore<Session | null | undefined>(subscribe, readSession, unknownOnServer);
}

/** Like useSession, but sends signed-out visitors to the login page. */
export function useRequiredSession(): Session | null | undefined {
  const session = useSession();
  const router = useRouter();
  useEffect(() => {
    if (session === null) router.replace("/login");
  }, [session, router]);
  return session;
}
