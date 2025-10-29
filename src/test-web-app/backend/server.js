const express = require('express');
const cors = require('cors');
const cookieParser = require('cookie-parser');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 4000;
const DATA_FILE = path.join(__dirname, 'data', 'hotels.json');
const SESSIONS_FILE = path.join(__dirname, 'data', 'sessions.json');
const USERS_FILE = path.join(__dirname, 'data', 'users.json');
const isProduction = process.env.NODE_ENV === 'production';
const allowedOrigins = process.env.ALLOWED_ORIGINS
  ? process.env.ALLOWED_ORIGINS.split(',')
  : [];

let hotels = [];
let sessions = {};
let users = [];

const cookieOptions = {
  httpOnly: true,
  sameSite: isProduction ? 'none' : 'lax',
  secure: isProduction,
  maxAge: 1000 * 60 * 60 * 12,
};

function ensureDataFile(filePath, defaultContent) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  if (!fs.existsSync(filePath)) {
    fs.writeFileSync(filePath, defaultContent);
  }
}

function loadHotels() {
  try {
    ensureDataFile(DATA_FILE, '[]');
    const content = fs.readFileSync(DATA_FILE, 'utf-8');
    hotels = JSON.parse(content);
  } catch (error) {
    hotels = [];
  }
}

function saveHotels() {
  ensureDataFile(DATA_FILE, '[]');
  fs.writeFileSync(DATA_FILE, JSON.stringify(hotels, null, 2));
}

function loadSessions() {
  try {
    ensureDataFile(SESSIONS_FILE, '{}');
    const content = fs.readFileSync(SESSIONS_FILE, 'utf-8');
    sessions = JSON.parse(content);
  } catch (error) {
    sessions = {};
  }
}

function saveSessions() {
  ensureDataFile(SESSIONS_FILE, '{}');
  fs.writeFileSync(SESSIONS_FILE, JSON.stringify(sessions, null, 2));
}

function removeSessionsByEmail(email) {
  const remaining = {};
  Object.entries(sessions).forEach(([key, value]) => {
    if (value.email !== email) {
      remaining[key] = value;
    }
  });
  sessions = remaining;
  saveSessions();
}

function loadUsers() {
  try {
    ensureDataFile(USERS_FILE, '[]');
    const content = fs.readFileSync(USERS_FILE, 'utf-8');
    users = JSON.parse(content);
  } catch (error) {
    users = [];
  }
}

function saveUsers() {
  ensureDataFile(USERS_FILE, '[]');
  fs.writeFileSync(USERS_FILE, JSON.stringify(users, null, 2));
}

function ensureAuthenticated(req, res, next) {
  const token = req.cookies.sessionToken;
  console.log('token', token);
  console.log('sessions', sessions[token]);
  console.log(token && sessions[token]);
  if (!token || !sessions[token]) {
    return res.status(401).json({ message: 'Unauthorized' });
  }
  req.user = sessions[token];
  next();
}

loadSessions();
loadUsers();
loadHotels();

app.use(
  cors({
    origin: allowedOrigins.length ? allowedOrigins : true,
    credentials: true,
  }),
);
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(cookieParser());

app.post('/auth/register', (req, res) => {
  const { email, password } = req.body || {};
  if (!email || !password) {
    return res.status(400).json({ message: 'Email and password required' });
  }
  const exists = users.find((user) => user.email === email);
  if (exists) {
    return res.status(409).json({ message: 'User already exists' });
  }
  const user = {
    id: crypto.randomUUID(),
    email,
    password,
    createdAt: new Date().toISOString(),
  };
  users.push(user);
  saveUsers();
  removeSessionsByEmail(email);
  res.status(201).json({ email });
});

app.post('/auth/login', (req, res) => {
  const { email, password } = req.body;
  if (!email || !password) {
    return res.status(400).json({ message: 'Email and password required' });
  }
  const user = users.find((item) => item.email === email && item.password === password);
  if (!user) {
    return res.status(401).json({ message: 'Invalid credentials' });
  }
  removeSessionsByEmail(email);
  const token = crypto.randomUUID();
  sessions[token] = { email, createdAt: new Date().toISOString() };
  saveSessions();
  res.cookie('sessionToken', token, cookieOptions);
  res.json({ email });
});

app.get('/auth/me', (req, res) => {
  const token = req.cookies.sessionToken;
  if (!token || !sessions[token]) {
    return res.status(401).json({ message: 'Unauthorized' });
  }
  res.json(sessions[token]);
});

app.post('/auth/logout', ensureAuthenticated, (req, res) => {
  const token = req.cookies.sessionToken;
  delete sessions[token];
  saveSessions();
  res.clearCookie('sessionToken', cookieOptions);
  res.json({ success: true });
});

app.get('/hotels', (req, res) => {
  const { q, city } = req.query;
  let result = hotels.slice();
  if (q) {
    const query = q.toLowerCase();
    result = result.filter((hotel) => hotel.name.toLowerCase().includes(query) || hotel.city.toLowerCase().includes(query));
  }
  if (city) {
    const cityFilter = city.toLowerCase();
    result = result.filter((hotel) => hotel.city.toLowerCase() === cityFilter);
  }
  res.json({ items: result });
});

app.get('/hotels/:id', (req, res) => {
  const hotel = hotels.find((item) => item.id === req.params.id);
  if (!hotel) {
    return res.status(404).json({ message: 'Not found' });
  }
  res.json(hotel);
});

app.post('/hotels', ensureAuthenticated, (req, res) => {
  const payload = req.body || {};
  const { name, city, pricePerNight, imageUrl, description } = payload;
  if (!name || !city || !pricePerNight) {
    return res.status(400).json({ message: 'Missing fields' });
  }
  const id = crypto.randomUUID();
  const hotel = {
    id,
    name,
    city,
    pricePerNight: Number(pricePerNight),
    imageUrl: imageUrl || '',
    description: description || '',
    createdAt: new Date().toISOString(),
  };
  hotels.push(hotel);
  saveHotels();
  res.status(201).json(hotel);
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

app.use((err, req, res, next) => {
  res.status(500).json({ message: err.message || 'Server error' });
});

app.listen(PORT, () => {
  console.log(`API listening on ${PORT}`);
});

