# Direct source-folder upload to Supervisely

Nightjar's Dataset tab now links an existing **host-accessible folder**, without browser uploads,
ZIP staging, or copying supplied JPEG/PNG frames into the Nightjar data directory.

## One-time Docker Desktop setup (Windows)

1. Put each dataset below a parent folder on the PC that runs the Docker containers, for example:

       D:/BirdDatasets/BirdGaze2026/Passer_domesticus/Video_01/frame_000001.jpg
       D:/BirdDatasets/BirdGaze2026/Passer_domesticus/Video_01/frame_000002.jpg
       D:/BirdDatasets/AnotherDataset/Corvus_corone/corvid_clip.mp4

   Species can also contain directly imported image files. Source symlinks are not permitted.
   No metadata JSON is required for normal frame folders. `frames.json` is supported.

2. In the existing, **private** `.env.live`, set:

       NIGHTJAR_DATASET_HOST_DIR=D:/BirdDatasets

   This must be a **parent directory**, not the dataset subfolder itself.
   Docker Compose mounts it read-only at `/source-datasets` for both the API and worker.
   Ensure Docker Desktop is allowed to access the drive. Do not place large datasets
   under a OneDrive-synced directory.

3. Update and deploy the existing stack (it backs up PostgreSQL and does not delete media):

       git switch main
       git pull --ff-only origin main
       .\scripts\deploy-live.ps1 -StartWorker

   The current Cloudflare Tunnel and existing PostgreSQL volume are retained.

4. Open **Dataset → Source folders & upload**, select `BirdGaze2026`,
   choose **Every source frame** (needed for full-density 30-frame jobs),
   and click **Link folder & start Supervisely upload**.

5. The long-running worker indexes source filenames and checksums in PostgreSQL
   without copying supplied frame files. Then the existing SDK mirror process
   uploads them directly from `/source-datasets` to the configured Supervisely
   workspace. The status panel shows `indexing`, then `ready` with
   Supervisely upload progress. Automatic jobs are enabled only after complete mirroring.

## Runtime constraints

- A browser cannot grant a remote web server arbitrary filesystem access. This
  feature only works when the source folder is on, shared with, or mounted on
  the **Docker host**. Remote visitors cannot select their own PC folders.
- Media under the source folder are mounted **read-only**. The app keeps
  annotations, database metadata, exports, and any frames extracted from raw videos
  in its existing writable Nightjar data directory.
- Raw video files must be decoded into frames for 30-frame labeling jobs.
  This uses additional disk space. For already-extracted images, Nightjar does
  not duplicate the image bytes locally.
- Do not move, rename, edit, disconnect, or delete a mounted source while
  Nightjar still needs the files for Supervisely mirroring or review display.
  Maintain a separate backup.
- A dataset directory is one direct child of the configured parent mount.
  Paths with traversal components and symlinked dataset folders are rejected.
- A browser request **only queues** the import. Supervisely credentials are never
  exposed to the browser, and media are never transferred through Cloudflare.
- Existing datasets and the previous chunk-upload API are retained for
  compatibility, but the Dataset tab now uses the mounted-folder workflow.

## Troubleshooting

If the app shows **No dataset source volume mounted**, check the
`NIGHTJAR_DATASET_HOST_DIR` entry and redeploy. If the dataset is **indexing**
without advancing, check the native worker:

    docker compose --env-file .env.live -f compose.live.yaml --profile native ps
    docker compose --env-file .env.live -f compose.live.yaml --profile native logs --tail=150 worker

If indexing reports **failed**, inspect those logs, correct the source folder,
and use **Retry indexing**. The worker removes only derived private frames
after a failed attempt; it never alters the mounted originals.

If indexing succeeded but the Supervisely mirror is `pending`, `running`, or
`attention`, check worker connectivity and the verified Supervisely Team ID
and Workspace ID in Settings. Use the existing retry action for `attention`.
