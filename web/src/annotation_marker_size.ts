export function keypointSize(width:number,height:number,zoom=1){
  const normalRadius=Math.max(1,Math.min(width,height)*0.004);
  const magnification=Number.isFinite(zoom)&&zoom>1?zoom:1;
  // Image-layer zoom moves the points with the image. Counter-scale only
  // their dimensions so dots and outlines keep the same size on screen.
  return {radius:normalRadius/magnification,
    strokeWidth:Math.max(0.35,normalRadius*0.2)/magnification};
}
