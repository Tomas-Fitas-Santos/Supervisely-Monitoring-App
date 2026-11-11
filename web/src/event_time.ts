/** Event activity timestamps are always read and entered in Europe/Lisbon.
 *  HTML datetime-local inputs omit a timezone; never interpret them in the monitor's
 *  own browser timezone. DST gap/repeated-hour inputs are rejected explicitly.
 */
export const EVENT_TIMEZONE="Europe/Lisbon";
const formatter=new Intl.DateTimeFormat("en-GB",{
  timeZone:EVENT_TIMEZONE,
  year:"numeric",month:"2-digit",day:"2-digit",
  hour:"2-digit",minute:"2-digit",hourCycle:"h23"
});
const presentation=new Intl.DateTimeFormat("en-GB",{
  timeZone:EVENT_TIMEZONE,dateStyle:"medium",timeStyle:"short"
});
export function formatLisbonTime(epochSeconds:number|null):string{
  return epochSeconds===null?"Not scheduled":
    presentation.format(new Date(epochSeconds*1000))+" (Lisbon)";
}
function wallTime(instantMs:number):string{
  const parts=Object.fromEntries(formatter.formatToParts(new Date(instantMs))
    .filter(p=>p.type!=="literal").map(p=>[p.type,p.value]));
  return [parts.year,parts.month,parts.day].join("-")+"T"+
    [parts.hour,parts.minute].join(":");
}
export function parseLisbonTime(value:string):number|null{
  if(!value)return null;
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
    throw new Error("Enter a valid date and time in Lisbon.");
  const [y,m,d,h,min]=value.match(/\d+/g)!.map(Number);
  const guess=Date.UTC(y,m-1,d,h,min);
  const date=new Date(guess);
  if(date.getUTCFullYear()!==y||date.getUTCMonth()!==m-1||
     date.getUTCDate()!==d||date.getUTCHours()!==h||
     date.getUTCMinutes()!==min)
    throw new Error("The selected Lisbon date/time is invalid.");
  const candidates:number[]=[];
  for(let minutes=-120;minutes<=120;minutes+=15){
    const instant=guess+minutes*60_000;
    if(wallTime(instant)===value)candidates.push(instant);
  }
  if(candidates.length!==1)
    throw new Error(candidates.length===0?
      "This local time does not exist in Lisbon because the clocks change. Choose a different time.":
      "This local time occurs twice in Lisbon when the clocks change. Choose an unambiguous time.");
  return Math.floor(candidates[0]/1000);
}
