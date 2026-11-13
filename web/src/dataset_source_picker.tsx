import { FileArchive, FolderOpen, LoaderCircle } from "lucide-react";
import type { RefObject } from "react";
import { useEffect } from "react";
import { flushSync } from "react-dom";
import type { useDatasetSelection } from "./dataset_selection";
import type { DatasetDirectoryHandle, UploadKind } from "./upload_helpers";

type PickerWindow=Window & {showDirectoryPicker?: (options:{mode:"read"})=>Promise<DatasetDirectoryHandle>};

export function DatasetSourcePicker({kind,selection,inputRef,disabled,folderLabel,zipLabel,append=false}:{
  kind:UploadKind;selection:ReturnType<typeof useDatasetSelection>;
  inputRef:RefObject<HTMLInputElement|null>;disabled:boolean;
  folderLabel:string;zipLabel:string;append?:boolean;
}) {
  useEffect(()=>{
    const input=inputRef.current;
    const cancelled=()=>selection.pickerCancelled();
    input?.addEventListener("cancel",cancelled);
    return()=>input?.removeEventListener("cancel",cancelled);
  },[inputRef,selection.pickerCancelled]);
  const progressive=kind==="folder"&&typeof (window as PickerWindow).showDirectoryPicker==="function";
  function legacyPicker(){flushSync(selection.openPicker);inputRef.current?.click();}
  function chooseFolder(){
    flushSync(selection.openPicker);
    const picker=(window as PickerWindow).showDirectoryPicker;
    if(!picker){inputRef.current?.click();return;}
    // Keep the permission prompt inside the click's user activation.
    void picker.call(window,{mode:"read"}).then(selection.selectDirectory).catch(error=>{
      if(error?.name==="AbortError")selection.pickerCancelled();
      else selection.pickerFailed("The browser could not open this folder. Try again or use the standard folder picker below.");
    });
  }
  const locked=disabled||selection.phase==="checking";
  return <div className="dataset-picker-control">
    <button type="button" className="dataset-drop dataset-file-picker" disabled={locked}
      onClick={kind==="folder"?chooseFolder:legacyPicker}>
      <span className="dataset-drop-icon">{kind==="folder"?<FolderOpen size={25}/>:<FileArchive size={25}/>}</span>
      <strong>{kind==="folder"?folderLabel:zipLabel}</strong>
      <span className="dataset-picker-hint">{selection.directory?
        `${selection.directory.name} selected · Files are read progressively during upload`:
        kind==="folder"?"Select the root folder containing your species":"ZIP archive containing species and video folders"}</span>
    </button>
    <input ref={inputRef} className="dataset-native-input" type="file" tabIndex={-1}
      aria-label={append?(kind==="folder"?"Append folder dataset":"Append ZIP dataset"):(kind==="folder"?"Folder dataset":"ZIP dataset")}
      multiple={kind==="folder"} accept={kind==="zip"?".zip,application/zip":undefined}
      {...(kind==="folder"?{webkitdirectory:"",directory:""}:{})}
      disabled={locked} onChange={e=>void selection.selectFiles(e.target.files)}/>
    {progressive&&<button className="link-button dataset-picker-fallback" type="button"
      disabled={locked} onClick={legacyPicker}>Use standard folder picker</button>}
    {selection.phase==="choosing"&&<p className="dataset-selection-status" role="status">
      <LoaderCircle size={15} className="refresh-icon-spinning"/>
      Waiting for the browser picker. Large folders may take time to list.
    </p>}
    {selection.phase==="checking"&&<div className="dataset-selection-status" role="status">
      <LoaderCircle size={15} className="refresh-icon-spinning"/>
      <span>Reading selected files: {selection.progress.count.toLocaleString()} / {selection.progress.total.toLocaleString()}</span>
      <progress value={selection.progress.count} max={selection.progress.total||1}/>
    </div>}
    {selection.error&&<p className="alert danger" role="alert">{selection.error}</p>}
  </div>;
}
