import { useState } from 'react';
import { DEFAULT_IMAGE_URL } from '../config/constants.js';

const HotelForm = ({ onCreate }) => {
  const [form, setForm] = useState({ name: '', city: '', pricePerNight: '', description: '', imageUrl: DEFAULT_IMAGE_URL });
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleChange = (event) => {
    const { name, value } = event.target;
    setForm((previous) => ({ ...previous, [name]: value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      await onCreate({ ...form, pricePerNight: Number(form.pricePerNight) });
      setForm({ name: '', city: '', pricePerNight: '', description: '', imageUrl: DEFAULT_IMAGE_URL });
    } catch (err) {
      setError(err.message || 'Unable to create hotel');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="hotel-form" onSubmit={handleSubmit}>
      <h3>Register hotel</h3>
      {error ? <p className="form-error">{error}</p> : null}
      <label>
        Name
        <input name="name" value={form.name} onChange={handleChange} required />
      </label>
      <label>
        City
        <input name="city" value={form.city} onChange={handleChange} required />
      </label>
      <label>
        Price per night
        <input name="pricePerNight" type="number" value={form.pricePerNight} onChange={handleChange} required />
      </label>
      <label>
        Description
        <textarea name="description" value={form.description} onChange={handleChange} rows={3} />
      </label>
      <label>
        Image URL (optional)
        <input name="imageUrl" value={form.imageUrl} onChange={handleChange} />
      </label>
      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? 'Saving…' : 'Create'}
      </button>
    </form>
  );
};

export default HotelForm;

