import { useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import source from "../../../docs/how-i-made-this.md?raw";
import "./BuildLog.css";

/**
 * Renders docs/how-i-made-this.md as a page so the markdown stays the single
 * source of truth. Editing the doc updates this page with no code changes.
 */

function slugify(text) {
  return String(text)
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/** Pull the h2 headings out of the raw markdown to build the contents rail. */
function useOutline(markdown) {
  return useMemo(
    () =>
      markdown
        .split("\n")
        .filter((line) => line.startsWith("## "))
        .map((line) => {
          const label = line.slice(3).trim();
          return { label, id: slugify(label) };
        }),
    [markdown],
  );
}

/** Flatten a heading's children back to a string so it can be slugified. */
function headingText(children) {
  const walk = (node) => {
    if (node === null || node === undefined || node === false) return "";
    if (typeof node === "string" || typeof node === "number") return String(node);
    if (Array.isArray(node)) return node.map(walk).join("");
    if (node.props) return walk(node.props.children);
    return "";
  };
  return walk(children);
}

const components = {
  h1: ({ children }) => <h1 className="bl-title">{children}</h1>,
  h2: ({ children }) => (
    <h2 className="bl-h2" id={slugify(headingText(children))}>
      {children}
    </h2>
  ),
  h3: ({ children }) => <h3 className="bl-h3">{children}</h3>,
  blockquote: ({ children }) => <blockquote className="bl-note">{children}</blockquote>,
  table: ({ children }) => (
    <div className="bl-table-wrap">
      <table className="bl-table">{children}</table>
    </div>
  ),
  hr: () => <hr className="bl-rule" />,
  a: ({ href, children }) => (
    <a href={href} className="bl-link" target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

export default function BuildLog() {
  const outline = useOutline(source);
  const [active, setActive] = useState("");
  const bodyRef = useRef(null);

  useEffect(() => {
    const headings = Array.from(bodyRef.current?.querySelectorAll("h2[id]") ?? []);
    if (!headings.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length) setActive(visible[0].target.id);
      },
      { rootMargin: "-72px 0px -70% 0px", threshold: 0 },
    );

    headings.forEach((h) => observer.observe(h));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="build-log">
      <div className="bl-shell">
        <aside className="bl-rail">
          <p className="bl-rail-title">Contents</p>
          <nav className="bl-rail-nav">
            {outline.map((item, i) => (
              <a
                key={item.id}
                href={`#${item.id}`}
                className={`bl-rail-link ${active === item.id ? "is-active" : ""}`}
              >
                <span className="bl-rail-num">{String(i + 1).padStart(2, "0")}</span>
                <span>{item.label}</span>
              </a>
            ))}
          </nav>
        </aside>

        <article className="bl-body" ref={bodyRef}>
          <p className="bl-eyebrow">Engineering log</p>
          <Markdown remarkPlugins={[remarkGfm]} components={components}>
            {source}
          </Markdown>
          <footer className="bl-footer">
            <span>Source: docs/how-i-made-this.md</span>
            <span>Edit the doc, this page follows</span>
          </footer>
        </article>
      </div>
    </div>
  );
}
