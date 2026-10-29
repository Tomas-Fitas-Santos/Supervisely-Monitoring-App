# Email invitation onboarding

Teams are native Supervisely resources created before their members accept. A new additive `team_onboarding` table stores requested manager email, required memberships, per-member state, observed native IDs/roles and check time. Existing event records are retained.

## State and safety

- **Not created:** CSV registration exists; no remote team has been created.
- **Pending invitations:** the team exists and one or more required memberships await acceptance. This is a normal result; other setup remains available.
- **Ready:** all annotators and the monitor have confirmed accepted memberships and appropriate roles. Monitor access in the monitoring team is also confirmed. The organiser's existing Admin role fulfils Manager responsibility without being downgraded.
- **Needs attention:** a definitive invitation error, missing/deactivated membership, role/identity mismatch or unavailable membership check. The individual record explains what needs checking.
- **Uncertain remote write:** setup checkpoints retain the native team ID and block blind repetition until inspection. The app does not automatically recreate a team or resend an uncertain invitation.

CSV supports one or two annotators per team and a separate `monitor_email`. A monitor may supervise several teams; an annotator cannot be registered to multiple teams or act as a monitor in the same event. Ordinary two-annotator event teams remain supported. The two-team demonstration has one annotator per team.

Refreshing membership is a read operation and does not send invitations. Explicit retry targets known unsent/rejected invitations. Accepted IDs are persisted only after native membership confirmation. Native API checks are repeated before distribution and job release, so a stale Ready badge cannot alone authorise work. Monitor handover updates both native reviewer jobs and onboarding records.

## Native integration and evidence

The current Supervisely interface accepts a login **or email**, as shown in the supplied screenshot. Its public frontend code uses `/api/groups/{teamId}/members/add` with a `login` identifier, role ID and member type, and distinguishes `activated`, `disabledAt` and `inviteEmail` in membership records. That UI operation is separate from instance-wide account searching.

The app uses the SDK's existing public **`members.add`** operation with the CSV email in its identifier field, a target team ID and a dynamically resolved role ID. This avoids browser-cookie authentication and does not enumerate global users. Each write is attempted once; an uncertain result is not silently retried. This email use of the public route needs verification on the actual instance; the SDK method's older documentation calls the field a login. The code does not assume that its name proves email support on every deployment.

Membership refresh uses raw **`members.list`**, because SDK `UserInfo` discards invitation metadata. The official OpenAPI describes `showInvited` (default true, includes not-yet-activated users) and `showUnregistered`. The app reads both invited-inclusive and accepted-only lists. Merely obtaining a user ID does not establish acceptance. It retains invitation-to-user association if the returned accepted profile hides email, and rejects conflicting identity/role information.

If the public invitation route is unavailable or rejects email on an instance, the created team remains visible with the native failure. Use **Open native invitations**, explicitly select the named team/ID, invite its CSV email in Supervisely, then refresh membership in the monitor app. No password, participant API token or global directory privilege is required by this fallback. The app never substitutes a guessed username. Seat/plan limits or missing native permissions remain visible errors; it does not purchase seats or change plans.

Primary references:

- API reference: https://api.docs.supervisely.com/ ; specification: https://api.docs.supervisely.com/openapi.json
- SDK user operations: https://supervisely.readthedocs.io/latest/sdk/supervisely.api.user_api.UserApi.html
- Invitation/acceptance guide: https://docs.supervisely.com/getting-started/invite-member
- Member roles: https://docs.supervisely.com/collaboration/members

## Validation boundary

Automated tests cover actual application code with native-shaped pending/accepted records, denied global user search, same-ID pending users, definitive and uncertain failures, idempotent import/refresh, role loss, monitor transfer, even image distribution, nested uploads and native-shaped job ownership. The browser walkthrough covers pending teams, simulated acceptance, upload, allocation and review.

No live Supervisely credential is supplied to the development tests. Actual email delivery, public-route email acceptance, accepted-only filtering, hidden-email association, role permissions, native jobs and licensing require the real-account pilot. Online presence is not implemented.
