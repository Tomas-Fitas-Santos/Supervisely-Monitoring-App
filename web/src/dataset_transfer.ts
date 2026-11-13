/** Browser-tab-wide dataset transfer. Page navigation never owns/cancels a transfer.
 * A full browser reload loses File objects: the organiser must reselect the
 * original archive/folder, and we resume using offsets persisted on the server.
 */
import { uploadApi as api, uploadFile, validateOffset, validateDatasetSources } from "./upload_helpers";
export { validateDatasetSources } from "./upload_helpers";

type UploadKind = "zip" | "folder";
type FileReceipt = {
  relative_path: string; received_bytes: number; expected_bytes: number;
};
type UploadStatus = { file_progress?: FileReceipt[]; file?: FileReceipt | null; next_after?: string | null };


export type DatasetTransferState = {
  datasetId: string; name: string; running: boolean;
  phase: "preparing" | "reconciling" | "uploading" | "validating" | "ready" | "interrupted";
  done: number; total: number; files: number; count: number;
  error: string | null;
};

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
  const total = 0;
  const controller = new AbortController();
  task = {
    state: {
      datasetId, name, running: true, phase: "preparing",
      done: 0, total, files: 0, count: files.length, error: null,
    },
    controller,
    done: Promise.resolve(),
  };
  notify();

  task.done = (async () => {
    try {
      const {sources, total} = await validateDatasetSources(files, kind, controller.signal,
        count => update({files: count}));
      update({total, files: 0, phase: resume ? "reconciling" : "uploading"});
      const offsets = new Map<string, number>();
      if (resume) {
        await api("/datasets/" + datasetId + "/resume", "POST", undefined, csrf, controller.signal);
        let after: string | null = null;
        do {
          const status: UploadStatus = await api<UploadStatus>("/datasets/" + datasetId +
            "/upload-status?include_files=true&limit=1000" + (after ? "&after=" + encodeURIComponent(after) : ""),
            "GET", undefined, undefined, controller.signal);
          for (const row of status.file_progress || []) {
            const source = sources.get(row.relative_path);
            if (!source || source.size !== row.expected_bytes) throw new Error("Reselected dataset does not match stored file paths and sizes.");
            offsets.set(row.relative_path, validateOffset(row.received_bytes, source.size));
          }
          if(status.next_after && status.next_after === after) throw new Error("Upload progress paging did not advance.");
          after = status.next_after || null;
        } while(after);
      }
      let done = 0;
      for(const offset of offsets.values()) done += offset;
      update({done, phase: "uploading"});
      let index = 0;
      for(const [path, file] of sources) {
        await uploadFile({file, offset: offsets.get(path) || 0, csrf, signal: controller.signal,
          url: "/datasets/uploads/" + datasetId + "/chunk?relative_path=" + encodeURIComponent(path),
          readOffset: async () => {
            const status = await api<UploadStatus>("/datasets/" + datasetId + "/upload-status?relative_path=" + encodeURIComponent(path),
              "GET", undefined, undefined, controller.signal);
            return status.file?.received_bytes ?? 0;
          },
          onProgress: bytes => {done += bytes; update({done});},
        });
        update({files: ++index});
      }
      update({ phase: "validating" });
      await api("/datasets/" + datasetId + "/finalize", "POST", undefined, csrf, controller.signal, 30 * 60_000);
      update({ phase: "ready", running: false });
    } catch (error) {
      update({ phase: "interrupted", running: false, error: (error as Error).message || "Dataset transfer stopped." });
      try {
        await api("/datasets/" + datasetId + "/interrupt", "POST", undefined, csrf, undefined, 10_000);
      } catch {
        // The server remains authoritative; a completed import cannot be interrupted.
      }

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
