# Nightjar Annotatiathon — Next Steps & Roadmap

## 1. Current State Summary

The platform is running a local-first FastAPI + PostgreSQL + React/TypeScript architecture:
* **Frame Provenance & Identity** (Migration `0017_frame_identity.py`):
  Videos are decoded at full density (presentation order, timestamps, ordinals, dimensions, SHA-256).
* **Bounded Dashboard Reads** (`job_reads.py`):
  Cursor-paginated job list with a constant SQL query budget ($\le 10$ statements) to handle large-scale events without latency spikes.
* **Pure Work Queue Functions** (`work_queue.py`):
  * `split_video(ordered_asset_ids, block_size=30)`: Chunks any video into contiguous 30-frame blocks without crossing video boundaries.
  * `sample_repeat_frames(block, seed, block_key)`: Deterministically selects 2 frames per block for blind second opinions.
* **Attendee Roster Ready**:
  Generated `Inscritos_pronto.csv` covering 258 participants across 130 competition pairs with full UTF-8 accent support and team forward-fills.

---

## 2. Immediate Next Steps: Dynamic 30-Frame Shared Queue (Phase 4)

To transition from the legacy pre-allocated video planner to the live 30-frame shared queue:

### Step 2.1: Queue Database Schema & Migration
* Create an additive migration after `0017_frame_identity`:
  * `work_blocks`: `id`, `event_id`, `dataset_id`, `video_id`, `block_index`, `frame_count`, `asset_ids` (JSON), `status` (`pending`, `claimed`, `published`, `submitted`, `completed`), `claimed_by_person_id`, `claimed_by_team_id`, `claimed_at`, `submitted_at`.
  * `work_block_repeats`: tracking the 2 repeat frames per block assigned to an independent team.
* Add indexes on `(event_id, status)` for rapid queue polling.

### Step 2.2: Atomic Claim Logic (`SKIP LOCKED`)
* Implement `claim_next_block(db, person_id, team_id, event_id)`:
  * Uses PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` to prevent race conditions across 260 concurrent annotators.
  * Priority order:
    1. Interrupted / recovered blocks.
    2. Remaining blocks from videos closest to 100% completion.
    3. Fresh videos when needed.
* Prevent intra-team overlap: ensure a team never receives repeat frames from their own primary blocks.

### Step 2.3: Supervisely Worker Integration
* In `native_worker.py`:
  * Detect when an annotator submits a 30-frame job in Supervisely.
  * Mark the current block as `submitted`.
  * Immediately claim the next available 30-frame block from the queue and publish it to the annotator's Supervisely workspace.

### Step 2.4: UI Updates (Competition Tab)
* In `web/src/competition.tsx`:
  * Add a toggle/subtab for **Shared Queue Mode (30 frames)** alongside the existing legacy planner.
  * Display real-time queue metrics: blocks remaining, videos in progress, videos completed, active annotator throughput.

---

## 3. Subsequent Phases (Phases 5 – 7)

### Phase 5: Team Checkout & Mid-Event Recovery
* Add **Check out team** action in People & Teams for pairs that leave early.
* Fences the departing team, captures any partially saved frames, and returns uncompleted frames back to the queue for other teams.

### Phase 6: Decoupled Worker Lanes
* Split worker loops into independent lanes:
  * Fast Lane: Job assignment & creation.
  * Background Lane: Status polling, snapshot generation, and audit logging.

### Phase 7: Sampled Review Queue for Monitors
* Instead of reviewing all 30 frames, deliver a live stream of samples to the 5 monitors:
  * Routine sample: 1 frame per 30-frame block.
  * Conflict sample: Disagreements between independent repeat annotations.
* Interactive point/bbox overlay for instantaneous grading.

---

## 4. Useful Commands Reference

* **Start Backend**:
  ```powershell
  .\.benchmark-venv\Scripts\python.exe -m uvicorn annotatiathon.app:app --reload --host 127.0.0.1 --port 8000
  ```
* **Start Frontend**:
  ```powershell
  cd web; npm run dev
  ```
* **Run Tests**:
  ```powershell
  $env:PYTHONPATH="src;C:\Users\luis\AppData\Local\Programs\Python\Python311\Lib\site-packages"
  .\.benchmark-venv\Scripts\python.exe -m pytest
  ```
* **Run Migrations**:
  ```powershell
  .\.benchmark-venv\Scripts\python.exe -m alembic upgrade head
  ```
