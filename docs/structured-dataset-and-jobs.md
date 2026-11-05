# Structured event dataset: folder or ZIP, identical for DEMO and PRODUCTION

The monitoring app accepts **one of two equivalent sources**:

- **Folder mode:** choose the complete dataset directory through the browser.
- **ZIP mode:** choose a single .zip file containing that dataset directory.

The archive name and outer dataset directory name may differ. The outer directory
name is not a species, not a video, and does not enter canonical source IDs.

## Required hierarchy

~~~text
Dataset Name/
├── images/
│   ├── species_or_morphology_A/
│   │   ├── image_001.jpg
│   │   └── image_002.png
│   └── species_or_morphology_B/
│       └── ...
└── videos/
    ├── species_or_morphology_A/
    │   ├── video_title_1/
    │   │   ├── frame_000001.png
    │   │   └── frame_000002.png
    │   └── video_title_2/
    │       ├── frame_000001.png
    │       └── frame_000002.png
    └── species_or_morphology_B/
        └── ...
~~~

ZIP files may also begin directly with images/ and videos/. The browser folder
selector normally includes Dataset Name/ in every uploaded relative path.

**One video = one leaf video-title folder.** Every frame within that folder is
sorted naturally by filename (1, 2, 10, not 1, 10, 2) and encoded into one
complete MP4 file before native Supervisely upload. Source frame paths and
their ordering are retained in the MediaSequence manifest. The source video
remains indivisible in the workload planner, even if it contains thousands
of frames.

Do not put MP4 files or unrelated documents into these folders. The input videos
are frame folders, not existing video files. The two required media roots must
both contain supported files. Frame filenames must be unique within their video.

## Common DEMO / PRODUCTION workflow

1. In **Settings**, connect a verified Supervisely account and configure the
   **pre-existing event Team ID and Workspace ID**.
2. In **Dataset**, choose **Folder** or **ZIP**.
3. Choose dataset, name and FPS (video frame sampling rate).
4. Click **Stage and inspect dataset**. The app uploads source bytes only into
   its private staging area, not into Supervisely. The backend verifies paths,
   archive format and hierarchy, inventories images, and counts videos/frames.
5. Review counts and examples. Click **Confirm upload to Supervisely**.
6. The app verifies the staged content against the preview, reconstructs source
   files and complete videos, validates metadata, and uploads the two native
   Supervisely project types into the configured workspace. It saves source
   origins and frame manifests. No native team is created.
7. In **Configure labeling jobs**, choose the nested source datasets and logical
   competition pairs, jobs per pair, videos per team, image replicas, video
   replicas, common-image percentage, workload estimate and allocation seed.
8. Preview every team's images, videos, ordered job numbers and workload.
   Confirm to persist all **app-level** prepared job definitions.

**Current implementation boundary:** native Supervisely labeling jobs, sequential
activation and return-for-correction have not yet been finished in the new
single-team architecture. App-level PreparedJob records are not equivalent to
native labeling jobs. Do not claim the event's end-to-end annotation workflow
is ready until live deployment validates creation, visibility, shared-pair
ownership, submission/review/correction and next-job release.

## ZIP safety

The ZIP import rejects: path traversal, absolute paths, duplicate/case-conflicting
media paths, symbolic links, encrypted entries, dangerous decompression ratios,
truncated/bad-CRC files and excessive archive contents. It ignores common
macOS/Windows metadata files. Extraction creates generated internal filenames,
never uses ZIP paths as target filesystem paths. Preview records an archive
SHA-256; confirmation recalculates it before any native write.

Limits are configurable with EVENT_MAX_ZIP_GB (default 10),
EVENT_MAX_UNPACKED_GB (default 30), EVENT_MAX_ENTRY_GB (default 2), plus the
existing UPLOAD_MAX_FILE_MB for staged files (default 1024). The staging limit
can be lower than the ZIP limit, so adjust limits together only when appropriate.
The server needs ffmpeg/ffprobe for encoding videos.

## Verification

~~~powershell
git fetch origin
git switch feat/single-team-settings-theme-20261105
git pull --ff-only origin feat/single-team-settings-theme-20261105
py -3.12 -m pytest -q tests/test_zip_dataset.py tests/test_job_design.py
~~~

The code is committed for validation. No live Supervisely import or real
annotation job release has been performed by this change.
