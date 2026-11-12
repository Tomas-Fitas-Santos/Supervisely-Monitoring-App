import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Eye, EyeOff } from "lucide-react";
import { api } from "./api";

type Point={x:number;y:number};
type VisualObject={index:number;title:string;shape:"rectangle"|"point";points:Point[];color:string};
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
  const rows=annotation?.objects??annotation?.labels;
  if(!Array.isArray(rows))return [];
  const shapes:VisualObject[]=[];
  for(const [index,original] of rows.entries()){
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
    shapes.push({index,title,shape,points:normalized,
      color:colors[title]||"#FFFFFF"});
  }
  return shapes;
}
export function AnnotationFrame({src,alt,annotation,zoom}:{
  src:string;alt:string;annotation:Record<string,unknown>|null;
  zoom?:number;
}){
  const [visible,setVisible]=useState(true);
  const [hiddenClasses,setHiddenClasses]=useState<string[]>([]);
  const [imageSize,setImageSize]=useState<{width:number;height:number}|null>(null);
  const [palette,setPalette]=useState<Record<string,string>>(DEFAULT_COLORS);
  const [pan,setPan]=useState<Point>({x:0,y:0});
  const [dragging,setDragging]=useState(false);
  const stageRef=useRef<HTMLDivElement|null>(null);
  const dragRef=useRef<{
    pointerId:number;clientX:number;clientY:number;origin:Point;
  }|null>(null);
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
  const usedClasses=useMemo(()=>[...new Set(objects.map(object=>object.title))],[objects]);
  const displayed=useMemo(()=>objects.filter(object=>!hiddenClasses.includes(object.title)),
    [objects,hiddenClasses]);
  useEffect(()=>setHiddenClasses([]),[src]);
  const pointRadius=Math.max(3,Math.min(width,height)*0.008);
  const magnification=zoom??1;

  // Fit the original image inside the fixed-size viewport. Pan limits are
  // calculated from its actual contained size, not the scrollable page size.
  function clampPan(position:Point,scale:number):Point{
    const stage=stageRef.current;
    const frameWidth=stage?.clientWidth??0,frameHeight=stage?.clientHeight??0;
    const sourceWidth=imageSize?.width??width,sourceHeight=imageSize?.height??height;
    if(scale<=1||!frameWidth||!frameHeight||!sourceWidth||!sourceHeight){
      return {x:0,y:0};
    }
    const ratio=sourceWidth/sourceHeight;
    const fittedWidth=Math.min(frameWidth,frameHeight*ratio);
    const fittedHeight=Math.min(frameHeight,frameWidth/ratio);
    const limitX=Math.max(0,(fittedWidth*scale-frameWidth)/2);
    const limitY=Math.max(0,(fittedHeight*scale-frameHeight)/2);
    return {
      x:Math.min(limitX,Math.max(-limitX,position.x)),
      y:Math.min(limitY,Math.max(-limitY,position.y)),
    };
  }

  useEffect(()=>{
    setPan(value=>clampPan(value,magnification));
  },[magnification,imageSize,width,height]);

  useEffect(()=>{
    const stage=stageRef.current;
    if(!stage||zoom===undefined)return;
    const observer=new ResizeObserver(()=>{
      setPan(value=>clampPan(value,magnification));
    });
    observer.observe(stage);
    return ()=>observer.disconnect();
  },[magnification,imageSize,width,height,zoom]);

  function startPan(event:ReactPointerEvent<HTMLDivElement>){
    if(magnification<=1||event.button!==0)return;
    dragRef.current={
      pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY,
      origin:pan,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
    event.preventDefault();
  }
  function movePan(event:ReactPointerEvent<HTMLDivElement>){
    const active=dragRef.current;
    if(!active||active.pointerId!==event.pointerId)return;
    setPan(clampPan({
      x:active.origin.x+event.clientX-active.clientX,
      y:active.origin.y+event.clientY-active.clientY,
    },magnification));
  }
  function stopPan(event:ReactPointerEvent<HTMLDivElement>){
    if(dragRef.current?.pointerId!==event.pointerId)return;
    dragRef.current=null;
    setDragging(false);
    if(event.currentTarget.hasPointerCapture(event.pointerId)){
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return <div className="review-annotation-visual">
    <div className="annotation-view-toolbar">
      <span>{annotation?displayed.length+" of "+objects.length+" annotations visible":"Original source frame"}</span>
      <button type="button" className="btn secondary" onClick={()=>setVisible(x=>!x)}>
        {visible?<EyeOff size={14}/>:<Eye size={14}/>}
        {visible?"Hide overlay":"Show overlay"}
      </button>
    </div>
    <div ref={stageRef}
      className={"annotation-image-stage"+(zoom===undefined?"":" is-zoomable")+
        (magnification>1?" is-magnified":"")+(dragging?" is-panning":"")}
      onPointerDown={startPan} onPointerMove={movePan}
      onPointerUp={stopPan} onPointerCancel={stopPan}
      onLostPointerCapture={()=>{dragRef.current=null;setDragging(false);}}>
      <div className="annotation-image-layer"
        style={zoom===undefined?undefined:{
          transform:`translate(${pan.x}px, ${pan.y}px) scale(${magnification})`,
        }}>
      <img src={src} alt={alt} className="review-preview"
        onLoad={event=>setImageSize({
          width:event.currentTarget.naturalWidth,
          height:event.currentTarget.naturalHeight,
        })}/>
      {visible&&width>0&&height>0&&displayed.length>0&&
        <svg className="annotation-image-overlay"
          viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet"
          role="img" aria-label="Saved Supervisely bird bounding boxes and keypoints">
          {displayed.map(object=>object.shape==="rectangle"?(()=>{
            const a=object.points[0],b=object.points[1];
            return <g key={object.index} className="annotation-shape">
              <rect x={Math.min(a.x,b.x)} y={Math.min(a.y,b.y)}
                width={Math.abs(b.x-a.x)} height={Math.abs(b.y-a.y)}
                fill="none" stroke="#121827" strokeWidth={Math.max(4,pointRadius*.9)}/>
              <rect x={Math.min(a.x,b.x)} y={Math.min(a.y,b.y)}
                width={Math.abs(b.x-a.x)} height={Math.abs(b.y-a.y)}
                fill="none" stroke={object.color}
                strokeWidth={Math.max(2,pointRadius*.55)}/>
              <text x={Math.min(a.x,b.x)+3} y={Math.max(14,Math.min(a.y,b.y)-5)}
                stroke="#15212D" strokeWidth={Math.max(3,pointRadius*.6)}
                paintOrder="stroke" fill={object.color} fontSize={Math.max(12,pointRadius*2)}
                fontWeight="bold">{object.index+1}. {object.title}</text>
            </g>;
          })():<g key={object.index} className="annotation-shape">
            <circle cx={object.points[0].x} cy={object.points[0].y}
              r={pointRadius*1.4} fill="#141b28"/>
            <circle cx={object.points[0].x} cy={object.points[0].y}
              r={pointRadius} fill={object.color} stroke="#fff"
              strokeWidth={Math.max(1,pointRadius*.2)}/>
            <text x={object.points[0].x+pointRadius*1.7}
              y={object.points[0].y-pointRadius*.9}
              stroke="#0E1822" strokeWidth={Math.max(3,pointRadius*.6)}
              paintOrder="stroke" fill={object.color}
              fontSize={Math.max(11,pointRadius*1.7)} fontWeight="bold">
              {object.index+1}. {object.title}
            </text>
          </g>)}
        </svg>}
      </div>
    </div>
    {usedClasses.length>0&&<div className="annotation-legend"
      role="group" aria-label="Toggle visible annotation classes">
      {usedClasses.map(name=>{
        const enabled=!hiddenClasses.includes(name);
        return <button key={name} type="button"
          className={"annotation-legend-toggle"+(enabled?"":" is-hidden")}
          aria-pressed={enabled}
          aria-label={(enabled?"Hide ":"Show ")+name+" annotations"}
          onClick={()=>setHiddenClasses(current=>
            current.includes(name)?current.filter(entry=>entry!==name):[...current,name])}>
          <i style={{backgroundColor:palette[name]||DEFAULT_COLORS[name]||"#FFFFFF"}}/>
          {name}
          {enabled?<Eye size={12}/>:<EyeOff size={12}/>}
        </button>;
      })}
    </div>}
    {annotation&&objects.length===0&&
      <p className="muted small">No supported point or rectangle annotations are present in this downloaded frame.</p>}
  </div>;
}
