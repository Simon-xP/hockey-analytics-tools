import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Card from "../components/Card";
import { getNewsFeed, getAllInjuries } from "../api/client";
import "./News.css";

const PAGE_SIZE = 50;

const SEVERITY_LABEL = {
  "season": "OUT FOR SEASON",
  "month-plus": "MONTH+",
  "week-to-week": "WEEK-TO-WEEK",
  "day-to-day": "DAY-TO-DAY",
  "unknown": "STATUS UNKNOWN",
};

const SEVERITY_COLOR = {
  "season": "#ef4444",
  "month-plus": "#f97316",
  "week-to-week": "#eab308",
  "day-to-day": "#7c5cfc",
  "unknown": "#94a3b8",
};

function formatTimeline(row) {
  const lo = row.timeline_days_min;
  const hi = row.timeline_days_max;
  if (!lo && !hi) return null;
  if (lo && hi && lo !== hi) return `${lo}-${hi} days`;
  return `${lo || hi} days`;
}

function SnippetRow({ snippet, onPlayerClick }) {
  const player = snippet.player;
  const teamTag = snippet.team_tag;
  const clickable = !!player?.nhl_id;

  return (
    <div
      className={`snippet-row ${clickable ? "snippet-clickable" : ""}`}
      onClick={(e) => {
        if (!clickable) return;
        e.stopPropagation();
        onPlayerClick(player.nhl_id);
      }}
    >
      <div className="snippet-left">
        {player?.headshot ? (
          <img
            src={player.headshot}
            alt=""
            className="snippet-headshot"
            onError={(e) => {
              if (teamTag) {
                e.target.src = `https://assets.nhle.com/logos/nhl/svg/${teamTag}_dark.svg`;
                e.target.className = "snippet-team-logo";
              } else {
                e.target.style.display = "none";
              }
            }}
          />
        ) : teamTag ? (
          <img
            src={`https://assets.nhle.com/logos/nhl/svg/${teamTag}_dark.svg`}
            alt=""
            className="snippet-team-logo"
          />
        ) : (
          <div className="snippet-headshot-placeholder" />
        )}
      </div>
      <div className="snippet-body">
        <span
          className="snippet-badge"
          style={{
            background: snippet.category_color + "20",
            color: snippet.category_color,
          }}
        >
          {snippet.category_label}
        </span>
        <span className="snippet-summary">{snippet.summary}</span>
      </div>
    </div>
  );
}

function InjuryRow({ injury, onPlayerClick }) {
  const clickable = !!injury.nhl_id;
  const sevColor = SEVERITY_COLOR[injury.severity] || SEVERITY_COLOR.unknown;
  const sevLabel = SEVERITY_LABEL[injury.severity] || "UNKNOWN";
  const timeline = formatTimeline(injury);

  return (
    <div
      className={`snippet-row injury-row ${clickable ? "snippet-clickable" : ""}`}
      onClick={() => clickable && onPlayerClick(injury.nhl_id)}
    >
      <div className="snippet-left">
        {injury.headshot ? (
          <img
            src={injury.headshot}
            alt=""
            className="snippet-headshot"
            onError={(e) => {
              if (injury.team_abbrev) {
                e.target.src = `https://assets.nhle.com/logos/nhl/svg/${injury.team_abbrev}_dark.svg`;
                e.target.className = "snippet-team-logo";
              } else {
                e.target.style.display = "none";
              }
            }}
          />
        ) : injury.team_abbrev ? (
          <img
            src={`https://assets.nhle.com/logos/nhl/svg/${injury.team_abbrev}_dark.svg`}
            alt=""
            className="snippet-team-logo"
          />
        ) : (
          <div className="snippet-headshot-placeholder" />
        )}
      </div>
      <div className="snippet-body injury-body">
        <div className="injury-top">
          <span className="injury-player-name">{injury.player_name}</span>
          {injury.team_abbrev && (
            <span className="injury-team">{injury.team_abbrev}</span>
          )}
          <span
            className="snippet-badge"
            style={{ background: sevColor + "20", color: sevColor }}
          >
            {sevLabel}
          </span>
        </div>
        <div className="injury-meta">
          {injury.body_part && <span className="injury-tag">{injury.body_part}</span>}
          {timeline && <span className="injury-tag">{timeline}</span>}
          {injury.injury_status && (
            <span className="injury-tag">{injury.injury_status.toUpperCase()}</span>
          )}
        </div>
        {injury.news_details && (
          <p className="injury-blurb">{injury.news_details}</p>
        )}
      </div>
    </div>
  );
}

function NewsTab({ navigate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getNewsFeed(PAGE_SIZE, 0).then((data) => {
      if (cancelled) return;
      const next = data?.items || [];
      setItems(next);
      setHasMore(next.length >= PAGE_SIZE);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  function loadMore() {
    if (loading || !hasMore) return;
    setLoading(true);
    getNewsFeed(PAGE_SIZE, items.length).then((data) => {
      const next = data?.items || [];
      setItems((prev) => [...prev, ...next]);
      setHasMore(next.length >= PAGE_SIZE);
      setLoading(false);
    });
  }

  if (loading && items.length === 0) {
    return <div className="placeholder-shimmer" style={{ height: 400 }} />;
  }
  if (items.length === 0) {
    return <p className="empty-state">No news available</p>;
  }

  return (
    <>
      <div className="news-page-list">
        {items.map((item, i) => (
          <div key={i} className="news-tweet-card">
            <div className="news-snippets">
              {(item.snippets || []).map((snip, j) => (
                <SnippetRow
                  key={j}
                  snippet={snip}
                  onPlayerClick={(id) => navigate(`/players/${id}`)}
                />
              ))}
            </div>
            <div className="news-source-tweet">
              {item.source && <span className="news-source-handle">{item.source}</span>}
              <p className="news-source-text">{item.text}</p>
            </div>
          </div>
        ))}
      </div>
      {hasMore && (
        <button className="news-load-more" onClick={loadMore} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </>
  );
}

function InjuriesTab({ navigate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getAllInjuries().then((data) => {
      if (cancelled) return;
      setItems(data?.items || []);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="placeholder-shimmer" style={{ height: 400 }} />;
  }
  if (items.length === 0) {
    return <p className="empty-state">No injuries on file</p>;
  }

  return (
    <div className="news-page-list">
      {items.map((inj, i) => (
        <InjuryRow
          key={`${inj.nhl_id || inj.player_name}-${i}`}
          injury={inj}
          onPlayerClick={(id) => navigate(`/players/${id}`)}
        />
      ))}
    </div>
  );
}

export default function News() {
  const [tab, setTab] = useState("news");
  const navigate = useNavigate();

  return (
    <div className="news-page">
      <h1>News & Updates</h1>
      <div className="news-tabs">
        <button
          className={`news-tab ${tab === "news" ? "active" : ""}`}
          onClick={() => setTab("news")}
        >
          News feed
        </button>
        <button
          className={`news-tab ${tab === "injuries" ? "active" : ""}`}
          onClick={() => setTab("injuries")}
        >
          Injuries
        </button>
      </div>
      <p className="page-subtitle">
        {tab === "news"
          ? "Actionable tweets — injuries, transactions, returns, scratches, PP changes."
          : "Structured injury report from Daily Faceoff, parsed for body part, severity, and timeline."}
      </p>

      <Card>
        {tab === "news" ? (
          <NewsTab navigate={navigate} />
        ) : (
          <InjuriesTab navigate={navigate} />
        )}
      </Card>
    </div>
  );
}
