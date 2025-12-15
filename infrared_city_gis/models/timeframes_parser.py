from enum import Enum
from typing import TypedDict, Literal, Union
import re

class SeasonalTimeFrameConfig(str, Enum):
    FullYear = "full-year"
    Winter = "winter"
    Spring = "spring"
    Summer = "summer"
    Autumn = "autumn"

class MonthConfig(str, Enum):
    January = "January"
    February = "February"
    March = "March"
    April = "April"
    May = "May"
    June = "June"
    July = "July"
    August = "August"
    September = "September"
    October = "October"
    November = "November"
    December = "December"

    @property
    def number(self) -> int:
        mapping = {
            MonthConfig.January: 1,
            MonthConfig.February: 2,
            MonthConfig.March: 3,
            MonthConfig.April: 4,
            MonthConfig.May: 5,
            MonthConfig.June: 6,
            MonthConfig.July: 7,
            MonthConfig.August: 8,
            MonthConfig.September: 9,
            MonthConfig.October: 10,
            MonthConfig.November: 11,
            MonthConfig.December: 12,
        }
        return mapping[self]

class DailyTimeFrameConfig(str, Enum):
    FullDay = "all-hours"
    Morning = "morning"
    Noon = "noon"
    Afternoon = "afternoon"
    Evening = "evening"

class DailyTimeFrameConfigUTCI(str, Enum):
    Morning = "morning"
    Noon = "noon"
    Afternoon = "afternoon"
    Evening = "evening"


Season = Literal["winter", "spring", "summer", "autumn"]


class SeasonLimit(TypedDict):
    starts: int
    ends: int


class HemisphereLimits(TypedDict):
    winter: SeasonLimit
    spring: SeasonLimit
    summer: SeasonLimit
    autumn: SeasonLimit


SEASON_LIMITS: dict[Literal["north", "south"], HemisphereLimits] = {
    "north": {
        "winter": {"starts": 12, "ends": 3},
        "spring": {"starts": 3, "ends": 6},
        "summer": {"starts": 6, "ends": 9},
        "autumn": {"starts": 9, "ends": 12},
    },
    "south": {
        "winter": {"starts": 6, "ends": 9},
        "spring": {"starts": 9, "ends": 12},
        "summer": {"starts": 12, "ends": 3},
        "autumn": {"starts": 3, "ends": 6},
    },
}

HourBase = Literal["morning", "noon", "afternoon", "evening"]

class HourLimit(TypedDict):
    startTime: int
    endTime: int

HOURS_LIMIT: dict[HourBase, HourLimit] = {
    "morning": {"startTime": 6, "endTime": 10},
    "noon": {"startTime": 10, "endTime": 14},
    "afternoon": {"startTime": 14, "endTime": 18},
    "evening": {"startTime": 18, "endTime": 22},
}


def validate_weather_filename(name: str) -> bool:
    pattern = re.compile(r'^[A-Z]{3}_[A-Z]{2}_.+\.\d{6}_TMYx\.2009-2023$')
    return bool(pattern.match(name))