import { useEffect, useRef, useState } from "react";
import { validateDatasetSources, type DatasetDirectoryHandle,
  type PreparedDatasetSources, type UploadKind } from "./upload_helpers";

/** Selection is separate from upload: never reduce a 50k FileList during render. */
export function useDatasetSelection(kind: UploadKind) {
  const [files,setFiles]=useState<File[]>([]);
  const [directory,setDirectory]=useState<DatasetDirectoryHandle|null>(null);
  const [prepared,setPrepared]=useState<PreparedDatasetSources|null>(null);
  const [phase,setPhase]=useState<"idle"|"choosing"|"checking"|"ready"|"error">("idle");
  const [progress,setProgress]=useState({count:0,total:0,bytes:0});
  const [error,setError]=useState("");
  const controller=useRef<AbortController|null>(null);
  const clear=()=>{
    controller.current?.abort();controller.current=null;
    setFiles([]);setDirectory(null);setPrepared(null);setPhase("idle");setError("");
    setProgress({count:0,total:0,bytes:0});
  };
  useEffect(()=>()=>controller.current?.abort(),[]);
  const pickerCancelled=()=>setPhase(directory||prepared?"ready":"idle");
  async function selectFiles(input: FileList | null) {
    if(!input?.length){pickerCancelled();return;}
    controller.current?.abort();
    const current=new AbortController();controller.current=current;
    setPhase("checking");setError("");setDirectory(null);setPrepared(null);setFiles([]);
    setProgress({count:0,total:input.length,bytes:0});
    try {
      const checked=await validateDatasetSources(input,kind,current.signal,
        (count,bytes)=>{if(controller.current===current)setProgress({count,total:input.length,bytes});});
      if(controller.current!==current)return;
      setFiles(Array.from(checked.sources.values()));setPrepared(checked);setPhase("ready");
    } catch(e) {
      if(controller.current===current){setError((e as Error).message);setPhase("error");}
    }
  }
  return {files,directory,prepared,phase,progress,error,clear,selectFiles,
    ready:phase==="ready"&&Boolean(directory||prepared),
    openPicker:()=>{setError("");setPhase("choosing");},pickerCancelled,
    pickerFailed:(message: string)=>{setError(message);setPhase("error");},
    selectDirectory:(handle: DatasetDirectoryHandle)=>{
      controller.current?.abort();controller.current=null;
      setFiles([]);setPrepared(null);setDirectory(handle);setPhase("ready");setError("");
    }};
}
