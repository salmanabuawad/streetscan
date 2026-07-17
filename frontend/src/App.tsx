import { useEffect, useRef, useState } from 'react';
import { Camera, MapPin, Database, Route as RouteIcon, UploadCloud, StopCircle, PlayCircle, ScanSearch, Check, X, Film, Trash2, LogOut, Gauge, Compass, BatteryMedium, Wifi, WifiOff, ImageIcon, Store, GraduationCap } from 'lucide-react';
import { api, getToken, setToken } from './services/api';
import { queueSegment, queueGpsPoint, queueImage, pendingCounts, startAutoFlush } from './services/offlineQueue';
import { AuthImg, AuthVideo } from './AuthMedia';
import Login from './Login';
import MapView from './MapView';
import BBoxPicker, { type Box } from './BBoxPicker';

const SEGMENT_MS = 15000;

// Adaptive capture thresholds (m/s): 15 km/h = 4.17, 5 km/h = 1.39
const FAST_MPS = 4.17;
const SLOW_MPS = 1.39;
const STOP_MPS = 0.5;
const PHOTO_INTERVAL_SLOW_S = 6;   // 5-15 km/h
const PHOTO_INTERVAL_CRAWL_S = 3;  // <5 km/h
const STOP_BURST_COUNT = 5;
const STOP_BURST_COOLDOWN_S = 45;
const BLUR_THRESHOLD = 25;         // Laplacian variance below this = blurry

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
type Detection = {
  id:number; route_id?:number; proposed_asset_type:string; proposed_layer:string;
  confidence:number; latitude?:number; longitude?:number; status:string;
  snapshot_path?:string; created_at:string;
};
type RouteInfo = {
  id:number; vehicle_name:string; driver_name?:string;
  started_at:string; ended_at?:string; active:boolean;
};
type Segment = {
  id:number; route_id:number; mime_type:string; size_bytes:number;
  captured_at:string; processed:boolean; orientation_hint:number;
};
type CapturedImageInfo = {
  id:number; route_id:number; size_bytes:number; captured_at:string;
  latitude?:number; longitude?:number; kind:string; blur_score?:number; processed:boolean;
};
type UserInfo = { id:number; username:string; display_name:string; role:string };

const assetTypeLabels: Record<string,string> = {
  fire_hydrant:'ברז כיבוי', stop_sign:'תמרור עצור', traffic_light:'רמזור',
  bench:'ספסל', parking_meter:'מדחן', telephone_cabinet:'ארון תקשורת',
  electricity_pole:'עמוד חשמל', sewage_manhole:'שוחת ביוב', water_valve:'ברז מים'
};
const statusLabels: Record<string,string> = { draft:'ממתין לאישור', approved:'אושר', rejected:'נדחה' };

const categoryLabels: Record<string,string> = {
  pharmacy:'בית מרקחת', clinic:'מרפאה', dentist:'רופא שיניים', supermarket:'סופרמרקט',
  grocery:'מכולת', agriculture:'חקלאות', greengrocer:'ירקן', restaurant:'מסעדה', cafe:'בית קפה', bakery:'מאפייה', barber:'מספרה',
  beauty:'טיפוח ויופי', bank:'בנק', garage:'מוסך', clothing:'ביגוד', hardware:'חומרי בניין',
  mosque:'מסגד', school:'מוסד חינוך', municipal:'מבנה ציבורי', sports:'ספורט', hotel:'מלון',
  unknown:'לא מסווג'
};

type Business = {
  id:number; route_id?:number; name:string; category:string; ocr_text?:string;
  languages?:string; confidence:number; latitude?:number; longitude?:number;
  status:string; snapshot_path?:string;
};
const CATEGORY_OPTIONS = Object.keys(categoryLabels);

// Asset types offered when labeling training images, grouped by layer.
// value = machine label (used later as a YOLO class), then Hebrew UI label.
const TRAINING_TYPES: { layer:string; label:string; types:[string,string][] }[] = [
  { layer:'electricity', label:'חשמל', types:[
    ['electricity_pole','עמוד חשמל'], ['transformer','שנאי'], ['electrical_cabinet','ארון חשמל'],
    ['street_light','עמוד תאורה'], ['switchgear','לוח מיתוג'] ]},
  { layer:'telecom', label:'תקשורת', types:[
    ['telecom_pole','עמוד תקשורת'], ['telecom_cabinet','ארון תקשורת'],
    ['junction_box','קופסת חיבורים'], ['fiber_marker','סמן סיב אופטי'] ]},
  { layer:'water', label:'מים', types:[
    ['hydrant','ברז כיבוי'], ['water_valve','מגוף מים'], ['water_meter','מד מים'] ]},
  { layer:'sewage', label:'ביוב', types:[
    ['manhole','שוחת ביוב'], ['sewer_cover','מכסה ביוב'] ]},
  { layer:'drainage', label:'ניקוז', types:[ ['storm_drain','קולטן ניקוז'], ['culvert','מעביר מים'] ]},
  { layer:'road', label:'כבישים', types:[ ['sign','תמרור'], ['speed_bump','פס האטה'], ['guard_rail','מעקה בטיחות'] ]},
  { layer:'public_space', label:'מרחב ציבורי', types:[
    ['garbage_container','מכל אשפה'], ['bench','ספסל'], ['bus_station','תחנת אוטובוס'] ]},
];
const TRAINING_TYPE_LABEL: Record<string,string> = Object.fromEntries(
  TRAINING_TYPES.flatMap(g => g.types)
);
function trainingLayerOf(type: string): string {
  return TRAINING_TYPES.find(g => g.types.some(([v]) => v === type))?.layer || 'other';
}
const kindLabels: Record<string,string> = { interval:'נסיעה איטית', stop_burst:'עצירה', manual:'ידני' };
const layerLabels: Record<string,string> = {
  telecom:'תקשורת וטלפוניה', electricity:'חשמל', water:'מים', sewage:'ביוב',
  drainage:'ניקוז', tunnel:'תעלות ומעברים', road:'כבישים', public_space:'מרחב ציבורי'
};

function fmtTime(iso: string) {
  return new Date(iso.endsWith('Z') ? iso : iso + 'Z').toLocaleString('he-IL', {
    day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'
  });
}
function fmtSize(bytes: number) {
  return bytes > 1048576 ? `${(bytes/1048576).toFixed(1)}MB` : `${Math.round(bytes/1024)}KB`;
}

// Laplacian variance on a downscaled grayscale copy — cheap blur estimate.
async function blurScore(source: CanvasImageSource, w: number, h: number): Promise<number> {
  const SW = 160, SH = Math.max(1, Math.round(160 * h / w));
  const canvas = document.createElement('canvas');
  canvas.width = SW; canvas.height = SH;
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
  ctx.drawImage(source, 0, 0, SW, SH);
  const { data } = ctx.getImageData(0, 0, SW, SH);
  const gray = new Float32Array(SW * SH);
  for (let i = 0; i < SW * SH; i++) {
    gray[i] = 0.299 * data[i*4] + 0.587 * data[i*4+1] + 0.114 * data[i*4+2];
  }
  let sum = 0, sumSq = 0, n = 0;
  for (let y = 1; y < SH - 1; y++) {
    for (let x = 1; x < SW - 1; x++) {
      const i = y * SW + x;
      const lap = 4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - SW] - gray[i + SW];
      sum += lap; sumSq += lap * lap; n++;
    }
  }
  const mean = sum / n;
  return sumSq / n - mean * mean;
}

export default function App() {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  const [tab, setTab] = useState<'record'|'videos'|'detections'|'businesses'|'training'|'assets'|'dashboard'>('record');
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [training, setTraining] = useState<{id:number;asset_name:string;asset_type:string;layer:string;latitude?:number;longitude?:number;bbox_cx?:number|null}[]>([]);
  const [trainType, setTrainType] = useState('electricity_pole');
  const [trainName, setTrainName] = useState('');
  const [trainFile, setTrainFile] = useState<File | null>(null);
  const [trainFileUrl, setTrainFileUrl] = useState<string | null>(null);
  const [trainBox, setTrainBox] = useState<Box | null>(null);
  const [trainBusy, setTrainBusy] = useState(false);
  const [routes, setRoutes] = useState<RouteInfo[]>([]);
  const [selectedRoute, setSelectedRoute] = useState<number | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [routeImages, setRouteImages] = useState<CapturedImageInfo[]>([]);
  const [routeId, setRouteId] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState('מוכן');
  const [coords, setCoords] = useState<{lat:number;lng:number;accuracy:number}|null>(null);
  const [pending, setPending] = useState<{segments:number;gps:number;images:number}>({segments:0, gps:0, images:0});
  const [vehicleName, setVehicleName] = useState(localStorage.getItem('vehicle_name') || 'Garbage Truck 1');
  const [driverName, setDriverName] = useState(localStorage.getItem('driver_name') || '');
  const [speedKmh, setSpeedKmh] = useState<number | null>(null);
  const [mode, setMode] = useState('');
  const [headingUi, setHeadingUi] = useState<number | null>(null);
  const [battery, setBattery] = useState<number | null>(null);
  const [online, setOnline] = useState(navigator.onLine);
  const [photoStats, setPhotoStats] = useState({taken:0, rejected:0});

  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const videoElRef = useRef<HTMLVideoElement | null>(null);
  const watchId = useRef<number | null>(null);
  const recordingRef = useRef(false);
  const segmentTimer = useRef<number | null>(null);
  const tickTimer = useRef<number | null>(null);
  const speedRef = useRef<number | null>(null);
  const headingRef = useRef<number | null>(null);
  const posRef = useRef<{lat:number;lng:number;t:number}|null>(null);
  const lastPhotoAt = useRef(0);
  const stoppedMs = useRef(0);
  const burstRemaining = useRef(0);
  const lastBurstAt = useRef(0);
  const captureBusy = useRef(false);

  const canValidate = user != null && user.role !== 'driver';

  // ---- auth ----
  useEffect(() => {
    if (!getToken()) { setAuthChecked(true); return; }
    api<UserInfo>('/auth/me').then(setUser).catch(() => setToken(null)).finally(() => setAuthChecked(true));
  }, []);
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener('auth-expired', onExpired);
    return () => window.removeEventListener('auth-expired', onExpired);
  }, []);

  // Re-attach the camera stream to the preview element after tab switches
  // (leaving the record tab unmounts the <video>; recording keeps running).
  useEffect(() => {
    if (tab === 'record' && recording && videoElRef.current && streamRef.current) {
      videoElRef.current.srcObject = streamRef.current;
      videoElRef.current.play().catch(() => {});
    }
  }, [tab, recording]);

  function logout() {
    if (recordingRef.current) { alert('עצור את המסלול לפני יציאה.'); return; }
    setToken(null);
    setUser(null);
  }

  // ---- data ----
  async function refresh() {
    setDashboard(await api<Dashboard>('/dashboard'));
    setAssets(await api<Asset[]>('/assets'));
    setDetections(await api<Detection[]>('/detections'));
    setBusinesses(await api<Business[]>('/businesses'));
  }

  async function loadTraining() {
    setTraining(await api('/training-samples'));
  }

  function pickTrainFile(f: File | null) {
    if (trainFileUrl) URL.revokeObjectURL(trainFileUrl);
    setTrainFile(f);
    setTrainBox(null);
    setTrainFileUrl(f ? URL.createObjectURL(f) : null);
  }

  async function submitTrainingSample(e: React.FormEvent) {
    e.preventDefault();
    if (!trainFile || !trainBox) return;
    setTrainBusy(true);
    try {
      const fd = new FormData();
      fd.append('asset_type', trainType);
      fd.append('layer', trainingLayerOf(trainType));
      fd.append('asset_name', trainName.trim() || TRAINING_TYPE_LABEL[trainType] || trainType);
      fd.append('bbox_cx', String(trainBox.cx));
      fd.append('bbox_cy', String(trainBox.cy));
      fd.append('bbox_w', String(trainBox.w));
      fd.append('bbox_h', String(trainBox.h));
      if (posRef.current) {
        fd.append('latitude', String(posRef.current.lat));
        fd.append('longitude', String(posRef.current.lng));
      } else {
        // grab a one-shot fix so training samples get located even outside a route
        await new Promise<void>(res => navigator.geolocation.getCurrentPosition(
          p => { fd.append('latitude', String(p.coords.latitude)); fd.append('longitude', String(p.coords.longitude)); res(); },
          () => res(), { enableHighAccuracy:true, timeout:5000 }));
      }
      fd.append('file', trainFile, trainFile.name || 'sample.jpg');
      await api('/training-samples', { method:'POST', body: fd });
      setTrainName('');
      pickTrainFile(null);
      loadTraining();
    } catch (err) {
      alert(`שגיאה בהעלאה: ${String(err)}`);
    } finally {
      setTrainBusy(false);
    }
  }

  async function deleteTrainingSample(id: number) {
    if (!window.confirm('למחוק דוגמת אימון זו?')) return;
    await api(`/training-samples/${id}`, {method:'DELETE'});
    setTraining(t => t.filter(x => x.id !== id));
  }

  async function decideBusiness(id: number, action: 'approve'|'reject') {
    await api(`/businesses/${id}/${action}`, {method:'POST'});
    refresh();
  }

  async function saveBusinessEdit(id: number, patch: {name?:string; category?:string}) {
    await api(`/businesses/${id}`, {
      method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch),
    });
    refresh();
  }

  async function refreshPending() {
    try { setPending(await pendingCounts()); } catch { /* IndexedDB unavailable */ }
  }

  useEffect(() => {
    if (!user) return;
    refresh().catch(console.error);
    refreshPending();
    const stopFlush = startAutoFlush(() => refreshPending());
    const onNet = () => setOnline(navigator.onLine);
    window.addEventListener('online', onNet);
    window.addEventListener('offline', onNet);
    (navigator as any).getBattery?.().then((b: any) => {
      const set = () => setBattery(Math.round(b.level * 100));
      set(); b.addEventListener('levelchange', set);
    }).catch(() => {});
    return () => { stopFlush(); window.removeEventListener('online', onNet); window.removeEventListener('offline', onNet); };
  }, [user]);

  async function decideDetection(id: number, action: 'approve'|'reject') {
    await api(`/detections/${id}/${action}`, {method:'POST'});
    refresh();
  }

  async function openVideos() {
    setTab('videos');
    setRoutes(await api<RouteInfo[]>('/routes'));
  }

  async function selectRoute(id: number) {
    setSelectedRoute(id);
    setSegments(await api<Segment[]>(`/routes/${id}/segments`));
    setRouteImages(await api<CapturedImageInfo[]>(`/routes/${id}/images`));
  }

  async function deleteSegment(id: number) {
    if (!window.confirm('למחוק את מקטע הווידאו הזה? הקובץ יימחק מהשרת לצמיתות.')) return;
    await api(`/video-segments/${id}`, {method:'DELETE'});
    setSegments(s => s.filter(x => x.id !== id));
    refresh();
  }

  async function deleteImage(id: number) {
    if (!window.confirm('למחוק את התמונה? הקובץ יימחק מהשרת לצמיתות.')) return;
    await api(`/images/${id}`, {method:'DELETE'});
    setRouteImages(s => s.filter(x => x.id !== id));
  }

  async function deleteRoute(id: number) {
    if (!window.confirm(`למחוק את מסלול ${id} על כל הווידאו, התמונות ונקודות ה־GPS שלו? פעולה בלתי הפיכה.`)) return;
    try {
      await api(`/routes/${id}`, {method:'DELETE'});
      setSelectedRoute(null);
      setSegments([]);
      setRouteImages([]);
      setRoutes(await api<RouteInfo[]>('/routes'));
      refresh();
    } catch (e) {
      alert(String(e).includes('Stop the route') ? 'המסלול עדיין פעיל — עצור אותו קודם.' : `שגיאה: ${String(e)}`);
    }
  }

  // ---- video segments ----
  async function uploadOrQueueSegment(rid: number, blob: Blob, mimeType: string, orientation: number) {
    const capturedAt = new Date().toISOString();
    const ext = mimeType.includes('mp4') ? 'mp4' : 'webm';
    const filename = `segment-${Date.now()}.${ext}`;
    const fd = new FormData();
    fd.append('route_id', String(rid));
    fd.append('captured_at', capturedAt);
    fd.append('orientation', String(orientation));
    fd.append('file', blob, filename);
    try {
      await api(`/video-segments`, { method:'POST', body:fd });
      if (recordingRef.current) setStatus('מקליט ומעלה לשרת');
    } catch {
      await queueSegment(rid, capturedAt, blob, filename, orientation).catch(console.error);
      refreshPending();
      if (recordingRef.current) setStatus('אין חיבור — המקטע נשמר בדפדפן ויעלה אוטומטית');
    }
  }

  // Record one self-contained segment, then start the next one.
  // (A single recorder with a timeslice produces continuation chunks that are
  // not playable on their own, so each segment gets its own recorder.)
  function recordNextSegment(stream: MediaStream, rid: number, mimeType: string) {
    if (!recordingRef.current || !stream.active) return;
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    mediaRecorder.current = recorder;
    const orientation = (screen.orientation && screen.orientation.angle) || 0;
    const chunks: Blob[] = [];
    recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      if (chunks.length) {
        uploadOrQueueSegment(rid, new Blob(chunks, { type: mimeType || 'video/webm' }), mimeType, orientation);
      }
      recordNextSegment(stream, rid, mimeType);
    };
    recorder.start();
    segmentTimer.current = window.setTimeout(() => {
      if (recorder.state !== 'inactive') recorder.stop();
    }, SEGMENT_MS);
  }

  // ---- adaptive photo capture ----
  async function capturePhoto(rid: number, kind: string) {
    if (captureBusy.current) return;
    const video = videoElRef.current;
    if (!video || video.videoWidth === 0) return;
    captureBusy.current = true;
    try {
      const w = video.videoWidth, h = video.videoHeight;
      const score = await blurScore(video, w, h);
      if (score < BLUR_THRESHOLD) {
        setPhotoStats(s => ({...s, rejected: s.rejected + 1}));
        return;
      }
      // Camera buffer keeps a fixed landscape orientation; upright the still
      // to match how the phone is held so AI runs on a correctly-rotated image.
      const angle = (screen.orientation && screen.orientation.angle) || 0;
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d')!;
      if (angle === 90 || angle === 270) { canvas.width = h; canvas.height = w; }
      else { canvas.width = w; canvas.height = h; }
      ctx.save();
      if (angle === 90) { ctx.translate(0, w); ctx.rotate(-Math.PI/2); }        // CCW
      else if (angle === 270) { ctx.translate(h, 0); ctx.rotate(Math.PI/2); }   // CW
      else if (angle === 180) { ctx.translate(w, h); ctx.rotate(Math.PI); }
      ctx.drawImage(video, 0, 0);
      ctx.restore();
      const blob = await new Promise<Blob | null>(r => canvas.toBlob(r, 'image/jpeg', 0.88));
      if (!blob) return;
      const fields: Record<string, string> = {
        route_id: String(rid),
        captured_at: new Date().toISOString(),
        kind,
        blur_score: score.toFixed(1),
      };
      if (posRef.current) {
        fields.latitude = String(posRef.current.lat);
        fields.longitude = String(posRef.current.lng);
      }
      if (headingRef.current != null) fields.heading_deg = headingRef.current.toFixed(1);
      if (speedRef.current != null) fields.speed_mps = speedRef.current.toFixed(2);
      const filename = `photo-${Date.now()}.jpg`;
      try {
        const fd = new FormData();
        for (const [k, v] of Object.entries(fields)) fd.append(k, v);
        fd.append('file', blob, filename);
        await api('/images', { method:'POST', body: fd });
      } catch {
        await queueImage(blob, filename, fields).catch(console.error);
        refreshPending();
      }
      setPhotoStats(s => ({...s, taken: s.taken + 1}));
    } finally {
      captureBusy.current = false;
    }
  }

  // One tick per second: derive capture mode from speed and schedule photos.
  function captureTick(rid: number) {
    const speed = speedRef.current;
    setSpeedKmh(speed != null ? Math.round(speed * 3.6) : null);
    setHeadingUi(headingRef.current != null ? Math.round(headingRef.current) : null);
    const now = Date.now();

    // stop detection -> burst
    if (speed != null && speed < STOP_MPS) {
      stoppedMs.current += 1000;
      if (stoppedMs.current >= 3000 && burstRemaining.current === 0 &&
          now - lastBurstAt.current > STOP_BURST_COOLDOWN_S * 1000) {
        burstRemaining.current = STOP_BURST_COUNT;
        lastBurstAt.current = now;
      }
    } else {
      stoppedMs.current = 0;
    }

    if (burstRemaining.current > 0) {
      setMode('עצירה — צילום מוגבר');
      burstRemaining.current--;
      capturePhoto(rid, 'stop_burst');
      return;
    }

    if (speed == null) { setMode('וידאו (ממתין למהירות GPS)'); return; }
    if (speed > FAST_MPS) { setMode('נסיעה — וידאו'); return; }

    const interval = speed > SLOW_MPS ? PHOTO_INTERVAL_SLOW_S : PHOTO_INTERVAL_CRAWL_S;
    setMode(speed > SLOW_MPS ? 'איטי — וידאו + תמונות' : 'זחילה — תמונות בתדירות גבוהה');
    if (now - lastPhotoAt.current >= interval * 1000) {
      lastPhotoAt.current = now;
      capturePhoto(rid, 'interval');
    }
  }

  function onOrientation(e: DeviceOrientationEvent) {
    const webkit = (e as any).webkitCompassHeading;
    if (typeof webkit === 'number') headingRef.current = webkit;           // iOS: degrees from north, clockwise
    else if (e.absolute && e.alpha != null) headingRef.current = (360 - e.alpha) % 360;  // Android
  }

  // iOS requires DeviceOrientationEvent.requestPermission() to run inside the
  // user gesture, BEFORE any await (camera/network) consumes the activation.
  function enableCompass() {
    const doe = DeviceOrientationEvent as any;
    if (typeof doe?.requestPermission === 'function') {
      doe.requestPermission()
        .then((r: string) => { if (r === 'granted') window.addEventListener('deviceorientation', onOrientation); })
        .catch(() => {});
    } else {
      window.addEventListener('deviceorientationabsolute', onOrientation as any);
      window.addEventListener('deviceorientation', onOrientation);
    }
  }

  // ---- route lifecycle ----
  async function startRoute() {
    enableCompass();  // must be first — preserves the tap's transient activation
    try {
      setStatus('פותח מסלול...');
      localStorage.setItem('vehicle_name', vehicleName);
      localStorage.setItem('driver_name', driverName);
      const route = await api<{id:number}>('/routes', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({vehicle_name: vehicleName || 'Vehicle', driver_name: driverName || user?.display_name})
      });
      setRouteId(route.id);

      const stream = await navigator.mediaDevices.getUserMedia({
        video:{ facingMode:{ideal:'environment'}, width:{ideal:1920}, height:{ideal:1080} },
        audio:false
      });
      streamRef.current = stream;
      if (videoElRef.current) {
        videoElRef.current.srcObject = stream;
        videoElRef.current.play().catch(() => {});
      }
      recordingRef.current = true;
      recordNextSegment(stream, route.id, pickMimeType());

      watchId.current = navigator.geolocation.watchPosition(
        async p => {
          const c = {lat:p.coords.latitude, lng:p.coords.longitude, accuracy:p.coords.accuracy};
          setCoords(c);
          const t = Date.now();
          // speed: prefer the GPS chip's value, fall back to distance/time
          let sp = p.coords.speed;
          if (sp == null && posRef.current && t > posRef.current.t) {
            const dLat = (c.lat - posRef.current.lat) * 111320;
            const dLng = (c.lng - posRef.current.lng) * 111320 * Math.cos(c.lat * Math.PI / 180);
            sp = Math.hypot(dLat, dLng) / ((t - posRef.current.t) / 1000);
          }
          if (sp != null) speedRef.current = speedRef.current == null ? sp : 0.6 * speedRef.current + 0.4 * sp;
          posRef.current = {lat:c.lat, lng:c.lng, t};
          const body = {
            route_id:route.id, latitude:c.lat, longitude:c.lng,
            accuracy_m:c.accuracy, speed_mps:p.coords.speed,
            heading_deg: p.coords.heading ?? headingRef.current,
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
        err => {
          if (err.code === 1) setStatus('הרשאת מיקום נדחתה — אפשר מיקום ל־Safari בהגדרות iOS ובאתר (aA ← הגדרות אתר ← מיקום), ורענן');
          else if (err.code === 2) setStatus('אין אות GPS — ממשיך לנסות');
          else setStatus('ממתין לאות GPS...');
        },
        { enableHighAccuracy:true, maximumAge:3000, timeout:10000 }
      );

      setPhotoStats({taken:0, rejected:0});
      tickTimer.current = window.setInterval(() => captureTick(route.id), 1000);
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
    if (tickTimer.current !== null) window.clearInterval(tickTimer.current);
    window.removeEventListener('deviceorientation', onOrientation);
    window.removeEventListener('deviceorientationabsolute', onOrientation as any);
    if (mediaRecorder.current?.state !== 'inactive') mediaRecorder.current?.stop(); // last segment still uploads via onstop
    streamRef.current?.getTracks().forEach(t => t.stop());
    if (videoElRef.current) videoElRef.current.srcObject = null;
    if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current);
    if (routeId) await api(`/routes/${routeId}/stop`, {method:'POST'}).catch(console.error);
    setRecording(false);
    setRouteId(null);
    speedRef.current = null;
    setSpeedKmh(null);
    setMode('');
    setStatus('המסלול הסתיים');
    refresh();
  }

  if (!authChecked) return null;
  if (!user) return <Login onLogin={u => setUser({id:0, username:u.username, display_name:u.display_name, role:u.role})}/>;

  return <div className="app-shell">
    <header className="topbar">
      <div>
        <h1>Buqata StreetScan</h1>
        <p>מיפוי תשתיות ומפגעים באמצעות רכב מועצה</p>
      </div>
      <div className="topbar-side">
        <span className={`status ${recording ? 'live':''}`}>{status}</span>
        <button className="logout" onClick={logout} title={`${user.display_name} (${user.role})`}>
          <LogOut size={15}/> {user.display_name}
        </button>
      </div>
    </header>

    <nav className="tabs">
      <button onClick={()=>setTab('record')} className={tab==='record'?'active':''}><Camera size={18}/> הקלטה</button>
      <button onClick={openVideos} className={tab==='videos'?'active':''}><Film size={18}/> וידאו</button>
      <button onClick={()=>setTab('detections')} className={tab==='detections'?'active':''}>
        <ScanSearch size={18}/> זיהויים
        {detections.filter(d=>d.status==='draft').length > 0 && ` (${detections.filter(d=>d.status==='draft').length})`}
      </button>
      <button onClick={()=>setTab('businesses')} className={tab==='businesses'?'active':''}>
        <Store size={18}/> עסקים
        {businesses.filter(b=>b.status==='draft').length > 0 && ` (${businesses.filter(b=>b.status==='draft').length})`}
      </button>
      <button onClick={()=>{setTab('training'); loadTraining().catch(console.error);}} className={tab==='training'?'active':''}>
        <GraduationCap size={18}/> תיוג לאימון
      </button>
      <button onClick={()=>setTab('assets')} className={tab==='assets'?'active':''}><Database size={18}/> נכסים</button>
      <button onClick={()=>setTab('dashboard')} className={tab==='dashboard'?'active':''}><MapPin size={18}/> לוח בקרה</button>
    </nav>

    <main>
      {tab==='record' && <section className="panel hero">
        {!recording && <>
          <div className="record-icon"><RouteIcon size={44}/></div>
          <h2>מסלול צילום ומיפוי</h2>
          <p>צילום אדפטיבי: וידאו בנסיעה, תמונות באיטיות, צילום מוגבר בעצירות.</p>
          <div className="vehicle-form">
            <input placeholder="שם רכב" value={vehicleName} onChange={e=>setVehicleName(e.target.value)}/>
            <input placeholder="שם נהג" value={driverName} onChange={e=>setDriverName(e.target.value)}/>
          </div>
        </>}

        {/* dashcam view: live camera with HUD overlay (element stays mounted
            even when idle so the ref exists when the route starts) */}
        <div className="dashcam" style={{display: recording ? 'block' : 'none'}}>
          <div className="dashcam-frame">
            <video ref={videoElRef} muted playsInline autoPlay/>
            <div className="dashcam-hud">
              <span className="hud-chip live-dot">● REC {routeId ? `מסלול ${routeId}` : ''}</span>
              <span className="hud-chip"><Gauge size={13}/> {speedKmh != null ? `${speedKmh} קמ"ש` : '—'}</span>
              {headingUi != null && <span className="hud-chip"><Compass size={13}/> {headingUi}°</span>}
              <span className="hud-chip"><ImageIcon size={13}/> {photoStats.taken}{photoStats.rejected ? ` (${photoStats.rejected}✕)` : ''}</span>
              {battery != null && <span className="hud-chip"><BatteryMedium size={13}/> {battery}%</span>}
              <span className="hud-chip">{online ? <Wifi size={13}/> : <WifiOff size={13}/>}</span>
            </div>
            {mode && <div className="dashcam-mode">{mode}</div>}
          </div>
        </div>

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
            וידאו במקטעי 15 שניות + תמונות ברזולוציה גבוהה לפי מהירות. בניתוק הכל נשמר ב־IndexedDB ועולה אוטומטית.
            {(pending.segments > 0 || pending.gps > 0 || pending.images > 0) &&
              ` ממתינים להעלאה: ${pending.segments} מקטעים, ${pending.images} תמונות, ${pending.gps} נקודות GPS.`}
          </span>
        </div>
      </section>}

      {tab==='videos' && <section>
        <div className="section-head"><div>
          <h2>וידאו ותמונות מסלולים</h2>
          <p>צפייה בחומרים שהועלו מהשטח, לפי מסלול.</p>
        </div></div>
        {!routes.length && <div className="empty-note">אין עדיין מסלולים מוקלטים.</div>}
        <div className="route-list">
          {routes.map(r => <button key={r.id}
            className={`route-item ${selectedRoute===r.id?'active':''}`}
            onClick={()=>selectRoute(r.id)}>
            <strong>מסלול {r.id}</strong>
            <span>{r.vehicle_name}{r.driver_name ? ` · ${r.driver_name}` : ''}</span>
            <span>{fmtTime(r.started_at)}</span>
            {r.active && <span className="chip draft">פעיל</span>}
          </button>)}
        </div>
        {selectedRoute !== null && canValidate && <div className="route-actions">
          <button className="delete-route" onClick={()=>deleteRoute(selectedRoute)}>
            <Trash2 size={15}/> מחק מסלול {selectedRoute} ({segments.length} מקטעים, {routeImages.length} תמונות)
          </button>
        </div>}
        {selectedRoute !== null && (segments.length
          ? <div className="video-grid">
              {segments.map(s => <div className="video-card" key={s.id}>
                <AuthVideo path={`/video-segments/${s.id}/stream`} controls preload="metadata" playsInline/>
                <div className="video-meta">
                  <span>{fmtTime(s.captured_at)}</span>
                  <span>{fmtSize(s.size_bytes)}</span>
                  <span className={`chip ${s.processed?'approved':'draft'}`}>{s.processed?'עובד ב־AI':'בתור לעיבוד'}</span>
                  {canValidate && <button className="icon-danger" title="מחק מקטע" onClick={()=>deleteSegment(s.id)}><Trash2 size={16}/></button>}
                </div>
              </div>)}
            </div>
          : <div className="empty-note">אין מקטעי וידאו במסלול הזה.</div>)}
        {selectedRoute !== null && routeImages.length > 0 && <>
          <h3 className="images-head">תמונות ({routeImages.length})</h3>
          <div className="video-grid">
            {routeImages.map(im => <div className="video-card" key={im.id}>
              <AuthImg path={`/images/${im.id}/file`} alt={`image ${im.id}`} loading="lazy"/>
              <div className="video-meta">
                <span>{fmtTime(im.captured_at)}</span>
                <span>{kindLabels[im.kind] || im.kind}</span>
                <span className={`chip ${im.processed?'approved':'draft'}`}>{im.processed?'עובד ב־AI':'בתור'}</span>
                {canValidate && <button className="icon-danger" title="מחק תמונה" onClick={()=>deleteImage(im.id)}><Trash2 size={16}/></button>}
              </div>
            </div>)}
          </div>
        </>}
      </section>}

      {tab==='detections' && <section>
        <div className="section-head"><div>
          <h2>זיהויי AI</h2>
          <p>זיהויים אוטומטיים מווידאו ותמונות. אישור הופך זיהוי לנכס במאגר; דחייה מסירה אותו.</p>
        </div></div>
        {!detections.length && <div className="empty-note">
          אין עדיין זיהויי תשתית. זיהוי עמודי חשמל/תקשורת וארונות דורש מודל YOLO ייעודי מאומן — הפיילוט אוסף כעת את התמונות לאימונו. זיהוי עסקים מופיע בטאב "עסקים".
        </div>}
        <div className="detection-grid">
          {detections.map(d => <div className="detection-card" key={d.id}>
            {d.snapshot_path && <AuthImg path={`/detections/${d.id}/snapshot`} alt={d.proposed_asset_type} loading="lazy"/>}
            <div className="detection-body">
              <div className="detection-title">
                <strong>{assetTypeLabels[d.proposed_asset_type] || d.proposed_asset_type}</strong>
                <span className={`chip ${d.status}`}>{statusLabels[d.status] || d.status}</span>
              </div>
              <div className="detection-meta">
                <span>{layerLabels[d.proposed_layer] || d.proposed_layer}</span>
                <span>ביטחון: {Math.round(d.confidence*100)}%</span>
                {d.latitude != null && d.longitude != null
                  ? <span>{d.latitude.toFixed(5)}, {d.longitude.toFixed(5)}</span>
                  : <span>ללא מיקום</span>}
              </div>
              {d.status==='draft' && canValidate && <div className="detection-actions">
                <button className="approve" onClick={()=>decideDetection(d.id,'approve')}><Check size={16}/> אישור</button>
                <button className="reject" onClick={()=>decideDetection(d.id,'reject')}><X size={16}/> דחייה</button>
              </div>}
            </div>
          </div>)}
        </div>
      </section>}

      {tab==='businesses' && <section>
        <div className="section-head"><div>
          <h2>עסקים ומוסדות</h2>
          <p>זיהוי שלטי עסקים ב־OCR (ערבית/עברית/אנגלית). ניתן לתקן שם וקטגוריה לפני אישור.</p>
        </div></div>
        {!businesses.length && <div className="empty-note">
          אין עדיין עסקים. ה־OCR קורא שלטים מתמונות ברזולוציה גבוהה שנלכדות בעצירות — צלם סיבוב ברחוב עם חזיתות חנויות.
        </div>}
        <div className="detection-grid">
          {businesses.map(b => <div className="detection-card" key={b.id}>
            {b.snapshot_path && <AuthImg path={`/businesses/${b.id}/snapshot`} alt={b.name} loading="lazy"/>}
            <div className="detection-body">
              {b.status==='draft' && canValidate
                ? <input className="biz-name-edit" defaultValue={b.name}
                    onBlur={e => e.target.value !== b.name && saveBusinessEdit(b.id, {name: e.target.value})}/>
                : <strong>{b.name}</strong>}
              <div className="detection-title">
                {b.status==='draft' && canValidate
                  ? <select value={b.category} onChange={e => saveBusinessEdit(b.id, {category: e.target.value})}>
                      {CATEGORY_OPTIONS.map(c => <option key={c} value={c}>{categoryLabels[c]}</option>)}
                    </select>
                  : <span className="chip">{categoryLabels[b.category] || b.category}</span>}
                <span className={`chip ${b.status}`}>{statusLabels[b.status] || b.status}</span>
              </div>
              <div className="detection-meta">
                <span>ביטחון: {Math.round(b.confidence*100)}%</span>
                {b.languages && <span>{b.languages}</span>}
                {b.latitude != null && b.longitude != null
                  ? <span>{b.latitude.toFixed(5)}, {b.longitude.toFixed(5)}</span>
                  : <span>ללא מיקום</span>}
              </div>
              {b.status==='draft' && canValidate && <div className="detection-actions">
                <button className="approve" onClick={()=>decideBusiness(b.id,'approve')}><Check size={16}/> אישור</button>
                <button className="reject" onClick={()=>decideBusiness(b.id,'reject')}><X size={16}/> דחייה</button>
              </div>}
            </div>
          </div>)}
        </div>
      </section>}

      {tab==='training' && <section>
        <div className="section-head"><div>
          <h2>תיוג נכסים לאימון AI</h2>
          <p>צלם או העלה תמונה של נכס (עמוד חשמל, ארון תקשורת, שוחה...), בחר סוג ותן שם. הדוגמאות ישמשו לאימון מודל זיהוי ייעודי.</p>
        </div></div>

        <form className="panel train-form" onSubmit={submitTrainingSample}>
          {!trainFile || !trainFileUrl
            ? <label className="train-photo">
                <div className="train-photo-empty"><Camera size={30}/><span>צלם / בחר תמונה</span></div>
                <input type="file" accept="image/*" capture="environment" hidden
                  onChange={e => pickTrainFile(e.target.files?.[0] || null)}/>
              </label>
            : <>
                <BBoxPicker src={trainFileUrl} onChange={setTrainBox}/>
                <button type="button" className="link-btn" onClick={()=>pickTrainFile(null)}>החלף תמונה</button>
              </>}

          <select value={trainType} onChange={e=>setTrainType(e.target.value)}>
            {TRAINING_TYPES.map(g => <optgroup key={g.layer} label={g.label}>
              {g.types.map(([v,l]) => <option key={v} value={v}>{l}</option>)}
            </optgroup>)}
          </select>
          <input placeholder="שם/תיאור (לא חובה)" value={trainName} onChange={e=>setTrainName(e.target.value)}/>
          {trainFile && !trainBox && <span className="train-warn">סמן תיבה סביב הנכס כדי להמשיך</span>}
          <button className="primary big" type="submit" disabled={!trainFile || !trainBox || trainBusy}>
            <UploadCloud size={18}/> {trainBusy ? 'מעלה...' : 'הוסף דוגמה'}
          </button>
        </form>

        {training.length > 0 && (() => {
          const counts: Record<string,number> = {};
          training.forEach(t => { counts[t.asset_type] = (counts[t.asset_type]||0)+1; });
          return <div className="train-counts">
            {Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([t,c]) =>
              <span key={t} className="chip">{TRAINING_TYPE_LABEL[t]||t}: {c}</span>)}
          </div>;
        })()}

        {!training.length
          ? <div className="empty-note">עוד לא נאספו דוגמאות. ככל שתאסוף יותר לכל סוג (מומלץ 50+), האימון יהיה מדויק יותר.</div>
          : <div className="detection-grid">
              {training.map(t => <div className="detection-card" key={t.id}>
                <AuthImg path={`/training-samples/${t.id}/file`} alt={t.asset_type} loading="lazy"/>
                <div className="detection-body">
                  <div className="detection-title">
                    <strong>{TRAINING_TYPE_LABEL[t.asset_type] || t.asset_type}</strong>
                    <button className="icon-danger" title="מחק" onClick={()=>deleteTrainingSample(t.id)}><Trash2 size={15}/></button>
                  </div>
                  <div className="detection-meta">
                    <span>{layerLabels[t.layer] || t.layer}</span>
                    {t.bbox_cx != null
                      ? <span className="chip approved">תיבה ✓</span>
                      : <span className="chip draft">ללא תיבה</span>}
                    {t.latitude != null ? <span>📍</span> : <span>ללא מיקום</span>}
                  </div>
                </div>
              </div>)}
            </div>}
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
        <MapView layerLabels={layerLabels} assetTypeLabels={assetTypeLabels}/>
      </section>}
    </main>
  </div>
}
