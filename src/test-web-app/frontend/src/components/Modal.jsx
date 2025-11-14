const Modal = ({ open, title, onClose, children }) => {
  if (!open) {
    return null;
  }
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header className="modal-header">
          <h3>{title}</h3>
          <button type="button" className="modal-close" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="modal-content">{children}</div>
      </div>
    </div>
  );
};

export default Modal;

