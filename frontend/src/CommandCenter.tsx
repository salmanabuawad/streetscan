import { useEffect, useState } from 'react';
import { Route as RouteIcon, Navigation, ImageIcon, Store, ScanSearch, GraduationCap, Radio } from 'lucide-react';
import { api } from './services/api';
import MapView from './MapView';

type Overview = {
  routes: { total: number; active: number };
  distance_km: number;
  gps_points: number;
  images: number;
  videos: number;
  detections: { total: number; pending: number; approved: number };
  businesses: { total: number; by_category: Record<string, number> };
  training: { total: number; by_type: Record<string, number> };
  recent_businesses: { id: number; name: string; category: string; confidence: number; status: string }[];
};

// per-category accent for bars / chips
const CAT_COLOR: Record<string, string> = {
  pharmacy: '#3b82f6', clinic: '#38bdf8', dentist: '#22d3ee', supermarket: '#22c55e',
  grocery: '#4ade80', agriculture: '#84cc16', greengrocer: '#a3e635', restaurant: '#f97316',
  cafe: '#fb923c', bakery: '#fbbf24', barber: '#a855f7', beauty: '#e879f9', bank: '#14b8a6',
  garage: '#94a3b8', clothing: '#ec4899', hardware: '#f59e0b', mosque: '#10b981',
  holy_site: '#eab308', school: '#6366f1', municipal: '#0ea5e9', sports: '#ef4444',
  hotel: '#8b5cf6', unknown: '#64748b',
};

export default function CommandCenter({ layerLabels, assetTypeLabels, categoryLabels, statusLabels }: {
  layerLabels: Record<string, string>;
  assetTypeLabels: Record<string, string>;
  categoryLabels: Record<string, string>;
  statusLabels: Record<string, string>;
}) {
  const [o, setO] = useState<Overview | null>(null);
  useEffect(() => { api<Overview>('/overview').then(setO).catch(console.error); }, []);

  const tiles = o ? [
    { icon: <Navigation size={20} />, label: 'ק"מ נסרקו', value: o.distance_km.toFixed(1), accent: '#2563eb' },
    { icon: <RouteIcon size={20} />, label: 'מסלולים', value: o.routes.total, accent: '#0ea5e9' },
    { icon: <ImageIcon size={20} />, label: 'תמונות שטח', value: o.images, accent: '#22c55e' },
    { icon: <Store size={20} />, label: 'עסקים ואתרים', value: o.businesses.total, accent: '#f59e0b' },
    { icon: <ScanSearch size={20} />, label: 'זיהויי AI', value: o.detections.total, accent: '#f97316' },
    { icon: <GraduationCap size={20} />, label: 'דוגמאות אימון', value: o.training.total, accent: '#a855f7' },
  ] : [];

  const cats = o ? Object.entries(o.businesses.by_category).sort((a, b) => b[1] - a[1]) : [];
  const catMax = cats.length ? Math.max(...cats.map(c => c[1])) : 1;

  return <div className="cc">
    <div className="cc-hero">
      <div>
        <div className="cc-eyebrow"><Radio size={14} /> תאום דיגיטלי · Digital Twin</div>
        <h2>בוקעאתא — מרכז שליטה</h2>
        <p>מיפוי תשתיות, מפגעים ועסקים בזמן אמת מרכב המועצה</p>
      </div>
      <div className={`cc-live ${o?.routes.active ? 'on' : ''}`}>
        <span className="dot" />
        {o?.routes.active ? `${o.routes.active} מסלולים פעילים` : 'אין סריקה פעילה'}
      </div>
    </div>

    <div className="cc-tiles">
      {tiles.map((t, i) => <div className="cc-tile" key={i} style={{ ['--accent' as string]: t.accent }}>
        <div className="cc-tile-icon">{t.icon}</div>
        <div className="cc-tile-value">{t.value}</div>
        <div className="cc-tile-label">{t.label}</div>
      </div>)}
    </div>

    <div className="cc-grid">
      <div className="cc-panel cc-map">
        <div className="cc-panel-head">מפת בוקעאתא</div>
        <MapView layerLabels={layerLabels} assetTypeLabels={assetTypeLabels} />
      </div>

      <div className="cc-side">
        <div className="cc-panel">
          <div className="cc-panel-head">פילוח עסקים ואתרים</div>
          {!cats.length && <div className="cc-empty">אין עדיין נתונים</div>}
          <div className="cc-bars">
            {cats.map(([c, n]) => <div className="cc-bar-row" key={c}>
              <span className="cc-bar-label">{categoryLabels[c] || c}</span>
              <div className="cc-bar-track">
                <div className="cc-bar-fill" style={{ width: `${(n / catMax) * 100}%`, background: CAT_COLOR[c] || '#64748b' }} />
              </div>
              <span className="cc-bar-n">{n}</span>
            </div>)}
          </div>
        </div>

        <div className="cc-panel">
          <div className="cc-panel-head">נקלטו לאחרונה</div>
          {!o?.recent_businesses.length && <div className="cc-empty">עדיין ריק</div>}
          <div className="cc-feed">
            {o?.recent_businesses.map(b => <div className="cc-feed-row" key={b.id}>
              <span className="cc-dot" style={{ background: CAT_COLOR[b.category] || '#64748b' }} />
              <span className="cc-feed-name">{b.name}</span>
              <span className="cc-feed-cat">{categoryLabels[b.category] || b.category}</span>
              <span className={`chip ${b.status}`}>{statusLabels[b.status] || b.status}</span>
            </div>)}
          </div>
        </div>

        {o && (o.detections.pending > 0 || o.training.total > 0) && <div className="cc-panel cc-callout">
          {o.detections.pending > 0 && <div>⏳ {o.detections.pending} זיהויים ממתינים לאישור</div>}
          {o.training.total > 0 && <div>🎓 {o.training.total} דוגמאות אימון נאספו</div>}
        </div>}
      </div>
    </div>
  </div>;
}
