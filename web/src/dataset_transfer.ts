/** Browser-tab-wide dataset transfer. Page navigation never owns/cancels a transfer.
 * A full browser reload loses File objects: the organiser must reselect the
 * original archive/folder, and we resume using offsets persisted on the server.
 */
import { api } from "./api";

type UploadKind = "zip" | "folder";
type FileReceipt = {
  relative_path: string; received_bytes: number; expected_bytes: number;
};
type UploadStatus = { file_progress?: FileReceipt[]; file?: FileReceipt | null };
type ChunkReceipt = { received_bytes: number; expected_bytes: number; complete: boolean };

export type DatasetTransferState = {
  datasetId: string; name: string; running: boolean;
  phase: "uploading" | "validating" | "ready" | "interrupted";
  done: number; total: number; files: number; count: number;
  error: string | null;
};

const CHUNK = 8 * 1024 * 1024;
const listeners = new Set<(state: DatasetTransferState | null) => void>();
let task: {
  state: DatasetTransferState;
  controller: AbortController;
  done: Promise<void>;
} | null = null;

export function getDatasetTransfer(): DatasetTransferState | null {
  return task ? { ...task.state } : null;
}

function notify() {
  const snapshot = getDatasetTransfer();
  for (const listener of listeners) listener(snapshot);
}

export function watchDatasetTransfer(
  listener: (state: DatasetTransferState | null) => void
): () => void {
  listeners.add(listener);
  listener(getDatasetTransfer());
  return () => { listeners.delete(listener); };
}

function update(values: Partial<DatasetTransferState>) {
  if (!task) return;
  task.state = { ...task.state, ...values };
  notify();
}

export function clearDatasetTransfer(datasetId: string) {
  if (task?.state.datasetId !== datasetId || task.state.running) return;
  task = null;
  notify();
}

const relative = (file: File, kind: UploadKind): string => kind === "zip"
  ? "source.zip" : (file.webkitRelativePath || file.name).replaceAll("\\", "/");

async function requestError(response: Response): Promise<Error> {
  const body = await response.json().catch(() => ({}));
  return new Error(typeof body.detail === "string"
    ? body.detail : "Upload failed (" + response.status + ")");
}

export function validateDatasetSources(files: File[], kind: UploadKind): void {
  if (!files.length || (kind === "zip" && files.length !== 1)) {
    throw new Error("Select the original ZIP archive or full dataset folder.");
  }
  const seen = new Set<string>();
  for (const file of files) {
    const path = relative(file, kind);
    if (!path || file.size === 0 || seen.has(path)) {
      throw new Error("Invalid, empty or duplicated source file: " + path);
    }
    seen.add(path);
  }
}

async function serverOffset(id: string, path: string): Promise<number> {
  const status = await api<UploadStatus>(
    "/datasets/" + id + "/upload-status?relative_path=" + encodeURIComponent(path)
  );
  return status.file?.received_bytes ?? 0;
}

export function beginDatasetTransfer(params: {
  datasetId: string;
  name: string;
  kind: UploadKind;
  files: File[];
  csrf: string;
  resume: boolean;
}): void {
  if (task?.state.running) {
    throw new Error("A dataset transfer is already running in this browser tab.");
  }
  const { datasetId, name, kind, files, csrf, resume } = params;
  validateDatasetSources(files, kind);
  const total = files.reduce((sum, f) => sum + f.size, 0);
  const controller = new AbortController();
  task = {
    state: {
      datasetId, name, running: true, phase: "uploading",
      done: 0, total, files: 0, count: files.length, error: null,
    },
    controller,
    done: Promise.resolve(),
  };
  notify();

  task.done = (async () => {
    try {
      const offsets = new Map<string, number>();
      if (resume) {
        await api("/datasets/" + datasetId + "/resume", "POST", undefined, csrf);
        const status = await api<UploadStatus>(
          "/datasets/" + datasetId + "/upload-status?include_files=true"
        );
        for (const row of status.file_progress || []) {
          offsets.set(row.relative_path, row.received_bytes);
          const source = files.find(f => relative(f, kind) === row.relative_path);
          if (!source || source.size !== row.expected_bytes ||
              row.received_bytes > row.expected_bytes) {
            throw new Error(
              "Reselected dataset does not match stored file paths and sizes."
            );
          }
        }
      }
      let done = files.reduce((sum, f) => sum + (offsets.get(relative(f, kind)) || 0), 0);
      update({ done });
      for (let index = 0; index < files.length; index++) {
        const file = files[index];
        const path = relative(file, kind);
        let offset = offsets.get(path) || 0;
        let retries = 0;
        while (offset < file.size) {
          if (controller.signal.aborted) throw new Error("Upload stopped.");
          const end = Math.min(offset + CHUNK, file.size);
          try {
            const response = await fetch(
              "/api/v1/datasets/uploads/" + datasetId + "/chunk?relative_path=" +
              encodeURIComponent(path) + "&offset=" + offset + "&total=" + file.size,
              {
                method: "PUT", credentials: "same-origin",
                headers: {
                  "X-CSRF-Token": csrf,
                  "Content-Type": "application/octet-stream",
                },
                signal: controller.signal,
                body: file.slice(offset, end),
              }
            );
            if (!response.ok) throw await requestError(response);
            const receipt = await response.json() as ChunkReceipt;
            if (receipt.received_bytes <= offset || receipt.received_bytes > end) {
              throw new Error("Server returned invalid chunk progress.");
            }
            const progress = receipt.received_bytes - offset;
            offset = receipt.received_bytes;
            done += progress;
            retries = 0;
            update({ done, files: index });
          } catch (error) {
            if (controller.signal.aborted) throw error;
            // The server may have committed a chunk while its HTTP reply was lost.
            // Query its offset before attempting any retry to avoid duplicating bytes.
            const stored = await serverOffset(datasetId, path);
            if (stored > offset && stored <= end) {
              done += stored - offset;
              offset = stored;
              retries = 0;
              update({ done, files: index });
            } else if (++retries >= 3) {
              throw error;
            } else {
              await new Promise(resolve => setTimeout(resolve, 800 * retries));
            }
          }
        }
        update({ files: index + 1 });
      }
      update({ phase: "validating" });
      await api("/datasets/" + datasetId + "/finalize", "POST", undefined, csrf);
      update({ phase: "ready", running: false });
    } catch (error) {
      try {
        await api("/datasets/" + datasetId + "/interrupt", "POST", undefined, csrf);
      } catch {
        // The server remains authoritative; a completed import cannot be interrupted.
      }
      update({
        phase: "interrupted", running: false,
        error: (error as Error).message || "Dataset transfer stopped.",
      });
    }
  })();
}

export async function stopDatasetTransfer(id: string, csrf: string): Promise<void> {
  if (task?.state.datasetId === id && task.state.running) {
    if (task.state.phase === "validating") {
      throw new Error("Validation is already in progress; it cannot be stopped safely.");
    }
    task.controller.abort();
    await task.done;
  } else {
    await api("/datasets/" + id + "/interrupt", "POST", undefined, csrf);
  }
}
