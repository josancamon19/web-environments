import { useState } from 'react';

export const LoginPanel = ({ onLogin }) => {
  const [email, setEmail] = useState('demo@example.com');
  const [password, setPassword] = useState('demo');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      await onLogin(email, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="login-panel" onSubmit={handleSubmit}>
      <h3>Owner access</h3>
      {error ? <p className="error">{error}</p> : null}
      <label>
        Email
        <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
      </label>
      <label>
        Password
        <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
      </label>
      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? 'Signing in…' : 'Login'}
      </button>
    </form>
  );
};

export const RegisterPanel = ({ onRegister }) => {
  const [email, setEmail] = useState('new@example.com');
  const [password, setPassword] = useState('demo');
  const [confirm, setConfirm] = useState('demo');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setSuccess('');
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    setIsSubmitting(true);
    try {
      await onRegister(email, password);
      setSuccess('Account created. You can sign in now.');
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="login-panel" onSubmit={handleSubmit}>
      <h3>Create account</h3>
      {error ? <p className="error">{error}</p> : null}
      {success ? <p className="success">{success}</p> : null}
      <label>
        Email
        <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
      </label>
      <label>
        Password
        <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
      </label>
      <label>
        Confirm password
        <input value={confirm} onChange={(event) => setConfirm(event.target.value)} type="password" required />
      </label>
      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? 'Creating…' : 'Register'}
      </button>
    </form>
  );
};

