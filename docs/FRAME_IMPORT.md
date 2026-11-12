# Source frames for event review

In Datasets, select **Every source frame** when importing raw videos for the
event. FFmpeg writes a JPEG for every decoded frame of the first video stream
and records the frame ordinal, timestamp when present, and image dimensions.
The app retains the original video. A repeated-looking frame still has its
own asset ID and ordinal.

The **1 frame per second** option remains available. To use an existing
sampled video at full density, import the retained original into a new
dataset. This preserves the existing dataset and its references.

**Neither ZIP nor folder upload requires any JSON metadata file.** Supply
`dataset/species/video-name/frame.jpg`, or upload the original video.
For extracted frames, Nightjar reads numeric source ordinals from names such
as `frame_000001.jpg`, `ColumbaPalumbus_f000013.jpg`, or `clip_000013.jpg`.
If the names have no unique ordinal, the importer sorts them naturally
(`image2.jpg` before `image10.jpg`) and assigns sequential ordinals.

When importing a frame folder with **Every source frame**, a contiguous
numeric sequence is accepted as an explicitly declared full-density segment,
including if the first available source ordinal is not 1. If the filenames
contain internal numeric gaps, the import still succeeds but marks the video
as incomplete/unknown-density, and full-density automatic planning will not
silently skip those frames. Use an actual consecutive frame export for the
official 30-frame jobs. **Sample one frame per second** accepts sparse frame
numbers as sampled imagery and does not pretend that a sparse selection is
the complete source video. When importing original video files, Nightjar uses
FFmpeg for the selected extraction density and does not need any metadata.

Legacy `frames.json` files are still accepted for backward compatibility,
but creating or supplying one is no longer part of the upload workflow.
For supplied frame folders, source-video completeness cannot be independently
proven without the original video. Inspect the video's density and identity
notes before selecting it for official automatic distribution.

Import a representative batch on the organiser’s PC first. The dataset
view estimates JPEG storage for a target frame count using measured bytes
per frame. Allow separate space for original videos, upload staging,
annotation JSON and database files. Run full-density imports before the event.

Apply the additive migration with `alembic upgrade head` before starting
the updated API and worker. Run
`python -m annotatiathon.manage backfill-snapshots` to populate dashboard
counters from existing local snapshots. The live Compose migration service
runs both steps. Optional legacy metadata is validated when supplied; it is
not required for current uploads.

On the isolated PostgreSQL benchmark with 6,240 stored jobs, the new
2,000-row organiser handler issued three SQL statements. Ten concurrent
organiser pages took 501 ms median and 689 ms maximum. Five concurrent
100-row monitor pages took 17 ms median and 40 ms maximum across 25 reads.
These measurements exclude network transport, browser rendering and the
Supervisely worker; the original full-stack benchmark used a different
request path. The migration retained all 6,240 jobs and cached 87 snapshots.

The official automatic queue now publishes consecutive 30-frame jobs from a
deterministic dataset-wide video stream. When a video ends before the next
30-frame boundary, that job continues with the beginning of the following
video. Every frame keeps its own original video ID, source ordinal and
annotation provenance. The last job of the entire dataset may be shorter
than 30 frames; its exact frame count is used for completion checks.
Manual assignment remains single-video 30-frame selection.

Before launching real distribution, use a disposable Supervisely workspace
and two real annotator accounts. Verify that native jobs can include source
images from two videos, that returning an incomplete job preserves its
annotations, and that the worker retrieves only the latest complete job's
annotation JSON. Rehearse the short final job separately and confirm
published/restarted jobs retain the correct source mappings.
