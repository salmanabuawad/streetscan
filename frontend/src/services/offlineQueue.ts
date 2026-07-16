// Offline upload queue backed by IndexedDB.
// Video segments and GPS points that fail to upload are persisted here
// and retried when connectivity returns (online event / periodic flush).

import { API_URL } from './api';

const DB_NAME = 'streetscan-offline';
const DB_VERSION = 1;
const SEGMENTS = 'pending_segments';
const GPS = 'pending_gps';

type PendingSegment = { id?: number; routeId: number; capturedAt: string; blob: Blob; filename: string };
type PendingGps = { id?: number; body: string };

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(SEGMENTS)) db.createObjectStore(SEGMENTS, { keyPath: 'id', autoIncrement: true });
      if (!db.objectStoreNames.contains(GPS)) db.createObjectStore(GPS, { keyPath: 'id', autoIncrement: true });
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

export function queueSegment(routeId: number, capturedAt: string, blob: Blob, filename: string): Promise<unknown> {
  return tx(SEGMENTS, 'readwrite', s => s.add({ routeId, capturedAt, blob, filename }));
}

export function queueGpsPoint(body: object): Promise<unknown> {
  return tx(GPS, 'readwrite', s => s.add({ body: JSON.stringify(body) }));
}

export async function pendingCounts(): Promise<{ segments: number; gps: number }> {
  const [segments, gps] = await Promise.all([
    tx<number>(SEGMENTS, 'readonly', s => s.count()),
    tx<number>(GPS, 'readonly', s => s.count()),
  ]);
  return { segments, gps };
}

async function uploadSegment(item: PendingSegment): Promise<void> {
  const fd = new FormData();
  fd.append('route_id', String(item.routeId));
  fd.append('captured_at', item.capturedAt);
  fd.append('file', item.blob, item.filename);
  const res = await fetch(`${API_URL}/video-segments`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`upload failed: ${res.status}`);
}

async function uploadGps(item: PendingGps): Promise<void> {
  const res = await fetch(`${API_URL}/gps`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: item.body,
  });
  if (!res.ok) throw new Error(`gps upload failed: ${res.status}`);
}

let flushing = false;

// Retry everything in the queue, oldest first. Stops at the first failure
// (if one upload fails, the rest will most likely fail too).
export async function flushQueue(): Promise<{ segments: number; gps: number }> {
  if (flushing) return { segments: 0, gps: 0 };
  flushing = true;
  const done = { segments: 0, gps: 0 };
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
  } catch {
    // Still offline or server unreachable — keep the remainder queued.
  } finally {
    flushing = false;
  }
  return done;
}

export function startAutoFlush(onFlushed?: (done: { segments: number; gps: number }) => void): () => void {
  const run = () => flushQueue().then(done => {
    if (done.segments || done.gps) onFlushed?.(done);
  });
  window.addEventListener('online', run);
  const interval = window.setInterval(run, 30000);
  run();
  return () => { window.removeEventListener('online', run); window.clearInterval(interval); };
}
