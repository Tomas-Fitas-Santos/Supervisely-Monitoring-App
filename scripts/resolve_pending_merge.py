"""Resolve the six KNOWN conflicts from merging the November 5 app branch into
the November 9 video/event/external-auth branch. The existing Git merge must
already be in progress. This script NEVER calls git reset, checkout or commit.

Run from the repository root: python scripts/resolve_pending_merge.py
"""
from pathlib import Path
import re
import subprocess

EXPECTED = {
    "monitoring/run.py": 4,
    "monitoring/static/attendance.js": 1,
    "monitoring/static/dashboard.js": 1,
    "monitoring/static/job_planner.js": 2,
    "monitoring/static/structured_dataset.js": 1,
    "monitoring/ui.py": 3,
}
PATTERN = re.compile(
    r"^<<<<<<< [^\n]*\n(.*?)^=======\n(.*?)^>>>>>>> [^\n]*\n",
    re.MULTILINE | re.DOTALL,
)


def require(value, fragment, where):
    if fragment not in value:
        raise RuntimeError(f"Merge layout changed in {where}: missing {fragment!r}")
    return value


def resolve_chunk(path, index, ours, theirs):
    if path == "monitoring/run.py":
        if index == 0:
            require(theirs, "hosting.add_argument('--hosted'", path)
            return theirs + (
                "    hosting.add_argument('--external', action='store_true', "
                "help='Run external HTTPS monitoring with email-code login.')\n"
            )
        if index == 1:
            require(theirs, "configure_local_environment()", path)
            return theirs + (
                "    if args.external:\n"
                "        from .deployment import external_environment\n"
                "        external_environment()\n"
            )
        if index == 2:
            return (
                "                token = os.getenv('SYNC_API_TOKEN') or "
                "(os.getenv('SUPERVISELY_SERVICE_TOKEN') if args.external else None)\n"
            )
        if index == 3:
            require(theirs, "lambda secret: ui.connection.session(secret)", path)
            return (
                "        if args.external:\n"
                "            from .external_server import ExternalShell\n"
                "            application = ExternalShell(ui.app, ui.dashboard, ui.sessions)\n"
                "        else:\n"
                + theirs.replace("        application = ", "            application = ", 1)
                .replace("\n            lambda secret:", "\n                lambda secret:")
            )

    if path == "monitoring/static/attendance.js":
        return (
            "  data() {return {busy:false,refreshing:false,query:'',status:'all',"
            "notes:{},tables:{},clientError:'',timer:null};},\n"
            "  watch: {busy(value){this.$emit('busy',value);}},\n"
        )

    if path == "monitoring/static/dashboard.js":
        require(theirs, "Event setup", path)
        lines = theirs.splitlines(keepends=True)
        output = []
        added_tab = added_component = False
        for line in lines:
            output.append(line)
            if "<button" in line and "tab==='checkin'" in line:
                output.append(
                    "      <button role=\"tab\" :aria-selected=\"tab==='food'\" "
                    ":disabled=\"busy||setupBusy\" :class=\"{selected:tab==='food'}\" "
                    "@click=\"changeTab('food')\">Food &amp; Drinks</button>\n"
                )
                added_tab = True
            if "<event-attendance " in line:
                output.append(
                    "    <event-logistics v-if=\"tab === 'food' && view.connected\" "
                    ":view=\"view\" :form=\"form\" :post=\"post\" "
                    ":locked=\"busy || setupBusy\"></event-logistics>\n"
                )
                added_component = True
        if not (added_tab and added_component):
            raise RuntimeError("New dashboard layout differs; refusing to remove a tab.")
        return "".join(output)

    if path == "monitoring/static/job_planner.js":
        if index == 0:
            return (
                "    <h3>2. Distribute whole videos to teams</h3>\n"
                "    <p>Choose the video datasets and teams, set the number of jobs, "
                "then review the distribution. A video is never split.</p>\n"
                "    <p v-if=\"!ready\" class=\"nj-caution\">"
                "Configure the event's existing Supervisely team first.</p>\n"
                "    <div class=\"nj-columns\">\n"
                "      <div>\n"
                "        <h4>Source video datasets</h4>\n"
                "        <button v-if=\"sources.length\" "
                "@click=\"selectedDatasetIds=sources.filter(s=>s.kind==='video').map(s=>s.id)\" "
                ":disabled=\"disabled||!ready\">Select all videos</button>\n"
                "        <p v-if=\"!sources.length\" class=\"nj-muted\">"
                "Upload a video dataset above, then reload the lists.</p>\n"
            )
        if index == 1:
            return (
                "    </div>\n"
                "    <details class=\"nj-advanced\"><summary>"
                "Advanced: video overlap and workload balancing</summary>\n"
                "    <div class=\"nj-form-row\">\n"
            )

    if path == "monitoring/static/structured_dataset.js":
        return (
            "    <h3>Upload a video dataset</h3>\n"
            "    <p>Choose a dataset folder or ZIP. Inspect the files, then "
            "confirm before uploading to Supervisely.</p>\n"
            "    <details><summary>Required video-only folder layout</summary>"
            "<pre>Dataset/\n"
            "  bird_species_or_morphology/\n"
            "    named_video/\n"
            "      frame_0001.jpg\n"
            "      frame_0002.jpg\n"
            "</pre><p class=\"nj-muted\">Video folders contain ordered frames. "
            "Do not add separate images/ or videos/ folders.</p></details>\n"
        )

    if path == "monitoring/ui.py":
        if index == 0:
            require(ours, "config = event_config()", path)
            require(theirs, "api = RequestApi(api)", path)
            return ours + theirs
        if index == 1:
            require(theirs, "session_uid", path)
            return (
                "        if uid != session_uid:\n"
                "            raise WorkflowError("
                "'Your Supervisely account changed. Sign in again.')\n"
                "    elif not external and int(profile.id) != int(uid):\n"
            )
        if index == 2:
            return (
                "                                   registration_rows=[], "
                "registration_people=[], event_settings={}, "
                "attendance={'teams': [], 'monitors': [], 'summary': {}},\n"
                "                                   logistics={'teams': [], 'summary': {}},\n"
                "                                   roster_profiles=[], "
                "roster_import_result=None,\n"
            )
    raise RuntimeError(f"Unexpected conflict: {path} chunk #{index + 1}")


def main():
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], text=True
    ).strip()
    if branch != "feat/video-only-event-ops-20261109":
        raise RuntimeError("Wrong local branch; this script will not change your files.")
    conflicted = set(subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=U"], text=True
    ).splitlines())
    if conflicted != set(EXPECTED):
        raise RuntimeError(
            f"Unexpected unresolved paths. Expected {sorted(EXPECTED)}; "
            f"found {sorted(conflicted)}. Resolve manually instead of guessing."
        )

    # Validate every file and EVERY expected conflict before modifying any.
    resolved = {}
    for name, expected_count in EXPECTED.items():
        text = Path(name).read_text(encoding="utf-8")
        matches = list(PATTERN.finditer(text))
        if len(matches) != expected_count:
            raise RuntimeError(f"{name}: expected {expected_count} conflicts, "
                               f"found {len(matches)}; nothing changed.")
        index = [0]
        def replace(match):
            result = resolve_chunk(name, index[0], match.group(1), match.group(2))
            index[0] += 1
            return result
        merged = PATTERN.sub(replace, text)
        if name == "monitoring/ui.py":
            # An unregistered staff account must never see reception records.
            # Dashboard rendering should remain usable and fail closed.
            anchor = "    from .event_people import EventPeople\n"
            if anchor not in merged:
                raise RuntimeError("Dashboard staff-import location changed.")
            merged = merged.replace(anchor, anchor + (
                "    try:\n"
                "        reception_view = EventLogistics(sessions).overview(uid, organiser=can_setup)\n"
                "    except WorkflowError:\n"
                "        reception_view = {'teams': [], 'summary': {}}\n"
            ), 1)
            source = "logistics=EventLogistics(sessions).overview(uid, organiser=can_setup)"
            if source not in merged:
                raise RuntimeError("Dashboard reception snapshot location changed.")
            merged = merged.replace(source, "logistics=reception_view", 1)
        if "<<<<<<<" in merged or "=======" in merged or ">>>>>>>" in merged:
            raise RuntimeError(f"{name}: unresolved markers remain. Nothing changed.")
        resolved[name] = merged

    for name, text in resolved.items():
        Path(name).write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "--", name], check=True)
        print("Resolved:", name)

    # Never commit source attendance data to a public GitHub repository.
    subprocess.run(["git", "rm", "--cached", "--ignore-unmatch",
                    "--", "docs/Annotatiathon_OFFICIAL_Roster.xlsx"], check=True)
    ignore = Path(".gitignore")
    rule = "/docs/Annotatiathon_OFFICIAL_Roster.xlsx"
    existing = ignore.read_text(encoding="utf-8")
    if rule not in existing.splitlines():
        ignore.write_text(existing.rstrip("\n") + "\n" + rule + "\n",
                          encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], check=True)
    print("\nAll six conflicts resolved and staged. Nothing committed.")
    subprocess.run(["git", "status", "--short"], check=True)
    subprocess.run(["python", "-m", "compileall", "-q", "monitoring"], check=True)


if __name__ == "__main__":
    main()
