export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:4000';
export const DB_NAME = 'hotelDashboard';
export const DB_VERSION = 1;
export const STORE_NAME = 'hotels';
export const FAVORITES_KEY = 'favorite-hotels';
export const DEFAULT_IMAGE_URL = 'https://plus.unsplash.com/premium_photo-1661929519129-7a76946c1d38?ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&q=80&w=1074';
export const defaultImages = [
  'https://images.unsplash.com/photo-1600585154340-0ef3c08cfb7e?auto=format&fit=crop&w=800&q=80',
  'https://images.unsplash.com/photo-1568605114967-8130f3a36994?auto=format&fit=crop&w=800&q=80',
  'https://images.unsplash.com/photo-1611892440504-42a792e24d32?auto=format&fit=crop&w=800&q=80',
];

export const ROUTES = {
  HOME: 'home',
  FAVORITES: 'favorites',
  DETAILS: 'hotel-details',
};

export const DEFAULT_MODAL_STATE = { open: false, status: 'idle', hotel: null, message: '', context: null };

