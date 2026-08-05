"""Scrape GameDayTweets, classify new tweets via Gemini, persist to news_items.

Idempotent — re-running yields zero new rows until the source page has new
tweets. Run on a schedule (cron / agent loop) every few minutes during
active hours.

Usage:
    python -m scripts.ingest_news               # default 10 pages
    python -m scripts.ingest_news --pages 20    # walk further back
"""

import argparse

from src.ingest.news.rss import ingest_news


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pages", type=int, default=10,
        help="Max pages of GameDayTweets to walk (default: 10)",
    )
    args = parser.parse_args()

    result = ingest_news(max_pages=args.pages)
    print(
        f"pages={result['pages_scraped']} "
        f"scraped={result['scraped']} "
        f"new={result['new']} "
        f"llm={result['llm_classified']} "
        f"fallback={result['fallback_classified']}"
    )


if __name__ == "__main__":
    main()
