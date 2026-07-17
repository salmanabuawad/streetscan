import { useState } from 'react';
import { LogIn } from 'lucide-react';
import { api, setToken } from './services/api';

type LoginResult = { token: string; username: string; display_name: string; role: string };

export default function Login({ onLogin }: { onLogin: (user: LoginResult) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const result = await api<LoginResult>('/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      setToken(result.token);
      onLogin(result);
    } catch {
      setError('שם משתמש או סיסמה שגויים');
    } finally {
      setBusy(false);
    }
  }

  return <div className="login-shell">
    <form className="login-card" onSubmit={submit}>
      <h1>Buqata StreetScan</h1>
      <p>מיפוי תשתיות ומפגעים — כניסת משתמשים</p>
      <input placeholder="שם משתמש" value={username} autoComplete="username"
             onChange={e => setUsername(e.target.value)} required/>
      <input placeholder="סיסמה" type="password" value={password} autoComplete="current-password"
             onChange={e => setPassword(e.target.value)} required/>
      {error && <div className="login-error">{error}</div>}
      <button className="primary big" type="submit" disabled={busy}>
        <LogIn size={18}/> {busy ? 'מתחבר...' : 'כניסה'}
      </button>
    </form>
  </div>;
}
