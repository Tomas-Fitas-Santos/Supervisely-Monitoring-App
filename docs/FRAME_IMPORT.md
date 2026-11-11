# Source frames for event review

In Datasets, select **Every source frame** when importing raw videos for the
event. FFmpeg writes a JPEG for every decoded frame of the first video stream
and records the frame ordinal, timestamp when present, and image dimensions.
The app retains the original video. A repeated-looking frame still has its
own asset ID and ordinal.

The **1 frame per second** option remains available. To use an existing
sampled video at full density, import the retained original into a new
dataset. This preserves the existing dataset and its references.

For an image folder, name the frames by ordinal (for example, `frame_0001.jpg`)
or put `frames.json` next to the images. Numeric names establish order, but
cannot establish how many frames the original video contained. For example,
this manifest declares a one-frame source:

```json
{
  "version": 1,
  "source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "source_frame_count": 1,
  "all_frames": true,
  "time_base": "1/30",
  "frame_rate": "30/1",
  "frames": [{"file": "frame_0001.jpg", "ordinal": 1, "pts": 0}]
}
```

Use the real source SHA-256 and list every supplied image once. Include
`time_base` if any frame has a PTS. Optional frame fields are `width`, `height`
and `sha256`. For uploaded folders, the source checksum and density are
declarations. Inspect the dataset’s video details for gaps or unknown
density before choosing event footage.

Import a representative batch on the organiser’s PC first. The dataset
view estimates JPEG storage for a target frame count using measured bytes
per frame. Allow separate space for original videos, upload staging,
annotation JSON and database files. Run full-density imports before the event.

Apply the additive migration with `alembic upgrade head` before starting
the updated API and worker. Run
`python -m annotatiathon.manage backfill-snapshots` to populate dashboard
counters from existing local snapshots. The live Compose migration service
runs both steps. Missing and unreadable manifests are reported separately.

On the isolated PostgreSQL benchmark with 6,240 stored jobs, the new
2,000-row organiser handler issued three SQL statements. Ten concurrent
organiser pages took 501 ms median and 689 ms maximum. Five concurrent
100-row monitor pages took 17 ms median and 40 ms maximum across 25 reads.
These measurements exclude network transport, browser rendering and the
Supervisely worker; the original full-stack benchmark used a different
request path. The migration retained all 6,240 jobs and cached 87 snapshots.

The current worker still publishes the existing job format. The new 30-frame
splitter and repeat selector are ready for the later queue and do not publish
anything by themselves.

Before wiring the queue to Supervisely, use a disposable workspace and two
test annotators. Verify which per-image signals distinguish untouched, saved
partial, individually confirmed, confirmed empty and submitted images in a
30-image job. Verify that two copies of the same source image have separate
native IDs and independent annotations. Finally, stop one disposable job
and check whether saved annotations remain readable and whether editing
is blocked. Keep credentials in the app settings.
