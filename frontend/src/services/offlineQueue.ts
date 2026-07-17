// Offline upload queue backed by IndexedDB.
// Video segments and GPS points that fail to upload are persisted here
// and retried when connectivity returns (online event / periodic flush).

import { API_URL, authHeaders } from './api';

const DB_NAME = 'streetscan-offline';
const DB_VERSION = 2;
const SEGMENTS = 'pending_segments';
const GPS = 'pending_gps';
const IMAGES = 'pending_images';

type PendingSegment = { id?: number; routeId: number; capturedAt: string; blob: Blob; filename: string; orientation?: number };
type PendingGps = { id?: number; body: string };
type PendingImage = { id?: number; blob: Blob; filename: string; fields: Record<string, string> };

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(SEGMENTS)) db.createObjectStore(SEGMENTS, { keyPath: 'id', autoIncrement: true });
      if (!db.objectStoreNames.contains(GPS)) db.createObjectStore(GPS, { keyPath: 'id', autoIncrement: true });
      if (!db.objectStoreNames.contains(IMAGES)) db.createObjectStore(IMAGES, { keyPath: 'id', autoIncrement: true });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx<T>(storeName: string, mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(db => new Promise<T>((resolve, reject) => {
    const t = db.transaction(storeName, mode);
    const req = run(t.objectStore(storeName));
    t.oncomplete = () => { db.close(); resolve(req.result); };
    t.onerror = () => { db.close(); reject(t.error); };
  }));
}

export function queueSegment(routeId: number, capturedAt: string, blob: Blob, filename: string, orientation: number): Promise<unknown> {
  return tx(SEGMENTS, 'readwrite', s => s.add({ routeId, capturedAt, blob, filename, orientation }));
}

export function queueGpsPoint(body: object): Promise<unknown> {
  return tx(GPS, 'readwrite', s => s.add({ body: JSON.stringify(body) }));
}

export function queueImage(blob: Blob, filename: string, fields: Record<string, string>): Promise<unknown> {
  return tx(IMAGES, 'readwrite', s => s.add({ blob, filename, fields }));
}

export async function pendingCounts(): Promise<{ segments: number; gps: number; images: number }> {
  const [segments, gps, images] = await Promise.all([
    tx<number>(SEGMENTS, 'readonly', s => s.count()),
    tx<number>(GPS, 'readonly', s => s.count()),
    tx<number>(IMAGES, 'readonly', s => s.count()),
  ]);
  return { segments, gps, images };
}

async function uploadSegment(item: PendingSegment): Promise<void> {
  const fd = new FormData();
  fd.append('route_id', String(item.routeId));
  fd.append('captured_at', item.capturedAt);
  fd.append('orientation', String(item.orientation ?? 0));
  fd.append('file', item.blob, item.filename);
  const res = await fetch(`${API_URL}/video-segments`, { method: 'POST', headers: authHeaders(), body: fd });
  if (!res.ok) throw new Error(`upload failed: ${res.status}`);
}

async function uploadGps(item: PendingGps): Promise<void> {
  const res = await fetch(`${API_URL}/gps`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: item.body,
  });
  if (!res.ok) throw new Error(`gps upload failed: ${res.status}`);
}

async function uploadImage(item: PendingImage): Promise<void> {
  const fd = new FormData();
  for (const [k, v] of Object.entries(item.fields)) fd.append(k, v);
  fd.append('file', item.blob, item.filename);
  const res = await fetch(`${API_URL}/images`, { method: 'POST', headers: authHeaders(), body: fd });
  if (!res.ok) throw new Error(`image upload failed: ${res.status}`);
}

let flushing = false;

// Retry everything in the queue, oldest first. Stops at the first failure
// (if one upload fails, the rest will most likely fail too).
export async function flushQueue(): Promise<{ segments: number; gps: number; images: number }> {
  if (flushing) return { segments: 0, gps: 0, images: 0 };
  flushing = true;
  const done = { segments: 0, gps: 0, images: 0 };
  try {
    const segments = await tx<PendingSegment[]>(SEGMENTS, 'readonly', s => s.getAll());
    for (const item of segments) {
      await uploadSegment(item);
      await tx(SEGMENTS, 'readwrite', s => s.delete(item.id!));
      done.segments++;
    }
    const gpsItems = await tx<PendingGps[]>(GPS, 'readonly', s => s.getAll());
    for (const item of gpsItems) {
      await uploadGps(item);
      await tx(GPS, 'readwrite', s => s.delete(item.id!));
      done.gps++;
    }
    const images = await tx<PendingImage[]>(IMAGES, 'readonly', s => s.getAll());
    for (const item of images) {
      await uploadImage(item);
      await tx(IMAGES, 'readwrite', s => s.delete(item.id!));
      done.images++;
    }
  } catch {
    // Still offline or server unreachable — keep the remainder queued.
  } finally {
    flushing = false;
  }
  return done;
}

export function startAutoFlush(onFlushed?: (done: { segments: number; gps: number; images: number }) => void): () => void {
  const run = () => flushQueue().then(done => {
    if (done.segments || done.gps || done.images) onFlushed?.(done);
  });
  window.addEventListener('online', run);
  const interval = window.setInterval(run, 30000);
  run();
  return () => { window.removeEventListener('online', run); window.clearInterval(interval); };
}
