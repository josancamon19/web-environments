import { API_URL, defaultImages } from '../config/constants.js';

export async function fetchHotelById(id) {
  const response = await fetch(`${API_URL}/hotels/${id}`);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || 'Unable to load hotel details.');
  }
  return response.json();
}

export function getHotelImage(hotel) {
  if (!hotel) {
    return defaultImages[0];
  }
  return hotel.imageUrl || defaultImages[hotel.name.length % defaultImages.length];
}

