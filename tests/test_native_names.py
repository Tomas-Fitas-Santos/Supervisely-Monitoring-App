"""Stable readable Supervisely titles and strict reset ownership discovery."""
from annotatiathon.models import (
    CompetitionTeam, Event, LabelingJob, MediaDataset,
)
from annotatiathon.native_names import (
    dataset_name, labeling_job_name, legacy_project_name,
    owned_project_event_id, project_name, video_label,
)

EVENT_ID = "00000000-0000-4000-8000-000000000010"
DATASET_ID = "aaaa0000-0000-4000-8000-000000000001"
JOB_ID = "bbbb0000-0000-4000-8000-000000000002"


def test_supervisely_names_readable_and_deterministic():
    event = Event(id=EVENT_ID, name="Annotatiathon 2026")
    ds = MediaDataset(id=DATASET_ID, event_id=EVENT_ID,
                      name="BirdGaze - Birds", upload_kind="zip")
    team = CompetitionTeam(id="team01", name="Team 12")
    job = LabelingJob(id=JOB_ID, team_id=team.id, event_id=EVENT_ID,
                      dataset_id=DATASET_ID, plan_id="plan01",
                      sequence=1, frame_count=4, video_count=1,
                      media_ids=[], morphology_counts={})
    remote_project = project_name(event, ds)
    assert remote_project.startswith("Nightjar · BirdGaze - Birds · Annotatiathon 2026")
    assert remote_project.endswith(f"[NJ:{EVENT_ID}:{DATASET_ID}]")
    assert owned_project_event_id(remote_project) == EVENT_ID
    assert owned_project_event_id(legacy_project_name(ds)) == EVENT_ID
    remote_dataset = dataset_name(job, team, "CorvusCorone/flight_01")
    assert remote_dataset.startswith("Team 12 · flight 01")
    assert remote_dataset.endswith("[NJ:" + JOB_ID + "]")
    remote_job = labeling_job_name(job, team, "CorvusCorone/flight_01", "Miguel Marx")
    assert remote_job.startswith("Team 12 · flight 01 · Miguel Marx")
    assert remote_job.endswith(" · #" + JOB_ID[:8])
    assert video_label("ColumbaPalumbus/flight_02") == "flight 02"


def test_remote_reset_never_guesses_ownership_from_unrelated_projects():
    assert owned_project_event_id("Other research") is None
    assert owned_project_event_id("Nightjar someone else's bird dataset") is None
    assert owned_project_event_id("Nightjar-unknown-custom-title") is None
    assert owned_project_event_id(
        "Foreign Project [NJ:" + EVENT_ID + ":" + DATASET_ID + "]"
    ) is None
    assert owned_project_event_id(
        project_name(
            Event(id="99999999-0000-4000-8000-000000000099", name="Other event"),
            MediaDataset(id=DATASET_ID, event_id=EVENT_ID,
                         name="BirdGaze", upload_kind="zip"),
        )
    ) != EVENT_ID
