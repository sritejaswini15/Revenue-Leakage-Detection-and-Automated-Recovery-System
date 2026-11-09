// Browser calls go to this same-origin prefix; next.config.ts proxies it to the backend, so the
// HttpOnly session cookie stays first-party and no API token is ever readable by scripts.
export const API_BASE = "/api";

export const IS_DEVELOPMENT = process.env.NODE_ENV !== "production";
