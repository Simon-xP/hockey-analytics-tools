import { useNavigate } from "react-router-dom";
import "./Card.css";

export default function Card({ title, linkTo, children, className = "" }) {
  const navigate = useNavigate();

  function handleCardClick(e) {
    if (!linkTo) return;
    // Don't navigate if clicking on an interactive child element
    if (e.target.closest(".clickable-row, .regression-row, .adds-preview-row, .streamable-goalie-tile, a, button")) return;
    navigate(linkTo);
  }

  return (
    <div
      className={`card ${linkTo ? "card-clickable" : ""} ${className}`}
      onClick={handleCardClick}
    >
      {title && (
        <div className="card-header">
          {title}
          {linkTo && <span className="card-header-arrow">&rsaquo;</span>}
        </div>
      )}
      <div className="card-body">{children}</div>
    </div>
  );
}
