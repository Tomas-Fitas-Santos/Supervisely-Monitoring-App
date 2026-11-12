import { useState } from "react";
import { Database, BarChart3, CloudUpload, FolderOpen } from "lucide-react";
import { type Auth } from "./api";
import { DatasetScreen } from "./datasets";
import { MountedDatasetLibrary } from "./mounted_dataset_library";
import { CoverageScreen } from "./coverage";

export function DatasetWorkspace({auth}:{auth:Auth}){
  const canInspectCoverage=auth.staff.roles.some(r=>["organiser","monitor","observer"].includes(r));
  // The original ZIP/folder import remains the default. Direct host mounting
  // is optional, and neither path changes the job or review contracts.
  const [subtab,setSubtab]=useState<"upload"|"mount"|"coverage">("upload");
  return <div className="dataset-workspace">
    <div className="page-body dataset-workspace-top">
      <div className="page-heading"><div>
        <span className="eyebrow">DATA OPERATIONS</span><h2>Dataset</h2>
        <p className="muted">Upload a folder or ZIP, or link a host directory, then inspect dataset coverage.</p>
      </div><Database size={22}/></div>
      <nav className="workspace-subtabs" aria-label="Dataset sections">
        <button className={subtab==="upload"?"active":""} onClick={()=>setSubtab("upload")}>
          <CloudUpload size={16}/> Upload folder or ZIP</button>
        <button className={subtab==="mount"?"active":""} onClick={()=>setSubtab("mount")}>
          <FolderOpen size={16}/> Link host folder (optional)</button>
        {canInspectCoverage&&<button className={subtab==="coverage"?"active":""}
          onClick={()=>setSubtab("coverage")}><BarChart3 size={16}/> Coverage & classification</button>}
      </nav>
    </div>
    {subtab==="mount"?<MountedDatasetLibrary auth={auth}/>:
      subtab==="coverage"&&canInspectCoverage?<CoverageScreen auth={auth}/>:
      <DatasetScreen auth={auth}/>}
  </div>;
}
