/** Authorization headers shared by the REST and SSE clients. */

import { DEV_ACCESS_TOKEN } from "./config";
import { getAccessToken } from "./oauth";

export function authHeaders(): Record<string, string> {
  const token = getAccessToken() || DEV_ACCESS_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}
