import { useEffect, useMemo, useState } from 'react'
import './App.css'
import Modal from './components/Modal.jsx'
import HotelCard from './components/HotelCard.jsx'
import HotelForm from './components/HotelForm.jsx'
import { LoginPanel, RegisterPanel } from './components/AuthPanels.jsx'
import useSession from './hooks/useSession.js'
import { DEFAULT_MODAL_STATE, FAVORITES_KEY, ROUTES, API_URL, defaultImages } from './config/constants.js'
import { saveHotelsToDb, readHotelsFromDb } from './utils/storage.js'
import { parseRoute, buildUrl } from './utils/routes.js'
import { fetchHotelById } from './utils/fetchHotel.js'


const DB_NAME = 'hotelDashboard'
const DB_VERSION = 1
const STORE_NAME = 'hotels'
const DEFAULT_IMAGE_URL = 'https://plus.unsplash.com/premium_photo-1661929519129-7a76946c1d38?ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&q=80&w=1074'

function App() {
  const { user, login, logout } = useSession()
  const [route, setRoute] = useState(() => (typeof window === 'undefined' ? { name: ROUTES.HOME, search: '', params: new URLSearchParams() } : parseRoute()))
  const [hotels, setHotels] = useState([])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('idle')
  const [lastFetch, setLastFetch] = useState(() => {
    if (typeof window === 'undefined') {
      return 'Never'
    }
    return sessionStorage.getItem('last-fetch') || 'Never'
  })
  const [featuredHotelName, setFeaturedHotelName] = useState('')
  const [modalState, setModalState] = useState(DEFAULT_MODAL_STATE)
  const [authMode, setAuthMode] = useState('login')
  const [favoriteIds, setFavoriteIds] = useState(() => {
    if (typeof window === 'undefined') {
      return []
    }
    try {
      const raw = localStorage.getItem(FAVORITES_KEY)
      return raw ? JSON.parse(raw) : []
    } catch (error) {
      return []
    }
  })
  const [favoriteDetails, setFavoriteDetails] = useState({})
  const isFavoritesView = route.name === ROUTES.FAVORITES

  const filteredHotels = useMemo(() => {
    if (!search) {
      return hotels
    }
    const query = search.toLowerCase()
    return hotels.filter((hotel) => hotel.name.toLowerCase().includes(query) || hotel.city.toLowerCase().includes(query))
  }, [hotels, search])

  const stats = useMemo(() => {
    if (!hotels.length) {
      return { total: 0, average: 0, cities: 0 }
    }
    const total = hotels.length
    const sum = hotels.reduce((acc, hotel) => acc + Number(hotel.pricePerNight || 0), 0)
    const average = Math.round(sum / total)
    const cityCounts = new Set(hotels.map((hotel) => hotel.city || 'Unknown'))
    return { total, average, cities: cityCounts.size }
  }, [hotels])

  const favoriteHotels = useMemo(() => favoriteIds.map((id) => favoriteDetails[id]).filter(Boolean), [favoriteIds, favoriteDetails])
  const favoritesLoading = favoriteIds.length > 0 && favoriteHotels.length < favoriteIds.length

  const navigate = (name, params = {}) => {
    if (typeof window === 'undefined') {
      return
    }
    const url = buildUrl(name, params)
    const current = `${window.location.pathname}${window.location.search}`
    if (current !== url) {
      window.history.pushState({}, '', url)
    }
    setRoute(parseRoute())
  }

  const closeModal = (options = { updateRoute: true }) => {
    setModalState(DEFAULT_MODAL_STATE)
    if (!options.updateRoute) {
      return
    }
    if (modalState.context === 'favorites') {
      navigate(ROUTES.FAVORITES)
    } else if (modalState.context === 'home') {
      navigate(ROUTES.HOME)
    }
  }

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const handlePopState = () => {
      setRoute(parseRoute())
    }
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(favoriteIds))
  }, [favoriteIds])

  useEffect(() => {
    if (!favoriteIds.length) {
      setFavoriteDetails({})
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const entries = await Promise.all(
          favoriteIds.map(async (id) => {
            const existing = hotels.find((hotel) => hotel.id === id)
            if (existing) {
              return [id, existing]
            }
            const data = await fetchHotelById(id)
            return [id, data]
          }),
        )
        if (cancelled) {
          return
        }
        const map = {}
        entries.forEach(([id, data]) => {
          map[id] = data
        })
        setFavoriteDetails(map)
      } catch (error) {
        if (!cancelled) {
          setFavoriteDetails({})
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [favoriteIds, hotels])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const storedId = localStorage.getItem('featured-hotel')
    if (!storedId) {
      setFeaturedHotelName('')
      return
    }
    const match = hotels.find((item) => item.id === storedId)
    setFeaturedHotelName(match ? match.name : '')
  }, [hotels])

  useEffect(() => {
    let cancelled = false
    const { name, params } = route
    if (name === ROUTES.DETAILS) {
      const id = params.get('id')
      if (id) {
        setModalState({ open: true, status: 'loading', hotel: null, message: '', context: 'home' })
        fetchHotelById(id)
          .then((data) => {
            if (cancelled) {
              return
            }
            setModalState({ open: true, status: 'ready', hotel: data, message: '', context: 'home' })
            if (favoriteIds.includes(data.id)) {
              setFavoriteDetails((previous) => ({ ...previous, [data.id]: data }))
            }
          })
          .catch((error) => {
            if (cancelled) {
              return
            }
            setModalState({ open: true, status: 'error', hotel: null, message: error.message, context: 'home' })
          })
      } else {
        setModalState((previous) => (previous.context === 'home' ? { open: false, status: 'idle', hotel: null, message: '', context: null } : previous))
      }
    } else if (name === ROUTES.FAVORITES) {
      const id = params.get('hotel_id')
      if (id) {
        setModalState({ open: true, status: 'loading', hotel: null, message: '', context: 'favorites' })
        fetchHotelById(id)
          .then((data) => {
            if (cancelled) {
              return
            }
            setModalState({ open: true, status: 'ready', hotel: data, message: '', context: 'favorites' })
            if (favoriteIds.includes(data.id)) {
              setFavoriteDetails((previous) => ({ ...previous, [data.id]: data }))
            }
          })
          .catch((error) => {
            if (cancelled) {
              return
            }
            setModalState({ open: true, status: 'error', hotel: null, message: error.message, context: 'favorites' })
          })
      } else {
        setModalState((previous) => (previous.context === 'favorites' ? { open: false, status: 'idle', hotel: null, message: '', context: null } : previous))
      }
    } else {
      setModalState((previous) => (previous.context === 'home' ? { open: false, status: 'idle', hotel: null, message: '', context: null } : previous))
    }
    return () => {
      cancelled = true
    }
  }, [route.name, route.search, favoriteIds])

  const fetchHotels = async () => {
    setStatus('loading')
    try {
      const response = await fetch(`${API_URL}/hotels`, { credentials: 'include' })
      if (!response.ok) {
        throw new Error('Failed to fetch hotels')
      }
      const data = await response.json()
      setHotels(data.items)
      await saveHotelsToDb(data.items)
      const stamp = new Date().toLocaleString()
      if (typeof window !== 'undefined') {
        sessionStorage.setItem('last-fetch', stamp)
      }
      setLastFetch(stamp)
      setStatus('ready')
    } catch (error) {
      try {
        const cached = await readHotelsFromDb()
        if (cached.length) {
          setHotels(cached)
          setLastFetch('Offline cache')
          setStatus('offline')
        } else {
          setStatus('error')
        }
      } catch (cacheError) {
        setStatus('error')
      }
    }
  }

  useEffect(() => {
    fetchHotels()
  }, [])

  const toggleFavorite = (hotel) => {
    if (!hotel) {
      return
    }
    setFavoriteIds((previous) => {
      const exists = previous.includes(hotel.id)
      const next = exists ? previous.filter((item) => item !== hotel.id) : [...previous, hotel.id]
      setFavoriteDetails((details) => {
        if (exists) {
          const map = { ...details }
          delete map[hotel.id]
          return map
        }
        return { ...details, [hotel.id]: hotel }
      })
      setModalState((current) => {
        if (!current.open || !current.hotel || current.hotel.id !== hotel.id) {
          return current
        }
        return { ...current, message: exists ? 'Removed from favorites.' : 'Added to favorites.' }
      })
      return next
    })
  }

  const handleCreateHotel = async (payload) => {
    const response = await fetch(`${API_URL}/hotels`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      throw new Error('Failed to create hotel')
    }
    await fetchHotels()
    setModalState({ open: true, status: 'message', hotel: null, message: 'Hotel registered successfully.', context: 'message' })
  }

  const handleLogin = async (email, password) => {
    await login(email, password)
  }

  const handleRegister = async (email, password) => {
    const response = await fetch(`${API_URL}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: 'Registration failed' }))
      throw new Error(error.message || 'Registration failed')
    }
    setAuthMode('login')
  }

  const handleLogout = async () => {
    await logout()
    navigate(ROUTES.HOME)
  }

  const handleViewFromHome = (id) => {
    navigate(ROUTES.DETAILS, { id })
  }

  const handleViewFromFavorites = (id) => {
    navigate(ROUTES.FAVORITES, { hotel_id: id })
  }

  const modalTitle = modalState.context === 'message' ? 'Notice' : modalState.hotel?.name || 'Hotel details'
  const modalContent = (() => {
    if (modalState.context === 'message') {
      return <p className="modal-status">{modalState.message}</p>
    }
    if (modalState.status === 'loading') {
      return <p className="modal-status">Loading details…</p>
    }
    if (modalState.status === 'error') {
      return <p className="modal-error">{modalState.message}</p>
    }
    if (!modalState.hotel) {
      return null
    }
    const isFavorite = favoriteIds.includes(modalState.hotel.id)
    const image = modalState.hotel.imageUrl || defaultImages[modalState.hotel.name.length % defaultImages.length]
    return (
      <div className="modal-body">
        <img src={image} alt={modalState.hotel.name} />
        <p>{modalState.hotel.description || 'No description provided.'}</p>
        <p>City: {modalState.hotel.city}</p>
        <p>Price: ${modalState.hotel.pricePerNight} per night</p>
        {modalState.message && modalState.context !== 'message' ? <p className="modal-status">{modalState.message}</p> : null}
        <div className="modal-actions">
          <button type="button" onClick={() => toggleFavorite(modalState.hotel)}>
            {isFavorite ? 'Remove from favorites' : 'Add to favorites'}
          </button>
          <button type="button" className="secondary-button" onClick={() => closeModal()}>
            Close
        </button>
        </div>
      </div>
    )
  })()

  return (
    <div className="app-layout">
      <header>
        <div className="brand">
          <h1>
            <a
              href="/"
              onClick={(event) => {
                event.preventDefault()
                navigate(ROUTES.HOME)
              }}
            >
              StayScout
            </a>
          </h1>
          <p>Hotels made simple, with a touch of Zillow inspiration.</p>
        </div>
        <nav>
          <a
            href="#featured"
            className={route.name === ROUTES.HOME ? 'active' : ''}
            onClick={(event) => {
              event.preventDefault()
              navigate(ROUTES.HOME)
              requestAnimationFrame(() => {
                window.location.hash = 'featured'
                const target = document.getElementById('featured')
                if (target) {
                  target.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }
              })
            }}
          >
            Featured
          </a>
          <a
            href="/favorites"
            className={route.name === ROUTES.FAVORITES ? 'active' : ''}
            onClick={(event) => {
              event.preventDefault()
              navigate(ROUTES.FAVORITES)
            }}
          >
            Favorites
          </a>
          <a href="/about.html">About</a>
          <a href="https://www.zillow.com/" target="_blank" rel="noreferrer">
            Zillow
          </a>
        </nav>
        {user ? (
          <div className="user-pill">
            <span>{user.email}</span>
            <button type="button" onClick={handleLogout}>
              Logout
            </button>
          </div>
        ) : (
          <a className="login-link" href="#access">
            Owner access
          </a>
        )}
      </header>

      <main>
        {isFavoritesView ? (
          <>
            <section className="favorites-hero">
      <div>
                <h2>Your favorite stays</h2>
                <p>Hotels you bookmarked live here. Open details to revisit the property information.</p>
              </div>
              <button type="button" className="secondary-button" onClick={() => navigate(ROUTES.HOME)}>
                Back to hotels
              </button>
            </section>
            <section className="favorites-grid">
              {favoritesLoading ? (
                <p className="favorites-status">Loading favorites…</p>
              ) : favoriteHotels.length ? (
                <div className="hotel-list">
                  {favoriteHotels.map((hotel) => (
                    <HotelCard
                      key={hotel.id}
                      hotel={hotel}
                      isFavorite={true}
                      onToggleFavorite={toggleFavorite}
                      onView={handleViewFromFavorites}
                    />
                  ))}
      </div>
              ) : (
                <div className="favorites-empty">
                  <p>You have not saved any hotels yet.</p>
                  <button type="button" onClick={() => navigate(ROUTES.HOME)}>
                    Browse hotels
        </button>
                </div>
              )}
            </section>
          </>
        ) : (
          <>
            <section className="hero" id="access">
              <div className="hero-copy">
                <h2>Find and manage boutique stays</h2>
                <p>
                  Browse curated hotels, compare nightly rates, and add your own listings with a quick form. IndexedDB keeps
                  the list available after you fetch it once.
                </p>
                <div className="hero-stats">
                  <div>
                    <strong>{stats.total}</strong>
                    <span>Active stays</span>
                  </div>
                  <div>
                    <strong>${stats.average}</strong>
                    <span>Average nightly</span>
                  </div>
                  <div>
                    <strong>{stats.cities}</strong>
                    <span>Cities listed</span>
                  </div>
                </div>
              </div>
              <div className="hero-side">
                {user ? (
                  <div className="welcome-card">
                    <h3>Welcome back</h3>
                    <p>Jump to the owner tools below to publish a fresh property.</p>
                    <a href="#owner-form">Add new hotel</a>
                  </div>
                ) : (
                  <div className="auth-card">
                    <div className="auth-tabs">
                      <button
                        type="button"
                        className={authMode === 'login' ? 'active' : ''}
                        onClick={() => setAuthMode('login')}
                      >
                        Sign in
                      </button>
                      <button
                        type="button"
                        className={authMode === 'register' ? 'active' : ''}
                        onClick={() => setAuthMode('register')}
                      >
                        Register
                      </button>
                    </div>
                    {authMode === 'login' ? <LoginPanel onLogin={handleLogin} /> : <RegisterPanel onRegister={handleRegister} />}
                  </div>
                )}
                <div className="hero-image" aria-hidden="true" />
              </div>
            </section>

            <section className="video-highlight">
              <div className="video-copy">
                <h2>Take a quick tour</h2>
                <p>See how StayScout surfaces boutique stays, compares prices, and keeps your favorites in sync.</p>
                <ul>
                  <li>Highlights curated properties across cities</li>
                  <li>Add new listings with a simple owner workflow</li>
                  <li>Favorite hotels and review them later from any device</li>
                </ul>
              </div>
              <div className="video-frame">
                <iframe
                  title="StayScout intro"
                  src="https://www.youtube.com/watch?v=aFHji91unN4"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  loading="lazy"
                />
              </div>
            </section>

            <section className="search-section">
              <div className="search-box">
                <label>
                  Search hotels
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search by name or city"
                  />
                </label>
                <p className="status">Status: {status}</p>
                <p className="featured">Saved locally: {featuredHotelName || 'None yet'}</p>
              </div>
              <div className="search-side">
                <div className="preview-card">
                  <h3>Need a wider map?</h3>
                  <p>Open the static Zillow page for an embedded marketplace feed.</p>
                  <a href="/zillow.html" target="_blank" rel="noreferrer">
                    Open Zillow style view
                  </a>
                </div>
              </div>
            </section>

            <section className="content-grid" id="featured">
              <div className="list-wrapper">
                <div className="section-heading">
                  <h2>Browse stays</h2>
                  <p>Open listings stay on this page so you keep the single-page flow.</p>
                </div>
                <div className="hotel-list">
                  {filteredHotels.map((hotel) => (
                    <HotelCard
                      key={hotel.id}
                      hotel={hotel}
                      isFavorite={favoriteIds.includes(hotel.id)}
                      onToggleFavorite={toggleFavorite}
                      onView={handleViewFromHome}
                    />
                  ))}
                  {!filteredHotels.length ? <p className="empty-state">No hotels match the search.</p> : null}
                </div>
              </div>
              <aside className="owner-tools" id="owner-form">
                <h2>Owner tools</h2>
                <p>Authenticated users can register new hotels. Data is stored server-side and cached locally.</p>
                {user ? (
                  <HotelForm onCreate={handleCreateHotel} />
                ) : (
                  <div className="owner-locked">
                    <p>Sign in above to unlock the registration form.</p>
                  </div>
                )}
                <dl>
                  <div>
                    <dt>Last sync</dt>
                    <dd>{lastFetch}</dd>
                  </div>
                  <div>
                    <dt>Saved locally</dt>
                    <dd>{featuredHotelName || 'None yet'}</dd>
      </div>
                </dl>
              </aside>
            </section>
          </>
        )}
      </main>

      <footer>
        <div>
          <strong>StayScout</strong>
          <p>Hybrid experience merging React SPA routes with classic pages.</p>
        </div>
        <div>
          <span>Explore</span>
          <a href="/about.html">About</a>
          <a href="/contact.html" target="_blank" rel="noreferrer">
            Contact
          </a>
          <a href="/favorites" onClick={(event) => { event.preventDefault(); navigate(ROUTES.FAVORITES) }}>
            Favorites
          </a>
        </div>
        <div>
          <span>Shortcuts</span>
          <a href="#featured">Featured stays</a>
          <a href="#owner-form">Add hotel</a>
          <a href="/register-hotel.html">Static register</a>
        </div>
      </footer>

      <Modal open={modalState.open} title={modalTitle} onClose={() => closeModal({ updateRoute: modalState.context !== 'message' })}>
        {modalContent}
      </Modal>
    </div>
  )
}

export default App
