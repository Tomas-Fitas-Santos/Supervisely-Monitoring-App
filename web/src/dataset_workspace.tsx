import { useState } from "react";
import { Database, BarChart3, CloudUpload } from "lucide-react";
import { type Auth } from "./api";
import { DatasetScreen } from "./datasets";
import { CoverageScreen } from "./coverage";

export function DatasetWorkspace({auth}:{auth:Auth}){
  const canInspectCoverage=auth.staff.roles.some(r=>["organiser","monitor","observer"].includes(r));
  const [subtab,setSubtab]=useState<"library"|"coverage">("library");
  return <div className="dataset-workspace">
    <div className="page-body dataset-workspace-top">
      <div className="page-heading"><div>
        <span className="eyebrow">DATA OPERATIONS</span><h2>Dataset</h2>
        <p className="muted">Import and manage source material, then inspect species, video and morphology coverage.</p>
      </div><Database size={22}/></div>
      <nav className="workspace-subtabs" aria-label="Dataset sections">
        <button className={subtab==="library"?"active":""} onClick={()=>setSubtab("library")}>
          <CloudUpload size={16}/> Upload & manage</button>
        {canInspectCoverage&&<button className={subtab==="coverage"?"active":""}
          onClick={()=>setSubtab("coverage")}><BarChart3 size={16}/> Coverage & classification</button>}
      </nav>
    </div>
    {subtab==="library"||!canInspectCoverage?<DatasetScreen auth={auth}/>:<CoverageScreen auth={auth}/>}
  </div>;
}
