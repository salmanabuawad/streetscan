import { useEffect, useRef, useState } from 'react';
import { Camera, MapPin, Database, Route as RouteIcon, UploadCloud, StopCircle, PlayCircle, ScanSearch, Check, X, Film, Trash2, LogOut, Gauge, Compass, BatteryMedium, Wifi, WifiOff, ImageIcon, Store, GraduationCap, Boxes, Tags, StepBack, StepForward } from 'lucide-react';
import { api, getToken, setToken, fetchMediaUrl } from './services/api';
import { queueSegment, queueGpsPoint, queueImage, pendingCounts, startAutoFlush } from './services/offlineQueue';
import { AuthImg, AuthVideo } from './AuthMedia';
import Login from './Login';
import MapView from './MapView';
import CommandCenter from './CommandCenter';
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
  mosque:'מסגד', holy_site:'אתר קדוש/מורשת', school:'מוסד חינוך', municipal:'מבנה ציבורי', sports:'ספורט', hotel:'מלון',
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
  { layer:'safety', label:'מיגון וביטחון', types:[
    ['migunit','מיגונית'], ['public_shelter','מקלט ציבורי'] ]},
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
  drainage:'ניקוז', tunnel:'תעלות ומעברים', road:'כבישים', public_space:'מרחב ציבורי',
  safety:'מיגון וביטחון'
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

  const [tab, setTab] = useState<'record'|'videos'|'detections'|'businesses'|'candidates'|'training'|'assets'|'dashboard'>('record');
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [training, setTraining] = useState<{id:number;asset_name:string;asset_type:string;layer:string;latitude?:number;longitude?:number;bbox_cx?:number|null;bbox_cy?:number|null;bbox_w?:number|null;bbox_h?:number|null}[]>([]);
  const [trainType, setTrainType] = useState('electricity_pole');
  const [trainName, setTrainName] = useState('');
  const [trainFile, setTrainFile] = useState<File | null>(null);
  const [trainFileUrl, setTrainFileUrl] = useState<string | null>(null);
  const [trainBox, setTrainBox] = useState<Box | null>(null);
  const [trainBusy, setTrainBusy] = useState(false);
  const [editSample, setEditSample] = useState<any|null>(null);
  const [editType, setEditType] = useState('');
  const [editName, setEditName] = useState('');
  const [editBox, setEditBox] = useState<Box | null>(null);
  const [editImgUrl, setEditImgUrl] = useState<string | null>(null);
  const [captures, setCaptures] = useState<{id:number;latitude?:number;longitude?:number}[]>([]);
  const [showCaptures, setShowCaptures] = useState(false);
  const [annImg, setAnnImg] = useState<number | null>(null);
  const [annImgUrl, setAnnImgUrl] = useState<string | null>(null);
  const [annBox, setAnnBox] = useState<Box | null>(null);
  const [annType, setAnnType] = useState('electricity_pole');
  const [annCustom, setAnnCustom] = useState(false);
  const [annCustomType, setAnnCustomType] = useState('');
  const [annCustomName, setAnnCustomName] = useState('');
  const [annCustomLayer, setAnnCustomLayer] = useState('other');
  const [trainCustom, setTrainCustom] = useState(false);
  const [trainCustomType, setTrainCustomType] = useState('');
  const [trainCustomLayer, setTrainCustomLayer] = useState('other');
  const [videoAnnSegment, setVideoAnnSegment] = useState<Segment | null>(null);
  const [videoAnnUrl, setVideoAnnUrl] = useState<string | null>(null);
  const [videoFrameUrl, setVideoFrameUrl] = useState<string | null>(null);
  const [videoFrameTime, setVideoFrameTime] = useState(0);
  const [videoAnnBox, setVideoAnnBox] = useState<Box | null>(null);
  const [videoAnnType, setVideoAnnType] = useState('electricity_pole');
  const [videoAnnName, setVideoAnnName] = useState('');
  const [videoAnnLayer, setVideoAnnLayer] = useState('electricity');
  const [videoAnnCustom, setVideoAnnCustom] = useState(false);
  const [videoAnnBusy, setVideoAnnBusy] = useState(false);
  const videoAnnotatorRef = useRef<HTMLVideoElement | null>(null);
  const [candidates, setCandidates] = useState<any[]>([]);
  const [candSummary, setCandSummary] = useState<any>(null);
  const [detRoutes, setDetRoutes] = useState<RouteInfo[]>([]);
  const [detRoute, setDetRoute] = useState<number | null>(null);
  const [detCands, setDetCands] = useState<any[]>([]);
  const [analysisJob, setAnalysisJob] = useState<any>(null);
  const [allCats, setAllCats] = useState<string[]>([]);
  const [candCat, setCandCat] = useState<string>('');
  const [candBand, setCandBand] = useState<string>('');
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
    setBusinesses(await api<Business[]>('/businesses'));
  }

  async function loadTraining() {
    setTraining(await api('/training-samples'));
  }

  async function openDetections() {
    setTab('detections');
    setDetRoutes(await api<RouteInfo[]>('/routes'));
    try { setAllCats((await api<any[]>('/asset-categories')).map(c => c.name)); } catch { /* */ }
  }

  async function selectDetRoute(id: number) {
    setDetRoute(id);
    setAnalysisJob(null);
    setDetCands(await api(`/assets/candidates?route_id=${id}&limit=120`));
  }

  async function runAnalysis(routeId: number) {
    const job = await api<any>(`/analysis/routes/${routeId}`, {method:'POST'});
    setAnalysisJob({...job, processed:0, remaining:job.queued_images, status:'running', candidates:0});
    const poll = setInterval(async () => {
      try {
        const p = await api<any>(`/analysis/jobs/${job.job_id}/progress`);
        setAnalysisJob(p);
        setDetCands(await api(`/assets/candidates?route_id=${routeId}&limit=120`));
        if (p.status === 'done') clearInterval(poll);
      } catch { clearInterval(poll); }
    }, 6000);
  }

  async function decideCand(id: number, action: 'approve'|'reject') {
    await api(`/assets/candidates/${id}/${action}`, {method:'POST'});
    setDetCands(cs => cs.filter(c => c.id !== id));
  }

  async function correctCand(id: number, category: string) {
    const fd = new FormData(); fd.append('category', category);
    await api(`/assets/candidates/${id}/correct`, {method:'POST', body: fd});
    setDetCands(cs => cs.filter(c => c.id !== id));
  }

  async function loadCandidates() {
    setCandSummary(await api('/assets/candidates/summary'));
    const q = new URLSearchParams();
    if (candCat) q.set('category', candCat);
    if (candBand) q.set('band', candBand);
    q.set('limit', '60');
    setCandidates(await api(`/assets/candidates?${q}`));
  }

  async function decideCandidate(id: number, action: 'approve'|'reject') {
    await api(`/assets/candidates/${id}/${action}`, {method:'POST'});
    setCandidates(cs => cs.filter(c => c.id !== id));
  }

  async function correctCandidate(id: number, category: string) {
    const fd = new FormData(); fd.append('category', category);
    await api(`/assets/candidates/${id}/correct`, {method:'POST', body: fd});
    setCandidates(cs => cs.filter(c => c.id !== id));
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
      const resolvedType = trainCustom ? trainCustomType.trim() : trainType;
      if (!resolvedType) { alert('יש להזין סוג נכס חדש'); return; }
      fd.append('asset_type', resolvedType);
      fd.append('layer', trainCustom ? trainCustomLayer : trainingLayerOf(trainType));
      fd.append('asset_name', trainName.trim() || TRAINING_TYPE_LABEL[resolvedType] || resolvedType);
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

  useEffect(() => {
    if (!editSample) { setEditImgUrl(null); return; }
    let url: string | null = null, alive = true;
    fetchMediaUrl(`/training-samples/${editSample.id}/file`).then(u => {
      if (alive) { url = u; setEditImgUrl(u); } else URL.revokeObjectURL(u);
    }).catch(() => {});
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [editSample]);

  useEffect(() => {
    if (annImg == null) { setAnnImgUrl(null); return; }
    let url: string | null = null, alive = true;
    fetchMediaUrl(`/images/${annImg}/file`).then(u => {
      if (alive) { url = u; setAnnImgUrl(u); } else URL.revokeObjectURL(u);
    }).catch(() => {});
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [annImg]);

  async function loadCaptures() {
    setShowCaptures(true);
    setCaptures(await api('/captured-images'));
  }

  async function saveAnnotation() {
    if (annImg == null || !annBox) return;
    const resolvedType = annCustom ? annCustomType.trim() : annType;
    if (!resolvedType) { alert('יש להזין סוג נכס חדש'); return; }
    await api(`/captured-images/${annImg}/annotate`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        asset_type:resolvedType,
        asset_name:annCustomName.trim() || TRAINING_TYPE_LABEL[resolvedType] || resolvedType,
        layer: annCustom ? annCustomLayer : trainingLayerOf(resolvedType),
        bbox_cx:annBox.cx, bbox_cy:annBox.cy, bbox_w:annBox.w, bbox_h:annBox.h
      }),
    });
    setAnnImg(null); setAnnBox(null); setAnnCustomName(''); setAnnCustomType('');
    loadTraining();
  }

  async function openVideoAnnotator(segment: Segment) {
    setVideoAnnSegment(segment);
    setVideoFrameUrl(null); setVideoAnnBox(null); setVideoAnnName('');
    try { setVideoAnnUrl(await fetchMediaUrl(`/video-segments/${segment.id}/stream`)); }
    catch { setVideoAnnUrl(null); }
  }

  function closeVideoAnnotator() {
    if (videoAnnUrl) URL.revokeObjectURL(videoAnnUrl);
    setVideoAnnSegment(null); setVideoAnnUrl(null); setVideoFrameUrl(null); setVideoAnnBox(null);
  }

  function captureVideoFrame() {
    const video = videoAnnotatorRef.current;
    if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
    video.pause();
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext('2d')!.drawImage(video, 0, 0, canvas.width, canvas.height);
    setVideoFrameTime(video.currentTime);
    setVideoFrameUrl(canvas.toDataURL('image/jpeg', .95));
    setVideoAnnBox(null);
  }

  function stepVideo(seconds: number) {
    const video = videoAnnotatorRef.current;
    if (!video) return;
    video.pause();
    video.currentTime = Math.max(0, Math.min(video.duration || Infinity, video.currentTime + seconds));
  }

  async function saveVideoAnnotation() {
    if (!videoAnnSegment || !videoAnnBox) return;
    const resolvedType = videoAnnCustom ? videoAnnType.trim() : videoAnnType;
    if (!resolvedType) { alert('יש להזין סוג נכס'); return; }
    setVideoAnnBusy(true);
    try {
      await api(`/video-segments/${videoAnnSegment.id}/annotate`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          timestamp_s: videoFrameTime,
          asset_type: resolvedType,
          asset_name: videoAnnName.trim() || TRAINING_TYPE_LABEL[resolvedType] || resolvedType,
          layer: videoAnnLayer,
          bbox_cx: videoAnnBox.cx, bbox_cy: videoAnnBox.cy,
          bbox_w: videoAnnBox.w, bbox_h: videoAnnBox.h,
        }),
      });
      setVideoFrameUrl(null); setVideoAnnBox(null); setVideoAnnName('');
      await loadTraining();
      alert('הנכס נשמר כדוגמת אימון');
    } catch (err) { alert(`שמירה נכשלה: ${String(err)}`); }
    finally { setVideoAnnBusy(false); }
  }

  async function saveSampleBox() {
    if (!editSample || !editBox) return;
    await api(`/training-samples/${editSample.id}`, {
      method:'PATCH', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        bbox_cx:editBox.cx, bbox_cy:editBox.cy, bbox_w:editBox.w, bbox_h:editBox.h,
        asset_type: editType || undefined,
        asset_name: editName.trim() || undefined,
      }),
    });
    setEditSample(null); setEditBox(null);
    loadTraining();
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
      <button onClick={openDetections} className={tab==='detections'?'active':''}>
        <ScanSearch size={18}/> זיהויי AI
      </button>
      <button onClick={()=>setTab('businesses')} className={tab==='businesses'?'active':''}>
        <Store size={18}/> עסקים
        {businesses.filter(b=>b.status==='draft').length > 0 && ` (${businesses.filter(b=>b.status==='draft').length})`}
      </button>
      <button onClick={()=>{setTab('candidates'); loadCandidates().catch(console.error);}} className={tab==='candidates'?'active':''}>
        <Boxes size={18}/> מועמדים
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
                  <button className="annotate-video-btn" onClick={()=>openVideoAnnotator(s)}><Tags size={15}/> סמן נכסים</button>
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
          <h2>זיהויי AI — נכסי תשתית</h2>
          <p>זיהוי מקומי (open-vocabulary) של עמודים, ארונות, שוחות ומפגעים. תוצאות ניסיוניות הדורשות אימות אנושי.</p>
        </div></div>

        <div className="route-list">
          {detRoutes.map(r => <button key={r.id}
            className={`route-item ${detRoute===r.id?'active':''}`} onClick={()=>selectDetRoute(r.id)}>
            <strong>מסלול {r.id}</strong><span>{fmtTime(r.started_at)}</span>
          </button>)}
        </div>

        {detRoute !== null && <>
          <div className="analysis-bar">
            {canValidate && <button className="primary big" onClick={()=>runAnalysis(detRoute)}
              disabled={analysisJob && ['queued','running'].includes(analysisJob.status)}>
              <ScanSearch size={18}/> ניתוח תמונות וחילוץ נכסים
            </button>}
            {analysisJob && <div className="analysis-progress">
              <span>עובד: {analysisJob.processed}/{analysisJob.total}</span>
              <span>נכסים שזוהו: {analysisJob.candidates ?? detCands.length}</span>
              <span className={`chip ${analysisJob.status==='done'?'approved':analysisJob.status==='failed'?'rejected':'draft'}`}>
                {analysisJob.status==='done'?'הושלם':analysisJob.status==='failed'?'נכשל':analysisJob.status==='queued'?'ממתין לעובד':'מנתח...'}</span>
              {analysisJob.detail && <span className="analysis-error">{analysisJob.detail}</span>}
            </div>}
          </div>

          {!detCands.length && <div className="empty-note">
            טרם נמצאו זיהויי תשתית במסלול זה. ניתן להפעיל ניתוח AI מקומי להפקת זיהויים ניסיוניים הדורשים אימות אנושי.
          </div>}

          <div className="detection-grid">
            {detCands.map(c => <div className="detection-card" key={c.id}>
              <AuthImg path={`/assets/candidates/${c.id}/image`} alt={c.category} loading="lazy"/>
              <div className="detection-body">
                <div className="detection-title">
                  {canValidate
                    ? <select value={c.category} onChange={e=>correctCand(c.id, e.target.value)}>
                        {(allCats.length ? allCats : [c.category]).map(k=>
                          <option key={k} value={k}>{TRAINING_TYPE_LABEL[k]||categoryLabels[k]||k}</option>)}
                      </select>
                    : <strong>{TRAINING_TYPE_LABEL[c.category]||c.category}</strong>}
                  <span className={`chip ${c.band==='high'?'approved':c.band==='medium'?'draft':''}`}>{Math.round(c.confidence*100)}%</span>
                </div>
                <div className="detection-meta">
                  <span className="det-badge">{c.detector==='yolo'?'YOLO':'ניסיוני open-vocab'}</span>
                  <span>{layerLabels[c.layer]||c.layer}</span>
                  {c.latitude!=null ? <span>📍</span> : <span>ללא מיקום</span>}
                </div>
                {canValidate && <div className="detection-actions">
                  <button className="approve" onClick={()=>decideCand(c.id,'approve')}><Check size={16}/> אשר → נכס</button>
                  <button className="reject" onClick={()=>decideCand(c.id,'reject')}><X size={16}/> דחה</button>
                </div>}
              </div>
            </div>)}
          </div>
        </>}
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

      {tab==='candidates' && <section>
        <div className="section-head"><div>
          <h2>מועמדי נכסים — אימות אנושי</h2>
          <p>זיהויי draft ממנוע ה־AI (open-vocabulary). אשר/דחה/תקן — כל פעולה הופכת לדאטת אימון למודל העירוני.</p>
        </div></div>

        {candSummary && <div className="cc-tiles" style={{gridTemplateColumns:'repeat(4,1fr)',marginBottom:14}}>
          <div className="cc-tile" style={{['--accent' as any]:'#2563eb'}}><div className="cc-tile-value">{candSummary.total_candidates}</div><div className="cc-tile-label">מועמדים</div></div>
          <div className="cc-tile" style={{['--accent' as any]:'#8b5cf6'}}><div className="cc-tile-value">{candSummary.proposed_assets}</div><div className="cc-tile-label">נכסים מוצעים</div></div>
          <div className="cc-tile" style={{['--accent' as any]:'#16a34a'}}><div className="cc-tile-value">{candSummary.by_band?.high||0}</div><div className="cc-tile-label">ביטחון גבוה</div></div>
          <div className="cc-tile" style={{['--accent' as any]:'#f59e0b'}}><div className="cc-tile-value">{candSummary.by_band?.medium||0}</div><div className="cc-tile-label">ביטחון בינוני</div></div>
        </div>}

        <div className="cand-filters">
          <select value={candBand} onChange={e=>setCandBand(e.target.value)}>
            <option value="">כל רמות הביטחון</option><option value="high">גבוה</option>
            <option value="medium">בינוני</option><option value="low">נמוך</option>
          </select>
          <select value={candCat} onChange={e=>setCandCat(e.target.value)}>
            <option value="">כל הקטגוריות</option>
            {candSummary && Object.entries(candSummary.by_category||{}).map(([c,n]:any)=>
              <option key={c} value={c}>{TRAINING_TYPE_LABEL[c]||categoryLabels[c]||c} ({n})</option>)}
          </select>
          <button className="link-btn" onClick={()=>loadCandidates()}>סנן</button>
        </div>

        {!candidates.length && <div className="empty-note">אין מועמדים בסינון הזה. {candSummary?'בחר קטגוריה/רמת ביטחון אחרת.':'טוען...'}</div>}
        <div className="detection-grid">
          {candidates.map(c => <div className="detection-card" key={c.id}>
            <AuthImg path={`/assets/candidates/${c.id}/image`} alt={c.category} loading="lazy"/>
            <div className="detection-body">
              <div className="detection-title">
                <select value={c.category} onChange={e=>correctCandidate(c.id, e.target.value)}>
                  {candSummary && Object.keys(candSummary.by_category||{}).map(k=>
                    <option key={k} value={k}>{TRAINING_TYPE_LABEL[k]||categoryLabels[k]||k}</option>)}
                </select>
                <span className={`chip ${c.band==='high'?'approved':c.band==='medium'?'draft':''}`}>{Math.round(c.confidence*100)}%</span>
              </div>
              <div className="detection-meta">
                <span>{layerLabels[c.layer]||c.layer}</span>
                <span>מסלול {c.route_id}</span>
                {c.latitude!=null ? <span>📍</span> : <span>ללא מיקום</span>}
              </div>
              {canValidate && <div className="detection-actions">
                <button className="approve" onClick={()=>decideCandidate(c.id,'approve')}><Check size={16}/> אשר</button>
                <button className="reject" onClick={()=>decideCandidate(c.id,'reject')}><X size={16}/> דחה</button>
              </div>}
            </div>
          </div>)}
        </div>
      </section>}

      {videoAnnSegment && <div className="modal-backdrop" onClick={closeVideoAnnotator}>
        <div className="modal video-annotator-modal" onClick={e=>e.stopPropagation()}>
          <h3>תיוג נכסים מתוך וידאו — מקטע {videoAnnSegment.id}</h3>
          {!videoFrameUrl ? <>
            {videoAnnUrl ? <video ref={videoAnnotatorRef} src={videoAnnUrl} controls playsInline preload="metadata" className="annotation-video"/>
              : <div className="media-loading">טוען וידאו...</div>}
            <div className="video-annotation-controls">
              <button type="button" onClick={()=>stepVideo(-1)}><StepBack size={16}/> שנייה אחורה</button>
              <button type="button" className="primary" onClick={captureVideoFrame}><Tags size={16}/> סמן נכס בפריים הנוכחי</button>
              <button type="button" onClick={()=>stepVideo(1)}><StepForward size={16}/> שנייה קדימה</button>
            </div>
          </> : <>
            <div className="frame-time">זמן בווידאו: {videoFrameTime.toFixed(2)} שניות</div>
            <BBoxPicker src={videoFrameUrl} onChange={setVideoAnnBox}/>
            <label className="form-check form-switch annotation-switch">
              <input className="form-check-input" type="checkbox" checked={videoAnnCustom} onChange={e=>setVideoAnnCustom(e.target.checked)}/>
              <span className="form-check-label">סוג נכס חדש / טקסט חופשי</span>
            </label>
            {videoAnnCustom ? <input className="annotation-input" placeholder="סוג חדש באנגלית, למשל solar_panel" value={videoAnnType} onChange={e=>setVideoAnnType(e.target.value)}/>
              : <select className="annotation-input" value={videoAnnType} onChange={e=>{setVideoAnnType(e.target.value);setVideoAnnLayer(trainingLayerOf(e.target.value));}}>
                  {TRAINING_TYPES.map(g => <optgroup key={g.layer} label={g.label}>{g.types.map(([v,l])=><option key={v} value={v}>{l}</option>)}</optgroup>)}
                </select>}
            <select className="annotation-input" value={videoAnnLayer} onChange={e=>setVideoAnnLayer(e.target.value)}>
              {Object.entries({...layerLabels, other:'אחר'}).map(([v,l])=><option key={v} value={v}>{l}</option>)}
            </select>
            <input className="annotation-input" placeholder="שם חופשי / תיאור הנכס" value={videoAnnName} onChange={e=>setVideoAnnName(e.target.value)}/>
            <div className="modal-actions">
              <button className="reject" type="button" onClick={()=>{setVideoFrameUrl(null);setVideoAnnBox(null);}}><X size={16}/> חזור לווידאו</button>
              <button className="approve" type="button" disabled={!videoAnnBox || videoAnnBusy} onClick={saveVideoAnnotation}><Check size={16}/> {videoAnnBusy?'שומר...':'שמור לאימון'}</button>
            </div>
          </>}
          {!videoFrameUrl && <div className="modal-actions"><button className="reject" type="button" onClick={closeVideoAnnotator}><X size={16}/> סגור</button></div>}
        </div>
      </div>}

      {tab==='training' && <section>
        <div className="section-head"><div>
          <h2>תיוג נכסים לאימון AI</h2>
          <p>צלם או העלה תמונה של נכס (עמוד חשמל, ארון תקשורת, שוחה...), בחר סוג ותן שם. הדוגמאות ישמשו לאימון מודל זיהוי ייעודי.</p>
        </div></div>

        <div className="train-mode-hint">
          💡 הכי יעיל: סמן נכסים על <b>צילומי רחוב אמיתיים</b> (אותו דומיין שהמצלמה רואה). תקריבים לא עובדים על פוטג' הרכב.
          <button type="button" className="link-btn" onClick={loadCaptures}>
            {showCaptures ? 'רענן צילומים' : 'תייג מצילומי רחוב'}
          </button>
        </div>

        {showCaptures && <div className="capture-strip">
          {!captures.length && <span className="empty-note">אין צילומי רחוב זמינים.</span>}
          {captures.map(c => <button key={c.id} className="capture-thumb" onClick={()=>{setAnnBox(null); setAnnImg(c.id);}}>
            <AuthImg path={`/images/${c.id}/file`} alt="street" loading="lazy"/>
          </button>)}
        </div>}

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

          <label className="form-check form-switch">
            <input className="form-check-input" type="checkbox" checked={trainCustom} onChange={e=>setTrainCustom(e.target.checked)}/>
            <span className="form-check-label">סוג נכס חדש</span>
          </label>
          {trainCustom ? <>
            <input placeholder="סוג חדש באנגלית, למשל solar_panel" value={trainCustomType} onChange={e=>setTrainCustomType(e.target.value)}/>
            <select value={trainCustomLayer} onChange={e=>setTrainCustomLayer(e.target.value)}>
              {Object.entries({...layerLabels, other:'אחר'}).map(([v,l])=><option key={v} value={v}>{l}</option>)}
            </select>
          </> : <select value={trainType} onChange={e=>setTrainType(e.target.value)}>
            {TRAINING_TYPES.map(g => <optgroup key={g.layer} label={g.label}>
              {g.types.map(([v,l]) => <option key={v} value={v}>{l}</option>)}
            </optgroup>)}
          </select>}
          <input placeholder="שם חופשי / תיאור הנכס" value={trainName} onChange={e=>setTrainName(e.target.value)}/>
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
                  <button className="link-btn" onClick={()=>{
                      setEditBox(t.bbox_cx != null ? {cx:t.bbox_cx, cy:t.bbox_cy!, w:t.bbox_w!, h:t.bbox_h!} : null);
                      setEditType(t.asset_type); setEditName(t.asset_name || '');
                      setEditSample({id:t.id, asset_type:t.asset_type});}}>
                    {t.bbox_cx != null ? 'ערוך' : 'סמן תיבה'}
                  </button>
                </div>
              </div>)}
            </div>}

        {annImg != null && <div className="modal-backdrop" onClick={()=>setAnnImg(null)}>
          <div className="modal" onClick={e=>e.stopPropagation()}>
            <h3>תיוג נכס בצילום רחוב</h3>
            {annImgUrl
              ? <BBoxPicker src={annImgUrl} onChange={setAnnBox}/>
              : <div className="media-loading">טוען תמונה...</div>}
            <label className="form-check form-switch annotation-switch">
              <input className="form-check-input" type="checkbox" checked={annCustom} onChange={e=>setAnnCustom(e.target.checked)}/>
              <span className="form-check-label">הוסף סוג נכס חדש</span>
            </label>
            {annCustom ? <>
              <input className="annotation-input" placeholder="סוג חדש באנגלית" value={annCustomType} onChange={e=>setAnnCustomType(e.target.value)}/>
              <select className="annotation-input" value={annCustomLayer} onChange={e=>setAnnCustomLayer(e.target.value)}>
                {Object.entries({...layerLabels, other:'אחר'}).map(([v,l])=><option key={v} value={v}>{l}</option>)}
              </select>
            </> : <select value={annType} onChange={e=>setAnnType(e.target.value)} style={{marginTop:12,width:'100%'}}>
              {TRAINING_TYPES.map(g => <optgroup key={g.layer} label={g.label}>
                {g.types.map(([v,l]) => <option key={v} value={v}>{l}</option>)}
              </optgroup>)}
            </select>}
            <input className="annotation-input" placeholder="שם חופשי / תיאור" value={annCustomName} onChange={e=>setAnnCustomName(e.target.value)}/>
            <div className="modal-actions">
              <button className="reject" onClick={()=>setAnnImg(null)}><X size={16}/> ביטול</button>
              <button className="approve" disabled={!annBox} onClick={saveAnnotation}><Check size={16}/> שמור נכס</button>
            </div>
          </div>
        </div>}

        {editSample && <div className="modal-backdrop" onClick={()=>setEditSample(null)}>
          <div className="modal" onClick={e=>e.stopPropagation()}>
            <h3>עריכת דוגמת אימון — {TRAINING_TYPE_LABEL[editType] || editType}</h3>
            {editImgUrl
              ? <BBoxPicker src={editImgUrl} onChange={setEditBox}/>
              : <div className="media-loading">טוען תמונה...</div>}
            <div className="edit-fields">
              <select value={editType} onChange={e=>setEditType(e.target.value)}>
                {TRAINING_TYPES.map(g => <optgroup key={g.layer} label={g.label}>
                  {g.types.map(([v,l]) => <option key={v} value={v}>{l}</option>)}
                </optgroup>)}
                {!TRAINING_TYPE_LABEL[editType] && <option value={editType}>{editType}</option>}
              </select>
              <input placeholder="שם/תיאור (לא חובה)" value={editName} onChange={e=>setEditName(e.target.value)}/>
            </div>
            <div className="modal-actions">
              <button className="reject" onClick={()=>setEditSample(null)}><X size={16}/> ביטול</button>
              <button className="approve" disabled={!editBox} onClick={saveSampleBox}><Check size={16}/> שמור</button>
            </div>
          </div>
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
        <CommandCenter layerLabels={layerLabels} assetTypeLabels={assetTypeLabels}
          categoryLabels={categoryLabels} statusLabels={statusLabels}/>
      </section>}
    </main>
  </div>
}
