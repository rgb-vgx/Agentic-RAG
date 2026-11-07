/** Authorization headers shared by the REST and SSE clients. */

import { DEV_ACCESS_TOKEN } from "./config";

export function authHeaders(): Record<string, string> {
  return DEV_ACCESS_TOKEN
    ? { Authorization: `Bearer ${DEV_ACCESS_TOKEN}` }
    : {};
}
