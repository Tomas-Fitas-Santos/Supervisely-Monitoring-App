# Mounted-source datasets: indexing and Supervisely mirroring

Nightjar imports a local dataset in **two separate, durable phases**:

1. **Local indexing**: the always-on `indexer` service reads the mounted species/video
   folder, checks media, extracts working frames from raw videos when needed,
   and creates PostgreSQL metadata. This does **not** need Supervisely access.
2. **Supervisely mirror**: the separately enabled `worker` service reads the
   indexed frames via the shared media/source mounts, uploads them using the
   Supervisely SDK, and persists the source-image to remote-image mapping.
   The team/workspace must be verified and an organiser credential configured.

The source files remain mounted read-only. Video decoding **does** create
derived local frames under the persistent `NIGHTJAR_MEDIA_DIR`, and these
may require significant disk space.

## Fix a registered dataset stuck at Indexing / pending / 0 frames

On the Windows PC hosting Docker Desktop:

```powershell
git pull --ff-only origin main
docker compose --env-file .env.live -f compose.live.yaml up -d --build api web indexer
docker compose --env-file .env.live -f compose.live.yaml ps
docker compose --env-file .env.live -f compose.live.yaml logs --tail=100 indexer
```

Existing **pending** mounted datasets are automatically picked up by the
indexer; **do not create another dataset**. Indexing counts may remain
zero until the local transaction finishes, especially with raw videos
that need decoding. In the UI, `running` and its elapsed time indicate
the operation was claimed.

If the indexer exits, check logs and that the same read-only Windows source
mount is available in both the API and indexer containers. To set or verify
the source mount without resetting the database, use:

```powershell
.\scripts\link-local-dataset.ps1 -DatasetFolder "D:\BirdDataset\DEMO_Bird_Dataset"
```

Adjust the path to the real dataset root on your PC. Its structure must be
`dataset-root/species/video.mp4`, `dataset-root/species/image.jpg`, or
`dataset-root/species/clip/frame_000001.jpg`.

**Do not** run `docker compose down -v`, delete `.env.live`, delete
the host source folder, or recreate the PostgreSQL volume.

## When indexing is ready but Supervisely upload remains queued

The native worker is intentionally optional. Once the source index is
ready, verify the shared Supervisely Team ID, Workspace ID and organiser
integration credential in Settings; then start publishing:

```powershell
docker compose --env-file .env.live -f compose.live.yaml --profile native up -d --build worker
docker compose --env-file .env.live -f compose.live.yaml --profile native logs --tail=100 worker
```

The mirror progress meter tracks the actual uploaded frames. A pending
mirror does **not** mean the uploaded frames already exist on Supervisely.
If the mirror enters `attention`, inspect its error code and worker logs
before using Retry in the UI. Don't manually restart an uncertain remote
write without reconciling native results.

## Distinguish the states

| Local dataset | Index operation | Meaning |
| --- | --- | --- |
| Indexing, zero frames | Pending | Indexer has not claimed the task |
| Indexing, zero frames | Running | Reading/decoding source media; counts committed at end |
| Failed | Attention | Indexer rejected source/failed; inspect logs, then retry |
| Ready, upload pending | Done | Local indexing succeeded; native worker needs to mirror |
| Ready, upload active | Done | Supervisely mirroring is in progress |
| Ready, upload complete | Done | Remote verification completed; proceed to jobs |

Always start by examining the current operation state rather than
registering another copy of the same source.
