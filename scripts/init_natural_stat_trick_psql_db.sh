#!/bin/bash

# Run all Natural Stat Trick scrapers
# Run from the project root directory

SCRAPER="python -m src.ingest.natural_stat_trick.scraper"

$SCRAPER 5v5_individual_counts
$SCRAPER 5v5_individual_rates
$SCRAPER 5v5_on-ice_counts
$SCRAPER 5v5_on-ice_rates
$SCRAPER pp_individual_counts
$SCRAPER all_individual_counts
$SCRAPER all_individual_rates
$SCRAPER all_on-ice_counts
$SCRAPER all_on-ice_rates
