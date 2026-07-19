import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { api } from './services/api';

// Buqata (בוקעאתא), northern Golan Heights
const BUQATA_CENTER: [number, number] = [33.201, 35.779];
// Lock the view on Buqata: zoom 15 keeps the village filling the frame, and a
// tight hard boundary (viscosity 1.0 below) stops panning out to Mas'ade /
// Odem forest. Roughly the village plus a short margin.
const MIN_ZOOM = 15;
const MAX_BOUNDS: [[number, number], [number, number]] = [[33.187, 35.762], [33.216, 35.797]];

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
  // one Leaflet layer group per asset type, so the dropdown can toggle them
  const typeLayers = useRef<Record<string, L.LayerGroup>>({});
  const [assetTypes, setAssetTypes] = useState<{type:string; count:number}[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // show/hide type groups when the filter changes
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    for (const [type, group] of Object.entries(typeLayers.current)) {
      if (hidden.has(type)) map.removeLayer(group);
      else group.addTo(map);
    }
  }, [hidden, assetTypes]);

  function toggleType(type: string) {
    setHidden(prev => {
      const next = new Set(prev);
      next.has(type) ? next.delete(type) : next.add(type);
      return next;
    });
  }

  useEffect(() => {
    if (!mapEl.current || mapRef.current) return;
    const map = L.map(mapEl.current, {
      zoomControl: true,
      minZoom: MIN_ZOOM,
      maxBounds: L.latLngBounds(MAX_BOUNDS),
      maxBoundsViscosity: 1.0,   // hard wall — panning cannot leave Buqata
    }).setView(BUQATA_CENTER, MIN_ZOOM);
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

      // group asset markers by type so they can be filtered from the dropdown
      const perType: Record<string, number> = {};
      for (const a of data.assets) {
        const marker = L.circleMarker([a.lat, a.lng], {
          radius: 8, weight: 2, color: LAYER_COLORS[a.layer] || '#e5e7eb',
          fillColor: LAYER_COLORS[a.layer] || '#e5e7eb', fillOpacity: a.underground ? 0.25 : 0.8,
          dashArray: a.underground ? '3 3' : undefined,
        }).bindPopup(
          `<b>${a.name}</b><br/>${assetTypeLabels[a.asset_type] || a.asset_type} · ${layerLabels[a.layer] || a.layer}` +
          (a.underground ? '<br/>תת־קרקעי' : '')
        );
        if (!typeLayers.current[a.asset_type]) typeLayers.current[a.asset_type] = L.layerGroup();
        typeLayers.current[a.asset_type].addLayer(marker);
        perType[a.asset_type] = (perType[a.asset_type] || 0) + 1;
        bounds.push([a.lat, a.lng]);
      }
      Object.values(typeLayers.current).forEach(g => g.addTo(map));
      setAssetTypes(Object.entries(perType).map(([type, count]) => ({ type, count }))
        .sort((a, b) => b.count - a.count));

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

      // fit to the data, but never outside the Buqata boundary (a stray
      // coordinate must not drag the view off the village)
      if (bounds.length) {
        const limit = L.latLngBounds(MAX_BOUNDS);
        const inside = bounds.filter(p => limit.contains(L.latLng(p[0], p[1])));
        if (inside.length) {
          map.fitBounds(L.latLngBounds(inside).pad(0.2), { maxZoom: 17 });
          if (!limit.contains(map.getCenter())) map.setView(BUQATA_CENTER, MIN_ZOOM);
        }
      }
      setCounts({ assets: data.assets.length, detections: data.detections.length,
                  businesses: (data.businesses || []).length, tracks: data.tracks.length });
    }).catch(console.error);

    return () => { map.remove(); mapRef.current = null; };
  }, []);

  const shownTypes = assetTypes.filter(t => !hidden.has(t.type)).length;

  return <div>
    <div className="map-toolbar">
      <details className="type-filter">
        <summary>
          סוגי נכסים במפה ({shownTypes}/{assetTypes.length})
        </summary>
        <div className="type-filter-menu">
          <div className="type-filter-actions">
            <button type="button" onClick={()=>setHidden(new Set())}>הצג הכל</button>
            <button type="button" onClick={()=>setHidden(new Set(assetTypes.map(t=>t.type)))}>הסתר הכל</button>
          </div>
          {!assetTypes.length && <div className="type-filter-empty">אין נכסים ממופים</div>}
          {assetTypes.map(t => <label key={t.type}>
            <input type="checkbox" checked={!hidden.has(t.type)} onChange={()=>toggleType(t.type)}/>
            <span>{assetTypeLabels[t.type] || t.type}</span>
            <em>{t.count}</em>
          </label>)}
        </div>
      </details>
    </div>
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
