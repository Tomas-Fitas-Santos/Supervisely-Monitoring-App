/** Coalesce overlapping reads, including manual refreshes, without caching results. */
export function singleFlight<T>(read:()=>Promise<T>):()=>Promise<T>{
  let pending:Promise<T>|null=null;
  return()=>{
    if(!pending){
      pending=Promise.resolve().then(read).finally(()=>{pending=null;});
    }
    return pending;
  };
}
