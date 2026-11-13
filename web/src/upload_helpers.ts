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
export type PreparedDatasetSources = {sources: Map<string, File>; total: number};
export interface DatasetFileHandle {
  kind: "file"; name: string; getFile(): Promise<File>;
}
export interface DatasetDirectoryHandle {
  kind: "directory"; name: string;
  values(): AsyncIterableIterator<DatasetDirectoryHandle | DatasetFileHandle>;
}

function checkedSourceSize(path: string, file: File, kind: UploadKind): number {
  const parts=path.split("/"), size=file.size;
  if (!path || size<=0 || !Number.isSafeInteger(size) || parts.some(p=>!p||p==="."||p===".."))
    throw new Error("Invalid or empty source file: "+path);
  if (kind === "folder") {
    const extension=parts.at(-1)!.split(".").pop()!.toLowerCase();
    if (!(videos.has(extension)&&parts.length>=2) && !(frames.has(extension)&&parts.length>=3)
      && !(parts.at(-1)==="frames.json"&&parts.length>=3))
      throw new Error("Only videos or frames inside video folders are supported. Use dataset/species/video.mp4 or dataset/species/video-title/frame.jpg.");
  }
  return size;
}

export async function validateDatasetSources(files: ArrayLike<File>, kind: UploadKind, signal?: AbortSignal,
  progress?: (count: number, bytes: number) => void): Promise<PreparedDatasetSources> {
  if (!files.length || (kind === "zip" && files.length !== 1)) throw new Error("Select the original ZIP archive or full dataset folder.");
  const sources = new Map<string, File>(); let total = 0;
  for (let i = 0; i < files.length; i++) {
    if (i % 500 === 0) { progress?.(i,total); await new Promise(resolve => setTimeout(resolve, 0)); if(signal?.aborted) throw new Error("Upload paused."); }
    const file = files[i], path = pathFor(file, kind);
    if(sources.has(path))throw new Error("Duplicated source file: "+path);
    total += checkedSourceSize(path,file,kind); sources.set(path,file);
  }
  progress?.(files.length,total); return {sources, total};
}

/** Traverse handles lazily: no FileList or entire-folder metadata pass before upload. */
async function* directoryEntries(directory: DatasetDirectoryHandle, prefix: string,
  signal: AbortSignal, depth=0): AsyncGenerator<[string, DatasetFileHandle]> {
  if(depth>6)throw new Error("Dataset folders are nested too deeply.");
  for await(const entry of directory.values()) {
    if(signal.aborted)throw new Error("Upload paused.");
    if(!entry.name||entry.name==="."||entry.name===".."||/[\\/]/.test(entry.name))
      throw new Error("Invalid source filename.");
    const path=prefix+"/"+entry.name;
    if(entry.kind==="directory")yield* directoryEntries(entry,path,signal,depth+1);
    else yield [path,entry];
  }
}

function abortableMetadata<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve,reject)=>{
    const cancel=()=>{signal.removeEventListener("abort",cancel);reject(new Error("Upload paused."));};
    signal.addEventListener("abort",cancel,{once:true});
    if(signal.aborted)cancel();
    pending.then(resolve,reject).finally(()=>signal.removeEventListener("abort",cancel));
  });
}

export async function uploadDirectorySources(directory: DatasetDirectoryHandle,
  send: (path: string, file: File) => Promise<void>, signal: AbortSignal,
  discovered?: (count: number, bytes: number) => void): Promise<void> {
  const entries=directoryEntries(directory,directory.name,signal);
  let failure: unknown, count=0, total=0;
  async function worker() {
    while(!failure) {
      try {
        if(signal.aborted)throw new Error("Upload paused.");
        const next=await abortableMetadata(entries.next(),signal);
        if(next.done||failure)return;
        const [path,handle]=next.value;
        const file=await abortableMetadata(handle.getFile(),signal);
        if(failure)return;
        if(signal.aborted)throw new Error("Upload paused.");
        total+=checkedSourceSize(path,file,"folder");
        discovered?.(++count,total);
        await send(path,file);
      } catch(error) {failure=error||new Error("Dataset upload stopped.");}
    }
  }
  try {
    await Promise.all(Array.from({length:4},worker));
    if(failure)throw failure;
    if(!count)throw new Error("Selected folder contains no supported files.");
  } finally {
    // Browser directory I/O is not cancellable. Do not hold Pause behind an
    // OS metadata read; abandoned results cannot schedule any further uploads.
    const closed=entries.return(undefined);
    if(failure||signal.aborted)void closed.catch(()=>undefined);
    else await closed;
  }
}
export function validateOffset(value: number, size: number): number {
  if (!Number.isSafeInteger(value) || value < 0 || value > size) throw new Error("Server returned invalid saved upload progress.");
  return value;
}
/** Hide per-file network latency with four requests; settle active writes on error. */
export async function uploadSources(sources: Map<string, File>,
  send: (path: string, file: File) => Promise<void>): Promise<void> {
  const entries = sources.entries();
  let failure: unknown;
  async function worker() {
    while (!failure) {
      const next = entries.next();
      if (next.done) return;
      try { await send(next.value[0], next.value[1]); }
      catch (error) { failure = error || new Error("Dataset upload stopped."); }
    }
  }
  await Promise.all(Array.from({length: Math.min(4, sources.size)}, worker));
  if (failure) throw failure;
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
