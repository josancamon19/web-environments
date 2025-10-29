import { ROUTES } from '../config/constants.js';

export function parseRoute(location = window.location) {
  const { pathname, search } = location;
  const params = new URLSearchParams(search);
  if (pathname === '/favorites') {
    return { name: ROUTES.FAVORITES, search, params };
  }
  if (pathname === '/hotel-details') {
    return { name: ROUTES.DETAILS, search, params };
  }
  return { name: ROUTES.HOME, search, params };
}

export function buildUrl(name, params = {}) {
  if (name === ROUTES.FAVORITES) {
    const query = params.hotel_id ? `?hotel_id=${encodeURIComponent(params.hotel_id)}` : '';
    return `/favorites${query}`;
  }
  if (name === ROUTES.DETAILS) {
    const id = params.id ? String(params.id) : '';
    return id ? `/hotel-details?id=${encodeURIComponent(id)}` : '/hotel-details';
  }
  return '/';
}

