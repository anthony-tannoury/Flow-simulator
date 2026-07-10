from datetime import datetime
import json

from simulation.judgement_day import *


def minutes_between(date_str1: str, date_str2: str) -> int:
    format_str = "%d-%m-%Y %H:%M"
    dt1 = datetime.strptime(date_str1, format_str)
    dt2 = datetime.strptime(date_str2, format_str)
    delta = dt2 - dt1
    return int(delta.total_seconds() // 60)


class Parser:
    def __init__(self, filename: str):
        with open(filename, 'r') as file:
            self.data = json.load(file)
        
        self.start_date = self.data['start_date']  # dd-mm-yyyy hh:mm

    def to_minutes(self, date: str) -> int:
        return minutes_between(self.start_date, date)
    
    