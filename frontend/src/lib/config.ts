/**
 * Frontend configuration.
 *
 * Defaults come from `conf/conf.json` (bundled at build time). The
 * `NEXT_PUBLIC_*` environment variables override them at build time, which
 * keeps deployment-specific values out of the committed file.
 */

import conf from "../../conf/conf.json";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? conf.api_url;
export const API_PREFIX = `${API_URL}${
  process.env.NEXT_PUBLIC_API_PREFIX ?? conf.api_prefix
}`;
export const DOCUMENTS_POLL_INTERVAL_MS = conf.documents_poll_interval_ms;

/**
 * When false, the (future) login UI is skipped. It does NOT disable backend
 * authorization — protected endpoints still require a Bearer token.
 */
export const OAUTH_ENABLED =
  process.env.NEXT_PUBLIC_OAUTH_ENABLED === "true" || conf.oauth_enabled;

/**
 * Local-development token, injected via `frontend/.env.local` (never commit
 * it). Empty in normal builds.
 */
export const DEV_ACCESS_TOKEN = process.env.NEXT_PUBLIC_DEV_ACCESS_TOKEN ?? "";
