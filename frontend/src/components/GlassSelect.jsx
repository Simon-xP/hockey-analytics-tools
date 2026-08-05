import { useEffect, useRef, useState } from "react";
import "./GlassSelect.css";

export default function GlassSelect({
  value,
  options,
  onChange,
  getLabel,
  getSubLabel,
  minWidth = 340,
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    function onEsc(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, []);

  const current = options.find((o) => o.id === value);
  const label = current ? getLabel(current) : "";
  const sub = current && getSubLabel ? getSubLabel(current) : null;

  return (
    <div
      className={`glass-select ${open ? "open" : ""}`}
      style={{ minWidth }}
      ref={ref}
    >
      <button
        type="button"
        className="glass-select-trigger"
        onClick={() => setOpen(!open)}
      >
        <div className="glass-select-current">
          <span className="glass-select-label">{label}</span>
          {sub && <span className="glass-select-sub">{sub}</span>}
        </div>
        <span className="glass-select-chevron">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M2 4L6 8L10 4"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>
      {open && (
        <div className="glass-select-menu">
          {options.map((opt) => {
            const active = opt.id === value;
            return (
              <button
                key={opt.id}
                type="button"
                className={`glass-select-option ${active ? "active" : ""}`}
                onClick={() => {
                  onChange(opt);
                  setOpen(false);
                }}
              >
                <div className="glass-select-opt-main">
                  <span className="glass-select-opt-label">{getLabel(opt)}</span>
                  {getSubLabel && (
                    <span className="glass-select-opt-sub">{getSubLabel(opt)}</span>
                  )}
                </div>
                {active && <span className="glass-select-opt-check">✓</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
