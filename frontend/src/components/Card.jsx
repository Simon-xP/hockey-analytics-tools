import { useNavigate } from "react-router-dom";
import "./Card.css";

export default function Card({ title, linkTo, children, className = "" }) {
  const navigate = useNavigate();

  return (
    <div className={`card ${className}`}>
      {title && (
        <div
          className={`card-header ${linkTo ? "card-header-link" : ""}`}
          onClick={linkTo ? () => navigate(linkTo) : undefined}
        >
          {title}
          {linkTo && <span className="card-header-arrow">&rsaquo;</span>}
        </div>
      )}
      <div className="card-body">{children}</div>
    </div>
  );
}
