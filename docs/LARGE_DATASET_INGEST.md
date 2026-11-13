# Large dataset ingestion and system impact

## Recommended path

For a dataset already on the Docker host, use **Dataset → Link host folder**.
Mount its parent directory read-only through `NIGHTJAR_DATASET_HOST_DIR`; the
existing Compose backend definition exposes the same source and private media
paths to the API, indexer, and native workers. See
[the host-folder setup](DIRECT_FOLDER_TO_SUPERVISELY.md).

The indexer reads existing images without copying them. The mirror worker sends
them directly from the server to Supervisely once, retaining source-to-image-ID
mappings. Automatic jobs then reference those IDs. Existing manual assignments
retain their independent-copy behavior.

Every authorized organiser can view images through the existing authenticated,
event-scoped review image endpoint. Their computers need neither the source
folder nor a mount. The host and source volume must stay available. Displaying
an image still transfers its bytes to the viewing browser; mounting eliminates
the initial browser dataset transfer and automatic job reuploads.

For a dataset on a remote organiser's computer, retain **Upload folder or ZIP**.
ZIP is usually preferable to 100,000 individual files across a high-latency
connection. Both formats use resumable 8 MiB chunks. Folder imports use up to
four concurrent file requests, and failed transfers settle active writes before
reporting interruption.

## Why validation could stall the system

The old browser path performed hashing, archive extraction, frame validation,
and inventory insertion inside a single finalization HTTP request. Its event
row lock stayed open during that work. Increasing a browser deadline did not
remove upstream proxy deadlines. Cloudflare documents its origin read timeout
and recommends status polling for long operations in its
[524 guidance](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/).

The inspected live logs contained a disconnected finalization request (Nginx
499), connection-pool exhaustion, and disconnected membership refresh requests.
Those logs do not prove which component first ended the historical request.
The code does explain a likely amplification: other mutations wait for the same
event lock while occupying database connections.

There was a second locking problem even after moving work out of HTTP:
PostgreSQL foreign-key checks on inserted images hold `KEY SHARE` locks on the
event and dataset. The old `FOR UPDATE` guards conflict with those locks. Event
and dataset mutation guards now use `FOR NO KEY UPDATE`: they still serialize
mutations, but remain compatible with long image-insert transactions. This is
also applied to publication, manual jobs, submission validation, and closeout
event guards. Sync keeps its dataset in the committed `indexing` state without
holding a metadata-write lock during file I/O. Its final commit locks the event
before flushing dataset changes, matching API mutation lock order. Event and
dataset IDs remain immutable. See the
[PostgreSQL lock compatibility rules](https://www.postgresql.org/docs/17/explicit-locking.html)
and [SQLAlchemy lock options](https://docs.sqlalchemy.org/en/20/core/selectable.html).

Chunk requests also previously ran synchronous database and disk operations on
the API's async event loop. They now buffer only one bounded chunk and perform
those operations in a worker thread.

## New lifecycle and compatibility

The browser calls `POST /datasets/{id}/finalize?background=true`. This returns
HTTP 202 after validating upload receipts and queuing a durable
`index_uploaded_dataset` operation. The dataset enters `indexing`. The always-on
indexer handles uploaded folders and ZIPs as well as mounted folders, without
Supervisely credentials. The browser can close after queuing validation.

The old synchronous finalization API remains available for existing clients and
scripts. It rejects a dataset that already has a background import in progress.
Existing ready datasets, media IDs, frame ordinals, checksum validation,
Supervisely mappings, and review URLs remain compatible. An additive migration
creates mounted-source receipts; existing imports remain usable without reimport.

| Area | Integration behavior |
| --- | --- |
| Browser | Polls indexing status; displays file/check progress; retries failed validation using retained server files |
| API | Accepts chunks and queues work; event mutations remain available during inventory inserts |
| Indexer | Uses 500-row bulk inserts; commits validated inventory atomically; writes a small shared progress snapshot at most once per second |
| Recovery | PostgreSQL session advisory locks prevent duplicate owners; after a crash, rebuilds uncommitted derived output from retained staging or mounted originals |
| Supervisely mirror | Batches default to 128 images and 64 MiB of source bytes; an individual larger image travels alone; remote inventory is reconciled before recovery writes |
| Job publication | Existing automatic source-image-ID references continue to avoid image reuploads |
| Review and organisers | Existing server authorization and event isolation apply to both mounted and private media |
| Reset | Refuses destructive reset while a local import is indexing |
| Closeout | Keeps the existing cutoff behavior; an importer checks event state again before committing and cannot add ready inventory after closeout |

Folder uploads attempt hard links from private staging into final media, avoiding
a second copy of all supplied image bytes. Filesystems without hard-link support
fall back to copying. ZIPs require extraction space. Mounted source files are
never deleted or rewritten by recovery; cleanup is limited to validated private
derived directories.

Current defaults allow 250,000 assets and
140,000,000,000 source bytes. ZIP expanded contents are also size-limited. Ensure
space for archive plus extracted output, or original videos plus decoded frames.
Keep large media outside OneDrive; actual host-volume performance must be
measured separately from the synthetic application benchmark.

Optional private environment settings:

```dotenv
MIRROR_BATCH_IMAGES=128
MIRROR_BATCH_BYTES=67108864
```

The dedicated mirror worker retains its existing 768 MiB container limit. The
batch byte setting describes source-file bytes, not a guarantee of total SDK
memory usage. Increase concurrency or batch limits only with measured throughput
and memory evidence. Uploads still depend on host uplink and Supervisely limits.

## Adding a mounted dataset in parts

The selected folder is the dataset root. The app does not watch for writes.
After copying **complete new videos or standalone images**, use **Sync additions**
in the host-folder library. This queues a durable local scan and returns HTTP
202; the browser may close. Synchronize all parts **before creating a labeling
plan or assigning any jobs**. Assigned jobs, historical assignments, and planned
or approved automatic pools prevent synchronization, preserving their source set.

For existing source files, committed size and modification-time receipts avoid
rereading unchanged bytes. A metadata change triggers checksum comparison;
unchanged content updates its receipt, while replacements fail the scan. As with
other metadata-based incremental tools, this assumes writers do not deliberately
preserve both size and modification time while altering content. Imports predating
the receipt migration bootstrap receipts by checking their committed media hashes
on the first sync. New file hashes and source statistics are checked during the
scan; directory changes also reject a scan that races a copy. Finish copying
before clicking: a contiguous partial video cannot be distinguished from an
intentional complete clip without a manifest or original source video.

Sync never renumbers old frames or removes inventory. Missing existing files,
replacements, and extra frames inside an already indexed video fail atomically.
Put each complete new video under a new video name. Resolve the reported source
problem and click the button again; abandoned decoder output for a new video is
removed before retry so it cannot shadow supplied mounted frames. Existing committed IDs and remote bindings
remain intact. The source folder must remain mounted throughout app use.

A successful nonempty sync increments the inventory revision, invalidates
previous balanced preparation, and queues only the missing Supervisely image
bindings through the existing reconciled mirror. A no-change sync leaves the
revision unchanged. The result displays new videos/frames/images and existing
files skipped. Duplicate clicks share the same operation; PostgreSQL advisory
ownership permits recovery after a crashed worker without concurrent indexing.

The shared annotation-review queue now resolves mounted images through the same
authorized source resolver as dataset previews. This fixes a previously private-
media-only path that could return 404 for mounted review samples.

## Scale evidence and limits

The repeatable [benchmark](../scripts/benchmark_dataset_ingest.py) indexes
100,000 JPEGs representing 6,553,600,000 logical source bytes. It uses temporary
SQLite, repeated cached JPEG content, and hard links on the container filesystem.
It does not measure a real host drive, diverse image decoding, PostgreSQL
throughput, or Supervisely upload speed.

On this host, the first instrumented run took **115.968 seconds**. After changing
inventory insertion to 500-row bulk mappings, the run took **97.432 seconds**
(about **16% less elapsed time**). Hashing took 15.704 seconds and image-header
reads 11.578 seconds in the latter run; the rest includes directory/path work,
identity sorting, SQL/ORM work, and commit overhead. These are single runs, not a
controlled hardware comparison or an upload-speed promise.

The large-file regression suite exercises a sparse ZIP64 archive above 6 GiB,
chunk receipts and resume offsets above 4 GiB, browser slicing above 5 GiB,
folder/ZIP background imports, retry and restart recovery, second-organiser
access, anonymous denial, unchanged automatic publication behavior, and real
PostgreSQL worker ownership and foreign-key lock concurrency. Sparse and virtual
fixtures prove large-file addressing, not sustained multi-gigabyte transfer.

Validation includes folder, ZIP, mounted, and incrementally synchronized mounted
imports through manual, standard automatic, and balanced automatic distribution;
partial submission, correction, accepted annotation preservation, review image
delivery, review completion, and subsequent nonduplicating job allocation. All
external operations in these tests use the isolated SDK test double, not the
real Supervisely workspace. The PostgreSQL checks use a disposable migrated
database. Lint ignores executable-bit checks because Docker's Windows bind mount
presents source files as executable. The final local gates passed **324 backend
tests**, **8 browser upload/read tests**, Python lint, and the production
TypeScript/Vite build. A separate isolated production-container HTTP/SDK smoke
used Supervisely SDK 6.74.56 against the internal simulator: 150 source images
mirrored, five unique 30-frame jobs allocated, all 150 frames validated, one
correction operation completed, and 92 frame reviews completed with image GETs.
All 447 application requests returned 200/201/202; there were no operation errors.
This validates SDK transport and lifecycle integration, not real Supervisely
service throughput or a 100,000-image network transfer.

A real 100,000-image Supervisely transfer remains an operational verification
step. This change does not upload a synthetic collection into the real workspace.

## November 13 indexing and recovery fixes (not deployed)

The observed mounted import failed after 8,156 files because replacing
`index-progress.json` raised `PermissionError` on the shared filesystem.
Progress snapshots are now best effort, including rate-limited failed writes and
warnings. A progress-display failure cannot abort indexing or undo valid work.

The source reader opens each frame directory once with no-follow directory
handles on Linux, then hashes and checks image headers through those handles.
Four reader threads process bounded groups of 128 files; SQL remains on the
owning thread. Directory enumeration uses `scandir`, and validated dimensions
are reused during frame-order/manifest validation. Final file and directory
checks still reject changed sources. Symlink tests cover both leaf files and
replaced parent directories. Platforms without directory handles retain the
checked path fallback.

Mounted-frame validation receipts are appended and flushed in batches of 250
to a private, versioned journal outside the SQL inventory transaction. On retry,
unchanged size, nanosecond timestamps, inode and device identity allow reuse of
saved checksums and dimensions. A torn journal append is ignored. An ordinary
exception flushes the remaining batch; abrupt process loss can repeat the last
unflushed batch. If checkpoint storage itself fails, indexing remains correct
but those checks may need repeating. Source files must remain unchanged.
Inventory publication is still atomic, so unfinished datasets cannot enter jobs
or review. Raw-video decoding and uploaded ZIP extraction may repeat on an
indexing retry; their source bytes remain retained and need no network reupload.

Both initial and append uploads now flush chunk bytes to storage before saving
their database receipt. If a crash leaves extra bytes beyond that receipt,
resume discards only the unacknowledged tail and resends from the saved offset.
Missing acknowledged bytes still cause a visible error instead of silently
accepting damaged storage. Append chunk I/O also runs outside the API event loop.
Supervisely still uses its existing 128-image/64-MiB batches and reconciles saved
remote IDs on retry; this change does not increase remote concurrency.

In a read-only 64-frame sample on the actual Windows/Docker source mount, the
previous I/O sequence took **4.384 seconds**, the implemented reader took
**0.275 seconds**, and checkpoint reuse took **0.108 seconds**. Hashes and
dimensions matched. These cached sample results measure the source-read stage,
not the full import or remote upload. Repeated path/stat checks alone accounted
for 3.61 seconds in the initial 64-frame profiling pass.

An isolated end-to-end synthetic run indexed **100,000 small JPEG frames across
100 videos in 93.14 seconds**, with frame dimensions and ordering verified, in a
container limited to 1 GiB. The journal occupied 24.5 MB. This used hard-linked,
cached 32×24 JPEGs and SQLite; it does not predict real image, drive, PostgreSQL,
or Supervisely throughput and is not directly comparable to the earlier padded
single-video benchmark. The benchmark script now supports `--videos` and
includes checkpoint writes. Its per-worker timings overlap.

Validation: **336 backend tests**, including disposable PostgreSQL concurrency,
job distribution, submission validation and review; **15 browser tests**,
including progressive 50K-file traversal, missing-file resume rejection, lost
responses, and virtual ZIP chunks above 5 GiB. The production frontend build
passes. The separate read-only preview verifies folder-picker feedback and
responsive upload/append layouts. Full real-dataset throughput remains to be
measured after deployment approval; the currently running stack is unchanged.

## Applying the change

Rebuild the API, web, indexer, and native worker images together using the
existing deployment workflow. The new browser queue needs the updated indexer;
restart the dedicated mirror worker when applying its recovery/lock changes.
Do not run an old mirror worker concurrently with a new one during the upgrade.
No database reset, source relocation, or volume removal is required.

After deployment, reload the browser so it uses the updated finalization flow.
Old disconnected imports are not automatically guessed to be complete: inspect
their saved upload receipts, then resume/requeue using the existing controls.
Do not register another mounted copy merely because indexing takes time.
