export type Role = "admin" | "organiser" | "monitor" | "reception" | "activity" | "distribution" | "observer";

export type Staff = { id: string; email: string; name: string; roles: Role[] };
export type Auth = { staff: Staff; csrf_token: string };
export type Person = {
  id: string; name: string; email: string; kind: "monitor" | "participant";
  team_id: string | null; team_name: string | null; active: boolean;
  membership_status: string; monitor_registration_approved: boolean;
  native_auto_imported: boolean; supervisely_user_id: number | null;
  supervisely_login: string | null;
  supervisely_role: string | null; native_verified_at: number | null;
  check_in_at: number | null; table_number: number | null;
  date_of_birth?: string; phone?: string; under_18?: string;
  image_consent?: string; data_consent?: string; food_allergies?: string;
};
export type Team = { id: string; name: string; type: string;
  member_source?: "monitor_pair" | "participants";
  monitor_person_id?: string | null; monitor_name?: string | null;
  table_number?: number | null; checked_in: number;
  first_check_in: number | null; members: Person[] };
export type TeamResponse = { teams: Team[]; totals: { people: number; monitors: number; competition_teams: number } };
export type PeopleResponse = { items: Person[]; total: number; page: number; size: number };
export type Integration = {
  settings: { server_url: string; team_id: number | null; workspace_id: number | null };
  credential_configured: boolean; verified: boolean; last_checked_at: string | null;
  last_error_code: string | null;
};

export async function api<T>(
  path: string, method = "GET", body?: unknown, csrfToken?: string
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch("/api/v1" + path, {
    method, headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (response.status === 204) return undefined as T;
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = result?.detail;
    const message = typeof detail === "string" ? detail :
      Array.isArray(detail) ? detail.map((e: { msg?: string }) => e.msg).join("; ") :
      "Request failed (" + response.status + ")";
    throw new Error(message);
  }
  return result as T;
}


export type SuperviselyRefresh = {
  status: "ok" | "archived" | "needs_review";
  matched: number; updated: number; created: number;
  unmatched_native: number; unmatched_roster: number;
  role_warnings: number; conflicts: number; native_mutations: boolean;
};

// Browser-wide coalescing: page changes reuse a recent result while an
// explicit Refresh button always checks Supervisely again.
let membershipRefreshPending: Promise<SuperviselyRefresh> | null = null;
let lastMembershipRefresh = 0;
export function refreshSuperviselyRoster(
  csrfToken: string, force = false
): Promise<SuperviselyRefresh | null> {
  if (membershipRefreshPending) return membershipRefreshPending;
  if (!force && Date.now() - lastMembershipRefresh < 60_000) {
    return Promise.resolve(null);
  }
  membershipRefreshPending = api<SuperviselyRefresh>(
    "/people/supervisely/refresh", "POST", undefined, csrfToken
  ).then(result => {
    lastMembershipRefresh = Date.now();
    return result;
  });
  return membershipRefreshPending.finally(() => { membershipRefreshPending = null; });
}
