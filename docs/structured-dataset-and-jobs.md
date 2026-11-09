# Event dataset: complete videos only

DEMO and PRODUCTION use one identical workflow. Only the uploaded data changes.
The dataset contains no standalone still images and no `images/` or `videos/` parent
directories. Upload a complete dataset folder **or** an equivalent ZIP archive.

## Input hierarchy

```text
Bird Event Dataset/
├── Species or Morphology A/
│   ├── Video 001/
│   │   ├── frame_000001.jpg
│   │   ├── frame_000002.jpg
│   │   └── ...
│   └── Video 002/
│       └── frame_000001.png
└── Species or Morphology B/
    └── Video 003/
        └── frame_000001.jpg
```

An archive may also start directly at the species/morphology folders
(e.g. `Species A/Video 001/frame_000001.jpg`). ZIP upload checks path
traversal, duplicates, symlinks, archive changes after preview, CRC,
decompression limits and ambiguous paths. The browser folder upload has the
same canonical manifest.

**One video is one leaf folder.** Every video must have at least 2 frames,
naturally sorted. The backend converts those frames to a single MP4 with ffmpeg
before uploading it to a native Supervisely **video** project in the previously
configured shared event Team and Workspace. No source image project is created.
A full video is never split across competition pairs or logical jobs.

## Import and distribution

1. **Settings**: configure the existing event Team ID (125980) and the real
   Workspace ID; authenticate the organiser using the real numeric User ID and
   API token. The app never creates a new native team.
2. **Dataset**: choose a folder or ZIP, stage the source, and inspect videos,
   ordered frames, number of categories and archive integrity.
3. Confirm explicitly to upload complete converted videos to the configured
   Supervisely workspace. The original paths remain as MediaSequence records.
4. **Configure labeling jobs**: choose only video datasets, choose competition
   pairs, jobs per pair, complete videos per pair and video overlap.
5. Preview the workload and overlap. Save an application-level job plan.

**Production-readiness blocker:** prepared application jobs are not yet native
Supervisely labeling jobs. Native pair sharing, submission recognition,
return-for-correction and automatic release of the next prepared job still
require implementation and real multi-account validation. Do not start a
competitive event on the strength of an app-level prepared manifest alone.

## Capacity

The file inventory guard permits up to 250,000 individual frame paths, subject
to video-count/frame-count and upload limits. This does **not** imply that the
monitoring server has sufficient temporary space for the full event dataset or
that the Pro subscription quota is verified. ZIP defaults are deliberately
restricted to 10 GB packed / 30 GB unpacked; configurable environment variables
`EVENT_MAX_ZIP_GB` and `EVENT_MAX_UNPACKED_GB` must be reviewed with
available server disk before scaling up. Confirm real Supervisely video storage
capacity separately. Never hard-code API tokens or registration data in Git.

## Tests

```powershell
python -m pytest -q tests/test_zip_dataset.py tests/test_job_design.py
```

The tests validate the local parsers and deterministic workload logic; they do
not prove that the native Supervisely server accepts the upload or job dispatch.
