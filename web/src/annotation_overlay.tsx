import { useEffect, useMemo, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { api } from "./api";

type Point={x:number;y:number};
type VisualObject={title:string;shape:"rectangle"|"point";points:Point[];color:string};
type AnnotationPalette={items:{name:string;shape:string;color:string}[]};
const DEFAULT_COLORS:Record<string,string>={
  bird:"#FFD600",left_eye:"#00C853",left_eye_occ:"#91D7A9",
  right_eye:"#00B8D4",right_eye_occ:"#8BCED9",
  beak:"#FF6D00",beak_occ:"#F5B282",
  crown:"#AA00FF",crown_occ:"#D3A1F0",
};
const asRecord=(value:unknown):Record<string,unknown>|null=>
  typeof value==="object"&&value!==null&&!Array.isArray(value)?
    value as Record<string,unknown>:null;
function asPoint(value:unknown):Point|null{
  if(!Array.isArray(value)||value.length<2)return null;
  const [x,y]=value;
  return typeof x==="number"&&Number.isFinite(x)&&
    typeof y==="number"&&Number.isFinite(y)?{x,y}:null;
}
function parseVisuals(annotation:Record<string,unknown>|null,
  colors:Record<string,string>):VisualObject[]{
  const rows=annotation?.objects;
  if(!Array.isArray(rows))return [];
  const shapes:VisualObject[]=[];
  for(const original of rows){
    const object=asRecord(original);
    if(!object||typeof object.classTitle!=="string")continue;
    const points=asRecord(object.points);
    if(!points)continue;
    const title=object.classTitle;
    const shape=object.geometryType;
    const exterior=points.exterior;
    let normalized:Point[]=[];
    if(shape==="point"){
      const p=asPoint(exterior);
      const nested=Array.isArray(exterior)&&exterior.length===1?
        asPoint(exterior[0]):null;
      if(p||nested)normalized=[(p||nested)!];
    }else if(shape==="rectangle"&&Array.isArray(exterior)){
      normalized=exterior.slice(0,2).map(asPoint).filter(
        (p):p is Point=>p!==null);
    }
    if(!normalized.length||(shape==="rectangle"&&normalized.length!==2))continue;
    if(shape!=="point"&&shape!=="rectangle")continue;
    shapes.push({title,shape,points:normalized,
      color:colors[title]||"#FFFFFF"});
  }
  return shapes;
}
export function AnnotationFrame({src,alt,annotation}:{
  src:string;alt:string;annotation:Record<string,unknown>|null
}){
  const [visible,setVisible]=useState(true);
  const [imageSize,setImageSize]=useState<{width:number;height:number}|null>(null);
  const [palette,setPalette]=useState<Record<string,string>>(DEFAULT_COLORS);
  useEffect(()=>{
    let active=true;
    void api<AnnotationPalette>("/labeling/annotation-classes")
      .then(result=>{if(active)setPalette(Object.fromEntries(result.items.map(
        cls=>[cls.name,cls.color])));})
      .catch(()=>{});
    return()=>{active=false;};
  },[]);
  const metaSize=asRecord(annotation?.size);
  const metaWidth=metaSize?.width;
  const metaHeight=metaSize?.height;
  const width=typeof metaWidth==="number"&&metaWidth>0?metaWidth:imageSize?.width||0;
  const height=typeof metaHeight==="number"&&metaHeight>0?metaHeight:imageSize?.height||0;
  const objects=useMemo(()=>parseVisuals(annotation,palette),[annotation,palette]);
  const pointRadius=Math.max(3,Math.min(width,height)*0.008);
  return <div className="review-annotation-visual">
    <div className="annotation-view-toolbar">
      <span>{annotation?objects.length+" objects in Supervisely annotation":"Original source frame"}</span>
      <button type="button" className="btn secondary" onClick={()=>setVisible(x=>!x)}>
        {visible?<EyeOff size={14}/>:<Eye size={14}/>}
        {visible?"Hide overlay":"Show overlay"}
      </button>
    </div>
    <div className="annotation-image-stage">
      <img src={src} alt={alt} className="review-preview"
        onLoad={event=>setImageSize({
          width:event.currentTarget.naturalWidth,
          height:event.currentTarget.naturalHeight,
        })}/>
      {visible&&width>0&&height>0&&objects.length>0&&
        <svg className="annotation-image-overlay"
          viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet"
          role="img" aria-label="Saved Supervisely bird bounding boxes and keypoints">
          {objects.map((object,index)=>object.shape==="rectangle"?(()=>{
            const a=object.points[0],b=object.points[1];
            return <g key={index}>
              <rect x={Math.min(a.x,b.x)} y={Math.min(a.y,b.y)}
                width={Math.abs(b.x-a.x)} height={Math.abs(b.y-a.y)}
                fill="none" stroke="#121827" strokeWidth={Math.max(4,pointRadius*.9)}/>
              <rect x={Math.min(a.x,b.x)} y={Math.min(a.y,b.y)}
                width={Math.abs(b.x-a.x)} height={Math.abs(b.y-a.y)}
                fill="none" stroke={object.color} strokeWidth={Math.max(2,pointRadius*.55)}/>
              <text x={Math.min(a.x,b.x)+3} y={Math.max(14,Math.min(a.y,b.y)-5)}
                stroke="#15212D" strokeWidth={Math.max(3,pointRadius*.6)}
                paintOrder="stroke" fill={object.color} fontSize={Math.max(12,pointRadius*2)}
                fontWeight="bold">{object.title}</text>
            </g>;
          })():<g key={index}>
            <circle cx={object.points[0].x} cy={object.points[0].y}
              r={pointRadius*1.4} fill="#141b28"/>
            <circle cx={object.points[0].x} cy={object.points[0].y}
              r={pointRadius} fill={object.color} stroke="#fff" strokeWidth={Math.max(1,pointRadius*.2)}/>
            <text x={object.points[0].x+pointRadius*1.7}
              y={object.points[0].y-pointRadius*.9}
              stroke="#0E1822" strokeWidth={Math.max(3,pointRadius*.6)}
              paintOrder="stroke" fill={object.color}
              fontSize={Math.max(11,pointRadius*1.7)} fontWeight="bold">
              {object.title}
            </text>
          </g>)}
        </svg>}
    </div>
    <div className="annotation-legend">
      {Object.entries(DEFAULT_COLORS).map(([name,color])=>
        <span key={name}><i style={{backgroundColor:palette[name]||color}}/>{name}</span>)}
    </div>
    {annotation&&objects.length===0&&
      <p className="muted small">No supported point or rectangle annotations are present in this downloaded frame.</p>}
  </div>;
}
