import os
import pandas as pd

schedule_df = pd.read_csv("schedule-data/nhl-schedule-raw.csv")
print(schedule_df.head().to_string())