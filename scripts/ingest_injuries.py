"""Ingest injury data from Daily Faceoff.

Scrapes all 32 team pages, LLM-parses any new injury news blurbs,
and upserts structured rows into `player_injuries`.

Usage:
    python -m scripts.ingest_injuries            # scrape + parse new blurbs
    python -m scripts.ingest_injuries --reparse  # re-LLM existing failed rows
"""

import argparse

from src.ingest.news.injuries import ingest_injuries, reparse_failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="Re-run LLM on rows with llm_parsed=False (no scraping)",
    )
    parser.add_argument(
        "--reparse-all",
        action="store_true",
        help="Re-run LLM on ALL rows (schema change, prompt update, etc.)",
    )
    args = parser.parse_args()

    if args.reparse or args.reparse_all:
        result = reparse_failed(reparse_all=args.reparse_all)
        print(
            f"pending={result['pending']} "
            f"parsed={result['parsed']} "
            f"failed={result['failed']}"
        )
        return

    result = ingest_injuries()
    print(
        f"teams={result['teams']} "
        f"injured={result['injured']} "
        f"new={result['new_blurbs']} "
        f"llm_parsed={result['llm_parsed']} "
        f"llm_failed={result['llm_failed']} "
        f"no_blurb={result['no_blurb']} "
        f"skipped={result['skipped_existing']}"
    )


if __name__ == "__main__":
    main()
