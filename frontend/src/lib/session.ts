import type { SessionUser } from "../types/api";

// The credential is an HttpOnly cookie that scripts cannot read. Only the signed-in user's
// public profile is kept here, so every tab can render immediately; the server re-checks it
// on load (GET /auth/me) and on every request.
const USER_KEY = "recoverx_user";
const LEGACY_TOKEN_KEY = "recoverx_access_token"; // removed: tokens are no longer stored by scripts
const CHANGE_EVENT = "recoverx:session";

export type Session = { user: SessionUser };

let cachedRaw: string | null = null;
let cachedSession: Session | null = null;

/** Stable snapshot for useSyncExternalStore: the same object until storage changes. */
export function readSession(): Session | null {
  window.localStorage.removeItem(LEGACY_TOKEN_KEY);
  const raw = window.localStorage.getItem(USER_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    const user = parseUser(raw);
    cachedSession = user ? { user } : null;
  }
  return cachedSession;
}

export function saveSession(user: SessionUser) {
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function clearSession() {
  window.localStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribe(onChange: () => void) {
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

function parseUser(raw: string | null): SessionUser | null {
  try {
    return raw ? (JSON.parse(raw) as SessionUser) : null;
  } catch {
    return null;
  }
}
