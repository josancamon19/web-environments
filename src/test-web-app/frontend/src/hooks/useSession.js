import { useEffect, useState } from 'react';
import { API_URL } from '../config/constants.js';

const useSession = () => {
  const [user, setUser] = useState(null);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    const saved = localStorage.getItem('session-email');
    if (saved) {
      setUser({ email: saved });
    }
  }, []);

  const login = async (email, password) => {
    const response = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: 'Invalid login' }));
      throw new Error(error.message || 'Invalid login');
    }
    const data = await response.json();
    if (typeof window !== 'undefined') {
      localStorage.setItem('session-email', data.email);
    }
    setUser(data);
  };

  const logout = async () => {
    await fetch(`${API_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
    if (typeof window !== 'undefined') {
      localStorage.removeItem('session-email');
    }
    setUser(null);
  };

  return { user, login, logout };
};

export default useSession;

