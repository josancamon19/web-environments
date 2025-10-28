import { defaultImages } from '../config/constants.js';

const HotelCard = ({ hotel, isFavorite, onToggleFavorite, onView }) => {
  const image = hotel.imageUrl || defaultImages[hotel.name.length % defaultImages.length];
  return (
    <article className={`hotel-card${isFavorite ? ' favorite' : ''}`}>
      <img src={image} alt={hotel.name} />
      <div>
        <div className="card-header">
          <h3>{hotel.name}</h3>
          {isFavorite ? <span className="favorite-badge">Favorite</span> : null}
        </div>
        <p>{hotel.city}</p>
        <p className="price">${hotel.pricePerNight} per night</p>
        {hotel.description ? <p>{hotel.description}</p> : null}
        <div className="card-actions">
          <button type="button" onClick={() => onView(hotel.id)}>
            View details
          </button>
          {onToggleFavorite ? (
            <button
              type="button"
              className={`secondary-button${isFavorite ? ' danger' : ''}`}
              onClick={() => onToggleFavorite(hotel)}
            >
              {isFavorite ? 'Remove favorite' : 'Save to favorites'}
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
};

export default HotelCard;

