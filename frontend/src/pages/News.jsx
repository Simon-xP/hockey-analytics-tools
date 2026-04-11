import { useNavigate } from "react-router-dom";
import Card from "../components/Card";
import { useApi } from "../hooks/useApi";
import { getNewsFeed } from "../api/client";
import "./News.css";

export default function News() {
  const { data, loading } = useApi(() => getNewsFeed(30));
  const navigate = useNavigate();

  const items = data?.items || [];

  return (
    <div className="news-page">
      <h1>News & Updates</h1>
      <p className="page-subtitle">
        Filtered to actionable fantasy news — injuries, transactions, returns, scratches, PP changes.
      </p>

      <Card>
        {loading ? (
          <div className="placeholder-shimmer" style={{ height: 400 }} />
        ) : items.length === 0 ? (
          <p className="empty-state">No news available</p>
        ) : (
          <div className="news-page-list">
            {items.map((item, i) => {
              const player = item.players?.[0];

              return (
                <div
                  key={i}
                  className={`news-page-card ${player?.nhl_id ? "news-clickable" : ""}`}
                  onClick={() => player?.nhl_id && navigate(`/players/${player.nhl_id}`)}
                >
                  <div className="news-page-left">
                    {player?.headshot ? (
                      <img
                        src={player.headshot}
                        alt=""
                        className="news-page-headshot"
                        onError={(e) => {
                          const tag = item.team_tags?.[0];
                          if (tag) {
                            e.target.src = `https://assets.nhle.com/logos/nhl/svg/${tag}_dark.svg`;
                            e.target.className = "news-page-team-logo";
                          } else {
                            e.target.style.display = "none";
                          }
                        }}
                      />
                    ) : item.team_tags?.[0] ? (
                      <img
                        src={`https://assets.nhle.com/logos/nhl/svg/${item.team_tags[0]}_dark.svg`}
                        alt=""
                        className="news-page-team-logo"
                      />
                    ) : (
                      <div className="news-page-headshot-placeholder" />
                    )}
                  </div>
                  <div className="news-page-body">
                    <div className="news-page-top">
                      <span
                        className="news-page-badge"
                        style={{ background: item.category_color + "20", color: item.category_color }}
                      >
                        {item.category_label}
                      </span>
                      {item.source && <span className="news-page-source">{item.source}</span>}
                    </div>
                    <p className="news-page-summary">{item.summary}</p>
                    <p className="news-page-detail">{item.text}</p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
