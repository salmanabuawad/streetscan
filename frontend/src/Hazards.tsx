import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { api } from './services/api';
import { AuthImg } from './AuthMedia';

// ---- shared vocab ----
const SEV_COLOR: Record<string,string> = { low:'#64748b', medium:'#f59e0b', high:'#f97316', critical:'#dc2626' };
const SEV_HE: Record<string,string> = { low:'נמוכה', medium:'בינונית', high:'גבוהה', critical:'קריטית' };
const STATUS_HE: Record<string,string> = {
  pending_review:'ממתין לבדיקה', open:'פתוח', in_progress:'בטיפול',
  likely_fixed:'כנראה תוקן', closed:'סגור', reopened:'נפתח מחדש', rejected:'נדחה',
};
const SOURCE_HE: Record<string,string> = { ai:'AI', staff:'עובד', resident:'תושב', hotline:'מוקד' };
const DEPARTMENTS = ['הנדסה','מים וביוב','תברואה','פיקוח','תחבורה','שפ"ע','רישוי עסקים','חשמל ותחזוקה','מוקד חירום'];

type Cat = { key:string; name_he:string; group:string; color:string; icon:string; department:string };
type Hazard = {
  id:number; category_key:string; category_he:string; color:string; group:string;
  subtype:string|null; status:string; severity:string; confidence:number;
  lat:number|null; lng:number|null; location_accuracy_m:number|null;
  department:string|null; source:string; observation_count:number; distinct_scan_days:number;
  first_detected_at:string; last_detected_at:string; age_days:number;
  estimated_size:string|null; blocks_path:boolean; near_sensitive:boolean; is_danger:boolean;
  best_observation_id:number|null;
};
type ReviewItem = {
  id:number; hazard_id:number|null; category_key:string; category_he:string; color:string;
  confidence:number; band:string; severity:string|null; lat:number|null; lng:number|null;
  location_accuracy_m:number|null; image_quality:string|null; quality_flags:string|null;
  detector:string; captured_at:string|null; has_image:boolean;
};

const BUQATA: [number,number] = [33.2007, 35.7772];

export default function Hazards() {
  const [view, setView] = useState<'overview'|'review'|'map'|'list'>('overview');
  const [cats, setCats] = useState<Cat[]>([]);
  useEffect(() => { api<Cat[]>('/hazards/categories').then(setCats).catch(console.error); }, []);
  const catBy = useMemo(() => Object.fromEntries(cats.map(c => [c.key, c])), [cats]);

  return (
    <section className="hz">
      <div className="section-head">
        <div><h2>מפגעים בכביש ובמרחב הציבורי</h2><p>זיהוי AI, בדיקת עובד, מעקב וטיפול.</p></div>
      </div>
      <div className="hz-subnav">
        <button className={view==='overview'?'active':''} onClick={()=>setView('overview')}>סקירה</button>
        <button className={view==='review'?'active':''} onClick={()=>setView('review')}>בדיקת זיהויי AI</button>
        <button className={view==='map'?'active':''} onClick={()=>setView('map')}>מפת מפגעים</button>
        <button className={view==='list'?'active':''} onClick={()=>setView('list')}>ניהול מפגעים</button>
      </div>
      {view==='overview' && <Overview/>}
      {view==='review' && <Review catBy={catBy}/>}
      {view==='map' && <HazardMap catBy={catBy}/>}
      {view==='list' && <HazardList/>}
    </section>
  );
}

// ---------------- Overview / dashboard ----------------
function Overview() {
  const [d, setD] = useState<any>(null);
  useEffect(() => { api('/hazards/dashboard').then(setD).catch(console.error); }, []);
  if (!d) return <div className="hz-loading">טוען…</div>;
  const bar = (obj:Record<string,number>, color?:(k:string)=>string) => {
    const max = Math.max(1, ...Object.values(obj));
    return <div className="hz-bars">{Object.entries(obj).sort((a,b)=>b[1]-a[1]).map(([k,v])=>(
      <div className="hz-bar-row" key={k}>
        <span className="hz-bar-label">{STATUS_HE[k]||SEV_HE[k]||k}</span>
        <span className="hz-bar-track"><i style={{width:`${100*v/max}%`,background:color?color(k):'#3b82f6'}}/></span>
        <span className="hz-bar-val">{v}</span>
      </div>))}</div>;
  };
  return <div className="hz-overview">
    <div className="hz-tiles">
      <Tile label="סה״כ מפגעים" value={d.hazards_total}/>
      <Tile label="זיהויים" value={d.observations_total}/>
      <Tile label="נבדקו" value={d.reviewed}/>
      <Tile label="אושרו" value={d.approved} tone="ok"/>
      <Tile label="False Positives" value={d.false_positives} tone="bad"/>
      <Tile label="דיוק כללי" value={d.overall_precision!=null?`${Math.round(d.overall_precision*100)}%`:'—'}/>
    </div>
    <div className="hz-panels">
      <div className="hz-panel"><h4>לפי סטטוס</h4>{bar(d.by_status)}</div>
      <div className="hz-panel"><h4>לפי חומרה</h4>{bar(d.by_severity, k=>SEV_COLOR[k]||'#3b82f6')}</div>
      <div className="hz-panel"><h4>לפי סוג מפגע</h4>{bar(d.by_category)}</div>
      <div className="hz-panel"><h4>דיוק לפי סוג</h4>
        <div className="hz-bars">{Object.entries(d.precision_by_category||{}).map(([k,v]:any)=>(
          <div className="hz-bar-row" key={k}>
            <span className="hz-bar-label">{k}</span>
            <span className="hz-bar-track"><i style={{width:`${100*(v||0)}%`,background:'#22c55e'}}/></span>
            <span className="hz-bar-val">{v!=null?`${Math.round(v*100)}%`:'—'}</span>
          </div>))}</div>
      </div>
    </div>
  </div>;
}
function Tile({label,value,tone}:{label:string;value:any;tone?:string}) {
  return <div className={`hz-tile ${tone||''}`}><span className="hz-tile-v">{value}</span><span className="hz-tile-l">{label}</span></div>;
}

// ---------------- AI review ----------------
function Review({catBy}:{catBy:Record<string,Cat>}) {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [busy, setBusy] = useState<number|null>(null);
  const load = () => api<{items:ReviewItem[]}>('/hazards/review').then(r=>setItems(r.items)).catch(console.error);
  useEffect(() => { load(); }, []);
  const act = async (o:ReviewItem, path:string, body?:any) => {
    setBusy(o.id);
    try { await api(`/hazards/observations/${o.id}/${path}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
      setItems(prev=>prev.filter(x=>x.id!==o.id)); } catch(e){ alert('שגיאה: '+e); } finally { setBusy(null); }
  };
  if (!items.length) return <div className="hz-empty">אין זיהויים הממתינים לבדיקה. סריקות חדשות יופיעו כאן.</div>;
  return <div className="hz-review-grid">
    {items.map(o => {
      const cat = catBy[o.category_key];
      return <div className="hz-card" key={o.id}>
        <div className="hz-card-media">
          {o.has_image ? <AuthImg path={`/hazards/observations/${o.id}/image`} alt={o.category_he}/>
                       : <div className="hz-noimg">אין תמונה</div>}
          <span className="hz-badge" style={{background:SEV_COLOR[o.severity||'medium']}}>{SEV_HE[o.severity||'medium']}</span>
        </div>
        <div className="hz-card-body">
          <div className="hz-card-title">
            <span className="hz-dot" style={{background:o.color||cat?.color}}/>
            <select defaultValue={o.category_key} id={`cat-${o.id}`} className="hz-inline-select">
              {Object.values(catBy).map(c=><option key={c.key} value={c.key}>{c.name_he}</option>)}
            </select>
          </div>
          <div className="hz-card-meta">
            <span>ביטחון {Math.round(o.confidence*100)}%</span>
            <span className={`hz-band hz-band-${o.band}`}>{o.band}</span>
            {o.quality_flags && <span className="hz-flag">⚠ {o.quality_flags}</span>}
            {o.lat!=null && <span>±{Math.round(o.location_accuracy_m||0)}מ׳</span>}
          </div>
          <div className="hz-card-actions">
            <button className="hz-approve" disabled={busy===o.id}
              onClick={()=>{const c=(document.getElementById(`cat-${o.id}`) as HTMLSelectElement)?.value; act(o,'approve',{category_key:c!==o.category_key?c:undefined});}}>אשר</button>
            <button className="hz-reject" disabled={busy===o.id} onClick={()=>act(o,'reject')}>False Positive</button>
          </div>
        </div>
      </div>;
    })}
  </div>;
}

// ---------------- Hazard GIS map ----------------
function HazardMap({catBy}:{catBy:Record<string,Cat>}) {
  const el = useRef<HTMLDivElement|null>(null);
  const map = useRef<L.Map|null>(null);
  const layer = useRef<L.LayerGroup|null>(null);
  const [hazards, setHazards] = useState<Hazard[]>([]);
  const [fSev, setFSev] = useState<Set<string>>(new Set());
  const [fStatus, setFStatus] = useState<Set<string>>(new Set());
  const [sel, setSel] = useState<Hazard|null>(null);

  useEffect(() => { api<{hazards:Hazard[]}>('/hazards/map').then(r=>setHazards(r.hazards)).catch(console.error); }, []);
  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { minZoom:14, maxBounds:L.latLngBounds([[33.187,35.762],[33.216,35.797]]), maxBoundsViscosity:1 }).setView(BUQATA, 15);
    map.current = m;
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    setTimeout(()=>m.invalidateSize(), 120);
  }, []);
  useEffect(() => {
    const lg = layer.current; if (!lg) return; lg.clearLayers();
    for (const h of hazards) {
      if (h.lat==null || h.lng==null) continue;
      if (fSev.has(h.severity) || fStatus.has(h.status)) continue;
      const mk = L.circleMarker([h.lat,h.lng], {
        radius: h.severity==='critical'?11:h.severity==='high'?9:7, weight:2,
        color:'#0b1220', fillColor:SEV_COLOR[h.severity]||'#888',
        fillOpacity: h.status==='closed'||h.status==='likely_fixed'?0.35:0.9,
      });
      mk.on('click', ()=>setSel(h));
      lg.addLayer(mk);
    }
  }, [hazards, fSev, fStatus]);

  const sevCounts = useMemo(()=>{const c:Record<string,number>={};hazards.forEach(h=>c[h.severity]=(c[h.severity]||0)+1);return c;},[hazards]);
  const toggle = (set:Set<string>, setter:(s:Set<string>)=>void, k:string) => { const n=new Set(set); n.has(k)?n.delete(k):n.add(k); setter(n); };

  return <div className="hz-map-wrap">
    <div className="hz-map-filters">
      <div className="hz-filter-group"><b>חומרה:</b>{['critical','high','medium','low'].map(s=>(
        <label key={s} className="hz-chip"><input type="checkbox" checked={!fSev.has(s)} onChange={()=>toggle(fSev,setFSev,s)}/>
          <i style={{background:SEV_COLOR[s]}}/>{SEV_HE[s]} <em>{sevCounts[s]||0}</em></label>))}</div>
      <div className="hz-filter-group"><b>סטטוס:</b>{['open','in_progress','pending_review','reopened','likely_fixed','closed'].map(s=>(
        <label key={s} className="hz-chip"><input type="checkbox" checked={!fStatus.has(s)} onChange={()=>toggle(fStatus,setFStatus,s)}/>{STATUS_HE[s]}</label>))}</div>
    </div>
    <div ref={el} className="hz-map"/>
    {sel && <HazardModal h={sel} catBy={catBy} onClose={()=>setSel(null)} onChanged={()=>{api<{hazards:Hazard[]}>('/hazards/map').then(r=>setHazards(r.hazards));setSel(null);}}/>}
  </div>;
}

// ---------------- shared hazard modal (detail + actions) ----------------
function HazardModal({h, catBy, onClose, onChanged}:{h:Hazard; catBy:Record<string,Cat>; onClose:()=>void; onChanged:()=>void}) {
  const [detail, setDetail] = useState<any>(null);
  const [dept, setDept] = useState(h.department||catBy[h.category_key]?.department||'');
  const [busy, setBusy] = useState(false);
  useEffect(()=>{ api(`/hazards/${h.id}`).then(setDetail).catch(console.error); }, [h.id]);
  const doAct = async (path:string, body:any) => {
    setBusy(true);
    try { await api(`/hazards/${h.id}/${path}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); onChanged(); }
    catch(e){ alert('שגיאה: '+e); } finally { setBusy(false); }
  };
  const bestObs = detail?.observations?.find((o:any)=>o.id===h.best_observation_id) || detail?.observations?.[0];
  return <div className="modal-backdrop above-map" onClick={onClose}>
    <div className="modal hz-modal" onClick={e=>e.stopPropagation()}>
      <div className="hz-modal-head">
        <h3><span className="hz-dot" style={{background:h.color}}/>{h.category_he}</h3>
        <button className="icon-danger" onClick={onClose}>✕</button>
      </div>
      {bestObs?.has_image
        ? <AuthImg path={`/hazards/observations/${bestObs.id}/image`} alt={h.category_he}/>
        : <div className="hz-noimg wide">אין תמונת זיהוי</div>}
      <div className="hz-modal-badges">
        <span className="hz-badge" style={{background:SEV_COLOR[h.severity]}}>{SEV_HE[h.severity]}</span>
        <span className="hz-badge alt">{STATUS_HE[h.status]}</span>
        {h.is_danger && <span className="hz-badge danger">סכנה</span>}
        {h.blocks_path && <span className="hz-badge">חוסם מעבר</span>}
        {h.near_sensitive && <span className="hz-badge">סמוך למוסד</span>}
      </div>
      <table className="hz-facts"><tbody>
        <tr><th>מקור</th><td>{SOURCE_HE[h.source]||h.source}</td></tr>
        <tr><th>ביטחון</th><td>{Math.round(h.confidence*100)}%</td></tr>
        <tr><th>זוהה</th><td>{h.observation_count} פעמים · {h.distinct_scan_days} ימי סריקה</td></tr>
        <tr><th>גיל</th><td>{h.age_days} ימים (מאז {new Date(h.first_detected_at).toLocaleDateString('he-IL')})</td></tr>
        <tr><th>מיקום</th><td>{h.lat?.toFixed(6)}, {h.lng?.toFixed(6)} <em>±{Math.round(h.location_accuracy_m||0)}מ׳</em></td></tr>
      </tbody></table>
      <div className="hz-modal-actions">
        <select value={dept} onChange={e=>setDept(e.target.value)}>{DEPARTMENTS.map(d=><option key={d}>{d}</option>)}</select>
        <button disabled={busy} onClick={()=>doAct('assign',{department:dept})}>הקצה למחלקה</button>
        {h.status!=='closed' && <button disabled={busy} className="hz-approve" onClick={()=>doAct('status',{status:'closed',force:true})}>סגור מפגע</button>}
        {h.status==='closed' && <button disabled={busy} onClick={()=>doAct('status',{status:'reopened'})}>פתח מחדש</button>}
      </div>
      {detail?.history?.length>0 && <div className="hz-history"><h4>היסטוריה</h4>
        {detail.history.map((s:any,i:number)=><div key={i} className="hz-hist-row">
          <span>{STATUS_HE[s.new]||s.new}</span><span className="hz-hist-note">{s.note}</span>
          <span className="hz-hist-at">{new Date(s.at).toLocaleDateString('he-IL')}</span></div>)}</div>}
    </div>
  </div>;
}

// ---------------- list / management ----------------
function HazardList() {
  const [rows, setRows] = useState<Hazard[]>([]);
  const [cats, setCats] = useState<Cat[]>([]);
  const [status, setStatus] = useState(''); const [severity, setSeverity] = useState(''); const [category, setCategory] = useState('');
  const [sel, setSel] = useState<Hazard|null>(null);
  const catBy = useMemo(()=>Object.fromEntries(cats.map(c=>[c.key,c])),[cats]);
  const load = () => { const q=new URLSearchParams(); if(status)q.set('status',status); if(severity)q.set('severity',severity); if(category)q.set('category',category);
    api<Hazard[]>(`/hazards?${q}`).then(setRows).catch(console.error); };
  useEffect(()=>{ api<Cat[]>('/hazards/categories').then(setCats); }, []);
  useEffect(()=>{ load(); }, [status, severity, category]);
  return <div>
    <div className="hz-list-filters">
      <select value={status} onChange={e=>setStatus(e.target.value)}><option value="">כל הסטטוסים</option>{Object.entries(STATUS_HE).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
      <select value={severity} onChange={e=>setSeverity(e.target.value)}><option value="">כל החומרות</option>{Object.entries(SEV_HE).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
      <select value={category} onChange={e=>setCategory(e.target.value)}><option value="">כל הסוגים</option>{cats.map(c=><option key={c.key} value={c.key}>{c.name_he}</option>)}</select>
      <span className="hz-count">{rows.length} מפגעים</span>
    </div>
    <div className="table-wrap"><table>
      <thead><tr><th>סוג</th><th>חומרה</th><th>סטטוס</th><th>מחלקה</th><th>מקור</th><th>תצפיות</th><th>גיל</th></tr></thead>
      <tbody>{rows.map(h=><tr key={h.id} className="hz-row" onClick={()=>setSel(h)}>
        <td><span className="hz-dot" style={{background:h.color}}/>{h.category_he}</td>
        <td><span className="hz-sev-pill" style={{background:SEV_COLOR[h.severity]}}>{SEV_HE[h.severity]}</span></td>
        <td>{STATUS_HE[h.status]}</td><td>{h.department||'—'}</td><td>{SOURCE_HE[h.source]}</td>
        <td>{h.observation_count}</td><td>{h.age_days} י׳</td>
      </tr>)}
      {!rows.length && <tr><td colSpan={7}>אין מפגעים בסינון זה.</td></tr>}</tbody>
    </table></div>
    {sel && <HazardModal h={sel} catBy={catBy} onClose={()=>setSel(null)} onChanged={()=>{load();setSel(null);}}/>}
  </div>;
}
