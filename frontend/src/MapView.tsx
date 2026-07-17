import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { api } from './services/api';

// Buqata (בוקעאתא), northern Golan Heights
const BUQATA_CENTER: [number, number] = [33.201, 35.779];
// zoom 14 shows the whole village + immediate surroundings; don't allow
// zooming further out than that, and keep panning near Buqata.
const MIN_ZOOM = 14;
const MAX_BOUNDS: [[number, number], [number, number]] = [[33.16, 35.72], [33.24, 35.84]];

const LAYER_COLORS: Record<string, string> = {
  telecom: '#a855f7', electricity: '#eab308', water: '#3b82f6', sewage: '#b45309',
  drainage: '#14b8a6', tunnel: '#94a3b8', road: '#64748b', public_space: '#22c55e',
};

type MapData = {
  assets: { id:number; name:string; asset_type:string; layer:string; status:string; lat:number; lng:number; underground:boolean }[];
  detections: { id:number; asset_type:string; layer:string; confidence:number; status:string; lat:number; lng:number }[];
  businesses: { id:number; name:string; category:string; confidence:number; status:string; lat:number; lng:number }[];
  tracks: { route_id:number; vehicle_name:string; points:[number, number][] }[];
};

export default function MapView({ layerLabels, assetTypeLabels }: {
  layerLabels: Record<string,string>;
  assetTypeLabels: Record<string,string>;
}) {
  const mapEl = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const [counts, setCounts] = useState<{assets:number; detections:number; businesses:number; tracks:number} | null>(null);

  useEffect(() => {
    if (!mapEl.current || mapRef.current) return;
    const map = L.map(mapEl.current, {
      zoomControl: true,
      minZoom: MIN_ZOOM,
      maxBounds: L.latLngBounds(MAX_BOUNDS),
      maxBoundsViscosity: 0.8,
    }).setView(BUQATA_CENTER, 15);
    mapRef.current = map;
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      minZoom: MIN_ZOOM,
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    // container uses aspect-ratio, so its height settles after layout — let
    // Leaflet re-measure once the box has its final size.
    setTimeout(() => map.invalidateSize(), 100);

    api<MapData>('/map-data').then(data => {
      const bounds: [number, number][] = [];

      for (const t of data.tracks) {
        L.polyline(t.points, { color: '#2563eb', weight: 4, opacity: 0.75 })
          .bindPopup(`מסלול ${t.route_id} · ${t.vehicle_name} · ${t.points.length} נקודות GPS`)
          .addTo(map);
        bounds.push(...t.points);
      }

      for (const a of data.assets) {
        L.circleMarker([a.lat, a.lng], {
          radius: 8, weight: 2, color: LAYER_COLORS[a.layer] || '#e5e7eb',
          fillColor: LAYER_COLORS[a.layer] || '#e5e7eb', fillOpacity: a.underground ? 0.25 : 0.8,
          dashArray: a.underground ? '3 3' : undefined,
        }).bindPopup(
          `<b>${a.name}</b><br/>${assetTypeLabels[a.asset_type] || a.asset_type} · ${layerLabels[a.layer] || a.layer}` +
          (a.underground ? '<br/>תת־קרקעי' : '')
        ).addTo(map);
        bounds.push([a.lat, a.lng]);
      }

      for (const d of data.detections) {
        L.circleMarker([d.lat, d.lng], {
          radius: 7, weight: 2, color: '#f97316', fillColor: '#f97316',
          fillOpacity: d.status === 'draft' ? 0.9 : 0.35,
        }).bindPopup(
          `<b>זיהוי AI: ${assetTypeLabels[d.asset_type] || d.asset_type}</b><br/>` +
          `ביטחון ${Math.round(d.confidence * 100)}% · ${layerLabels[d.layer] || d.layer}`
        ).addTo(map);
        bounds.push([d.lat, d.lng]);
      }

      for (const b of (data.businesses || [])) {
        L.marker([b.lat, b.lng], {
          icon: L.divIcon({ className: 'biz-pin', html: '🏪', iconSize: [22, 22] }),
        }).bindPopup(
          `<b>${b.name}</b><br/>${b.category} · ${Math.round(b.confidence * 100)}%`
        ).addTo(map);
        bounds.push([b.lat, b.lng]);
      }

      if (bounds.length) map.fitBounds(L.latLngBounds(bounds).pad(0.2), { maxZoom: 17 });
      setCounts({ assets: data.assets.length, detections: data.detections.length,
                  businesses: (data.businesses || []).length, tracks: data.tracks.length });
    }).catch(console.error);

    return () => { map.remove(); mapRef.current = null; };
  }, []);

  return <div>
    <div className="map-legend">
      {Object.entries(LAYER_COLORS).map(([k, c]) =>
        <span key={k} className="legend-item"><i style={{background:c}}/> {layerLabels[k] || k}</span>)}
      <span className="legend-item"><i style={{background:'#f97316'}}/> זיהוי AI</span>
      <span className="legend-item">🏪 עסק</span>
      <span className="legend-item"><i style={{background:'#2563eb', borderRadius:2, height:4}}/> מסלול</span>
    </div>
    <div ref={mapEl} className="map-container"/>
    {counts && <p className="map-counts">
      {counts.assets} נכסים · {counts.detections} זיהויים · {counts.businesses} עסקים · {counts.tracks} מסלולים עם GPS
      {!counts.assets && !counts.detections && !counts.businesses && !counts.tracks && ' — המפה תתמלא ככל שיוקלטו מסלולים ויאושרו נכסים'}
    </p>}
  </div>;
}
