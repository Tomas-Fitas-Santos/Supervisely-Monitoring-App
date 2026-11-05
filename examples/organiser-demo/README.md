# Live DEMO: one team, explicit CSV roles

The DEMO uses the real Supervisely API with **one pre-existing native team**
and its **pre-existing workspace**. It does not create Supervisely teams.
The organiser account is **tomas.dos.santos@tecnico.ulisboa.pt**;
connect with the organiser account's own numeric Supervisely User ID and
personal token. The organiser must already be an Admin of the chosen team.
The organiser is not a participant in the DEMO pair.

## Role-aware DEMO roster

[registrations.csv](registrations.csv) contains one two-person competition
pair, `DEMO PAIR`. Each member has explicit role `participant_monitor`;
this means each must receive the **Reviewer** native team role while also
participating in labeling. The monitoring app registers accepted members with
the app-level monitor role, and DEMO can assign one to supervise the pair.

| Registered person | Event role | Requested native role |
| --- | --- | --- |
| tomas.gaming.21@gmail.com | Participant + monitor | reviewer |
| tomas.santos.work2002@gmail.com | Participant + monitor | reviewer |
| Organiser's Técnico account | Organiser | Existing admin (never changed) |

**Important:** Supervisely invites *existing account logins*, which may
differ from their email addresses. If an email-only invitation returns 404,
find each participant's **actual Supervisely login** under
Supervisely → Team Members or that person's account profile, and populate
`member_1_login` and `member_2_login` in the CSV. Do not guess the
login from the email address. The app verifies a supplied login before
sending the invitation, and rejects an exposed account-email mismatch.

## Steps

1. Update the branch, then launch `Start-App.ps1 -Mode Demo`.
2. Connect the organiser's Técnico account through the app.
3. Open **Settings**, verify and save the existing Team ID and Workspace ID.
4. Open **Monitors & Teams → Load bundled demo CSV**.
   If actual Supervisely logins differ from emails, edit the local copy of
   the CSV and choose that CSV file instead.
5. Review each person's displayed **event role** and **native role**.
   Click **Send invitations with CSV roles** only once.
6. Each person accepts the real team invitation in Supervisely.
   Click **Refresh accepted memberships** to verify identity and effective
   native role. Pending invitations are not automatically resent.
7. Confirm both participants are accepted as `reviewer` in Supervisely,
   and both are registered as app monitors.
8. Test attendance, source folder-or-ZIP staging, explicit Supervisely upload
   and prepared job configuration.

`participant` requests native `annotator`. `monitor` requests native
`manager` (from `monitor_email` for a separate supervisor).
`participant_monitor` requests native `reviewer` (labeling and review) and carries both
event responsibilities. CSV cannot request native Admin, and re-import
never silently changes a pending or accepted native role. Conflicts
require deliberate Admin review.

No native pair-shared sequential labeling job activation/correction flow
has been completed in this branch. The app-level prepared-job preview
does not establish that two native accounts can simultaneously annotate
one shared labeling job. Confirm that separately with a real pilot.

## Bundled media and provenance

The dataset includes two CC0 bird photographs, in different `images/`
categories. Its `videos/` branch does not yet contain frame sequences;
test the current folder-or-ZIP importer with a complete video-frame tree.
The photos were resized to 960 pixels wide and JPEG recompressed. Exact
sources and SHA-256 checksums are in
[image-credits.json](image-credits.json).

- `dataset/images/sparrows/bird.jpg`: house sparrow by Mathias Appel, CC0,
  https://commons.wikimedia.org/wiki/File:House_Sparrow(Passer_domesticus).jpg
- `dataset/images/garden/bird.jpg`: house sparrow by Hobbyfotowiki, CC0,
  https://commons.wikimedia.org/wiki/File:House_sparrow_(Passer_domesticus),_Amrum.jpg

For offline-only simulation run `Start-App.ps1 -Mode Simulator`; it has
a different local database and does not send real invitations.
