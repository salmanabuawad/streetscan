import { useEffect, useState } from 'react';
import { fetchMediaUrl } from './services/api';

function useMediaUrl(path: string): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let revoked: string | null = null;
    let alive = true;
    fetchMediaUrl(path).then(u => {
      if (alive) { revoked = u; setUrl(u); }
      else URL.revokeObjectURL(u);
    }).catch(() => alive && setUrl(null));
    return () => { alive = false; if (revoked) URL.revokeObjectURL(revoked); };
  }, [path]);
  return url;
}

export function AuthImg({ path, alt, ...rest }: { path: string; alt?: string } & React.ImgHTMLAttributes<HTMLImageElement>) {
  const url = useMediaUrl(path);
  return url ? <img src={url} alt={alt} {...rest}/> : <div className="media-loading">טוען...</div>;
}

export function AuthVideo({ path, ...rest }: { path: string } & React.VideoHTMLAttributes<HTMLVideoElement>) {
  const url = useMediaUrl(path);
  return url ? <video src={url} {...rest}/> : <div className="media-loading">טוען וידאו...</div>;
}
