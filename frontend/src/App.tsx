import { useEffect, useRef, useState } from 'react';
import { Camera, MapPin, Database, Route as RouteIcon, UploadCloud, StopCircle, PlayCircle } from 'lucide-react';
import { api, API_URL } from './services/api';
import { queueSegment, queueGpsPoint, pendingCounts, startAutoFlush } from './services/offlineQueue';

const SEGMENT_MS = 15000;

function pickMimeType(): string {
  // iOS Safari records mp4; Chrome/Android record webm.
  const candidates = ['video/webm;codecs=vp8', 'video/webm', 'video/mp4'];
  return candidates.find(t => MediaRecorder.isTypeSupported(t)) || '';
}

type Dashboard = { assets:number; routes:number; detections:number; tickets:number; layers:string[] };
type Asset = {
  id:number; name:string; asset_type:string; layer:string; status:string;
  latitude?:number; longitude?:number; underground:boolean; source:string;
};

const layerLabels: Record<string,string> = {
  telecom:'תקשורת וטלפוניה', electricity:'חשמל', water:'מים', sewage:'ביוב',
  drainage:'ניקוז', tunnel:'תעלות ומעברים', road:'כבישים', public_space:'מרחב ציבורי'
};

export default function App() {
  const [tab, setTab] = useState<'record'|'assets'|'dashboard'>('record');
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [routeId, setRouteId] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState('מוכן');
  const [coords, setCoords] = useState<{lat:number;lng:number;accuracy:number}|null>(null);
  const [pending, setPending] = useState<{segments:number;gps:number}>({segments:0, gps:0});
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const watchId = useRef<number | null>(null);
  const recordingRef = useRef(false);
  const segmentTimer = useRef<number | null>(null);

  async function refresh() {
    setDashboard(await api<Dashboard>('/dashboard'));
    setAssets(await api<Asset[]>('/assets'));
  }

  async function refreshPending() {
    try { setPending(await pendingCounts()); } catch { /* IndexedDB unavailable */ }
  }

  useEffect(() => { refresh().catch(console.error) }, []);
  useEffect(() => {
    refreshPending();
    return startAutoFlush(() => refreshPending());
  }, []);

  async function uploadOrQueueSegment(routeId: number, blob: Blob, mimeType: string) {
    const capturedAt = new Date().toISOString();
    const ext = mimeType.includes('mp4') ? 'mp4' : 'webm';
    const filename = `segment-${Date.now()}.${ext}`;
    const fd = new FormData();
    fd.append('route_id', String(routeId));
    fd.append('captured_at', capturedAt);
    fd.append('file', blob, filename);
    try {
      const res = await fetch(`${API_URL}/video-segments`, { method:'POST', body:fd });
      if (!res.ok) throw new Error(String(res.status));
      if (recordingRef.current) setStatus('מקליט ומעלה לשרת');
    } catch {
      await queueSegment(routeId, capturedAt, blob, filename).catch(console.error);
      refreshPending();
      if (recordingRef.current) setStatus('אין חיבור — המקטע נשמר בדפדפן ויעלה אוטומטית');
    }
  }

  // Record one self-contained segment, then start the next one.
  // (A single recorder with a timeslice produces continuation chunks that are
  // not playable on their own, so each segment gets its own recorder.)
  function recordNextSegment(stream: MediaStream, routeId: number, mimeType: string) {
    if (!recordingRef.current || !stream.active) return;
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    mediaRecorder.current = recorder;
    const chunks: Blob[] = [];
    recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      if (chunks.length) {
        uploadOrQueueSegment(routeId, new Blob(chunks, { type: mimeType || 'video/webm' }), mimeType);
      }
      recordNextSegment(stream, routeId, mimeType);
    };
    recorder.start();
    segmentTimer.current = window.setTimeout(() => {
      if (recorder.state !== 'inactive') recorder.stop();
    }, SEGMENT_MS);
  }

  async function startRoute() {
    try {
      setStatus('פותח מסלול...');
      const route = await api<{id:number}>('/routes', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({vehicle_name:'Garbage Truck 1', driver_name:'Pilot'})
      });
      setRouteId(route.id);

      const stream = await navigator.mediaDevices.getUserMedia({
        video:{ facingMode:{ideal:'environment'}, width:{ideal:1280}, height:{ideal:720} },
        audio:false
      });
      streamRef.current = stream;
      recordingRef.current = true;
      recordNextSegment(stream, route.id, pickMimeType());

      watchId.current = navigator.geolocation.watchPosition(
        async p => {
          const c = {lat:p.coords.latitude, lng:p.coords.longitude, accuracy:p.coords.accuracy};
          setCoords(c);
          const body = {
            route_id:route.id, latitude:c.lat, longitude:c.lng,
            accuracy_m:c.accuracy, speed_mps:p.coords.speed,
            captured_at:new Date().toISOString()
          };
          try {
            await api('/gps', {
              method:'POST', headers:{'Content-Type':'application/json'},
              body:JSON.stringify(body)
            });
          } catch {
            await queueGpsPoint(body).catch(console.error);
            refreshPending();
            setStatus('GPS נשמר בדפדפן; ההעלאה תתחדש עם החיבור');
          }
        },
        () => setStatus('אין הרשאת GPS'),
        { enableHighAccuracy:true, maximumAge:3000, timeout:10000 }
      );

      setRecording(true);
      setStatus('מקליט מסלול');
    } catch (e) {
      recordingRef.current = false;
      setStatus(`שגיאה: ${String(e)}`);
    }
  }

  async function stopRoute() {
    recordingRef.current = false;
    if (segmentTimer.current !== null) window.clearTimeout(segmentTimer.current);
    if (mediaRecorder.current?.state !== 'inactive') mediaRecorder.current?.stop(); // last segment still uploads via onstop
    streamRef.current?.getTracks().forEach(t => t.stop());
    if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current);
    if (routeId) await api(`/routes/${routeId}/stop`, {method:'POST'}).catch(console.error);
    setRecording(false);
    setRouteId(null);
    setStatus('המסלול הסתיים');
    refresh();
  }

  return <div className="app-shell">
    <header className="topbar">
      <div>
        <h1>Buqata StreetScan</h1>
        <p>מיפוי תשתיות ומפגעים באמצעות רכב מועצה</p>
      </div>
      <span className={`status ${recording ? 'live':''}`}>{status}</span>
    </header>

    <nav className="tabs">
      <button onClick={()=>setTab('record')} className={tab==='record'?'active':''}><Camera size={18}/> הקלטה</button>
      <button onClick={()=>setTab('assets')} className={tab==='assets'?'active':''}><Database size={18}/> נכסים</button>
      <button onClick={()=>setTab('dashboard')} className={tab==='dashboard'?'active':''}><MapPin size={18}/> לוח בקרה</button>
    </nav>

    <main>
      {tab==='record' && <section className="panel hero">
        <div className="record-icon"><RouteIcon size={44}/></div>
        <h2>מסלול צילום ומיפוי</h2>
        <p>הטלפון מצלם וידאו, שולח GPS ומעלה מקטעים קצרים לשרת.</p>
        <div className="coords">
          <span>מסלול: {routeId ?? 'לא פעיל'}</span>
          <span>GPS: {coords ? `${coords.lat.toFixed(6)}, ${coords.lng.toFixed(6)} (±${Math.round(coords.accuracy)}m)` : 'ממתין'}</span>
        </div>
        {!recording
          ? <button className="primary big" onClick={startRoute}><PlayCircle/> התחל מסלול</button>
          : <button className="danger big" onClick={stopRoute}><StopCircle/> עצור מסלול</button>}
        <div className="notice">
          <UploadCloud size={20}/>
          <span>
            הווידאו מפוצל למקטעים של 15 שניות. במקרה של ניתוק המקטעים נשמרים ב־IndexedDB ועולים אוטומטית כשהחיבור חוזר.
            {(pending.segments > 0 || pending.gps > 0) && ` ממתינים להעלאה: ${pending.segments} מקטעים, ${pending.gps} נקודות GPS.`}
          </span>
        </div>
      </section>}

      {tab==='assets' && <section>
        <div className="section-head"><div><h2>מאגר נכסי תשתית</h2><p>נכסים גלויים ותשתיות תת־קרקעיות בשכבות GIS.</p></div></div>
        <div className="layer-grid">
          {dashboard?.layers.map(layer => <div className="layer-card" key={layer}>
            <strong>{layerLabels[layer] || layer}</strong>
            <span>{assets.filter(a=>a.layer===layer).length} נכסים</span>
          </div>)}
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>שם</th><th>סוג</th><th>שכבה</th><th>מקור</th><th>תת־קרקעי</th></tr></thead>
            <tbody>
              {assets.map(a=><tr key={a.id}>
                <td>{a.name}</td><td>{a.asset_type}</td><td>{layerLabels[a.layer]||a.layer}</td>
                <td>{a.source}</td><td>{a.underground?'כן':'לא'}</td>
              </tr>)}
              {!assets.length && <tr><td colSpan={5}>אין עדיין נכסים. ניתן להוסיף דרך ה־API או לאחר אימות זיהויים.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>}

      {tab==='dashboard' && <section>
        <h2>לוח בקרה</h2>
        <div className="metrics">
          <div className="metric"><span>נכסים</span><strong>{dashboard?.assets ?? 0}</strong></div>
          <div className="metric"><span>מסלולים</span><strong>{dashboard?.routes ?? 0}</strong></div>
          <div className="metric"><span>זיהויים</span><strong>{dashboard?.detections ?? 0}</strong></div>
          <div className="metric"><span>קריאות</span><strong>{dashboard?.tickets ?? 0}</strong></div>
        </div>
        <div className="map-placeholder">
          <MapPin size={38}/>
          <strong>GIS Map</strong>
          <p>בשלב הבא יש לחבר Leaflet או OpenLayers ולצרוך GeoJSON מה־backend.</p>
        </div>
      </section>}
    </main>
  </div>
}
