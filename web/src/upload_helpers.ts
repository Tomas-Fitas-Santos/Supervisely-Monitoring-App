/** Upload-only networking: deadlines cover headers AND body; mutations are never blindly replayed. */
export async function uploadRequest<T>(path: string, init: RequestInit = {}, signal?: AbortSignal,
  timeoutMs = 120_000): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  let cancel = () => {};
  const interrupted = new Promise<never>((_, reject) => {
    cancel = () => { controller.abort(); reject(new Error("Upload paused. Reselect the same files to resume.")); };
    timer = setTimeout(() => { controller.abort(); reject(new Error("Upload request timed out. Check the connection and resume from saved progress.")); }, timeoutMs);
    signal?.addEventListener("abort", cancel, { once: true });
    if (signal?.aborted) cancel();
  });
  try {
    return await Promise.race([interrupted, (async () => {
      if (signal?.aborted) throw new Error("Upload paused.");
      const response = await fetch("/api/v1" + path, {...init, credentials: "same-origin", signal: controller.signal});
      if (response.status === 204) return undefined as T;
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Upload request failed (" + response.status + ")");
      return body as T;
    })()]);
  } finally { clearTimeout(timer!); signal?.removeEventListener("abort", cancel); }
}
export function uploadApi<T>(path: string, method = "GET", body?: unknown, csrf?: string, signal?: AbortSignal, timeoutMs?: number) {
  return uploadRequest<T>(path, {method, headers: {Accept: "application/json", ...(body === undefined ? {} : {"Content-Type":"application/json"}), ...(csrf ? {"X-CSRF-Token":csrf} : {})}, body: body === undefined ? undefined : JSON.stringify(body)}, signal, timeoutMs);
}
export type UploadKind = "zip" | "folder";
export const pathFor = (file: File, kind: UploadKind) => kind === "zip" ? "source.zip" : (file.webkitRelativePath || file.name).replaceAll("\\", "/");
const videos = new Set(["mp4","mov","mkv","avi","webm","m4v","mpeg","mpg"]);
const frames = new Set(["jpg","jpeg","png","webp","bmp","tif","tiff"]);
export async function validateDatasetSources(files: File[], kind: UploadKind, signal?: AbortSignal,
  progress?: (count: number) => void): Promise<{sources: Map<string, File>; total: number}> {
  if (!files.length || (kind === "zip" && files.length !== 1)) throw new Error("Select the original ZIP archive or full dataset folder.");
  const sources = new Map<string, File>(); let total = 0;
  for (let i = 0; i < files.length; i++) {
    if (i % 500 === 0) { progress?.(i); await new Promise(resolve => setTimeout(resolve, 0)); if(signal?.aborted) throw new Error("Upload paused."); }
    const file = files[i], path = pathFor(file, kind), parts = path.split("/");
    if (!path || file.size <= 0 || !Number.isSafeInteger(file.size) || sources.has(path) || parts.some(p => !p || p === "." || p === "..")) throw new Error("Invalid, empty or duplicated source file: " + path);
    if (kind === "folder") {
      const extension = parts.at(-1)!.split(".").pop()!.toLowerCase();
      if (!(videos.has(extension) && parts.length >= 2) && !(frames.has(extension) && parts.length >= 3) && !(parts.at(-1) === "frames.json" && parts.length >= 3)) throw new Error("Only videos or frames inside video folders are supported. Use dataset/species/video.mp4 or dataset/species/video-title/frame.jpg.");
    }
    sources.set(path, file); total += file.size;
  }
  progress?.(files.length); return {sources, total};
}
export function validateOffset(value: number, size: number): number {
  if (!Number.isSafeInteger(value) || value < 0 || value > size) throw new Error("Server returned invalid saved upload progress.");
  return value;
}
/** Sequential slices bound memory regardless of total dataset size. */
export async function uploadFile(params: {file: Blob; offset: number; url: string; csrf: string; signal: AbortSignal;
  readOffset: () => Promise<number>; onProgress: (bytes: number) => void}) {
  const {file, url, csrf, signal, readOffset, onProgress} = params;
  let offset = validateOffset(params.offset, file.size), retries = 0;
  while (offset < file.size) {
    if (signal.aborted) throw new Error("Upload paused.");
    const end = Math.min(offset + 8 * 1024 * 1024, file.size);
    try {
      const receipt = await uploadRequest<{received_bytes: number}>(url + "&offset=" + offset + "&total=" + file.size,
        {method:"PUT", headers:{"X-CSRF-Token":csrf,"Content-Type":"application/octet-stream"},body:file.slice(offset,end)}, signal);
      validateOffset(receipt.received_bytes, end);
      if (receipt.received_bytes <= offset) throw new Error("Server returned invalid chunk progress.");
      onProgress(receipt.received_bytes - offset); offset = receipt.received_bytes; retries = 0;
    } catch(error) {
      if(signal.aborted) throw error;
      const saved = validateOffset(await readOffset(), end);
      if(saved < offset) throw new Error("Saved upload progress moved backwards. Reselect the source to resume.");
      if(saved > offset) { onProgress(saved-offset); offset=saved; retries=0; }
      else if(++retries >= 3) throw error;
    }
  }
}
