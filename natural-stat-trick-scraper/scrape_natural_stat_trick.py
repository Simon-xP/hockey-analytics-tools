import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
import sys
from sqlalchemy import create_engine
# This script scrapes 5v5 stats from Natural Stat Trick for NHL seasons specified in the passed config json file.

def scrape(situation_name):
    with open(f"config_files/natural_stat_trick_config.json", "r") as f:
        params = json.load(f)
        
    situation_dict = params[situation_name]
    all_dfs = []

    for year in range(situation_dict["start_season"], situation_dict["end_season"] + 1):
        season = str(year) + str(year + 1)

        url = 'http://www.naturalstattrick.com/playerteams.php?fromseason={}&thruseason={}&stype={}&sit={}&score={}&stdoi={}&rate={}&team={}&pos={}&loc={}&toi={}&gpfilt={}&fd={}&td={}td&tgp={}&lines={}&draftteam={}'.format(
        season,
        season,
        situation_dict["stype"],
        situation_dict["sit"],
        situation_dict["score"],
        situation_dict["stdoi"],
        situation_dict["rate"],
        situation_dict["team"],
        situation_dict["pos"],
        situation_dict["loc"],
        situation_dict["toi"],
        situation_dict["gpfilt"],
        situation_dict["fd"],
        situation_dict["td"],
        situation_dict["tgp"],
        situation_dict["lines"],
        situation_dict["draftteam"],)
        
        df = pd.read_html(url, header=0, index_col = 0, na_values=["-"])[0]
        df['Season'] = season
        
        all_dfs.append(df)

    final_df = pd.concat(all_dfs, ignore_index=True)
    engine = create_engine("postgresql+psycopg2://postgres@localhost:5432/naturalstattrick")

    final_df.to_sql(
        situation_name,
        engine,
        if_exists="replace",
        index=True
    )

if __name__ == "__main__":
    if len(sys.argv) > 1:
        scrape(sys.argv[1])
    else:
        print("No command-line arguments provided.")

