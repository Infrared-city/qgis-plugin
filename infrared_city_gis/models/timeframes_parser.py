from enum import Enum
from typing import TypedDict, Literal, Union
from datetime import datetime
from .analysis import AnalysisType

class TimePoint(TypedDict):
    month: int
    hour: int


class TimeFrame(TypedDict):
    startTime: TimePoint
    endTime: TimePoint


class SeasonalTimeFrameConfig(str, Enum):
    FullYear = "full-year"
    Winter = "winter"
    Spring = "spring"
    Summer = "summer"
    Autumn = "autumn"


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

def makeTimeFrameObj(
    isNorthHem: bool,
    season: Union[SeasonalTimeFrameConfig, str],
    hourly: Union[DailyTimeFrameConfig, str],
    analysis_type: AnalysisType = None
) -> TimeFrame:
    season = SeasonalTimeFrameConfig(season)
    hourly = DailyTimeFrameConfig(hourly)

    hemisphere = "north" if isNorthHem else "south"
    startMonth = 0
    endMonth = 0
    startHour = 0
    endHour = 0

    if season == SeasonalTimeFrameConfig.FullYear:
        startMonth = 1
        endMonth = 13
    else:
        s = season.value 
        startMonth = SEASON_LIMITS[hemisphere][s]["starts"]
        endMonth = SEASON_LIMITS[hemisphere][s]["ends"]

    if hourly == DailyTimeFrameConfig.FullDay:
        startHour = 1
        endHour = 25
    else:
        h = hourly.value  
        limits = HOURS_LIMIT[h]
        startHour = limits["startTime"]
        endHour = limits["endTime"]

    return TimeFrame(
        startTime=TimePoint(month=startMonth, hour=startHour),
        endTime=TimePoint(month=endMonth, hour=endHour),
    )

def makeTimeFrameObjWithMonth(
    month: int,
    hourly: Union[DailyTimeFrameConfig, str]
) -> TimeFrame:
    """Create a TimeFrame from a concrete month (1-12) and a daily timeframe.

    Month logic: startMonth = month, endMonth = month + 1.
    Hourly logic is identical to makeTimeFrameObj.
    """
    hourly = DailyTimeFrameConfig(hourly)

    startMonth = month
    # month + 1
    endMonth = month + 1 if month < 12 else 1

    startHour = 0
    endHour = 0

    if hourly == DailyTimeFrameConfig.FullDay:
        startHour = 1
        endHour = 25
    else:
        h = hourly.value
        limits = HOURS_LIMIT[h]
        startHour = limits["startTime"]
        endHour = limits["endTime"]

    return TimeFrame(
        startTime=TimePoint(month=startMonth, hour=startHour),
        endTime=TimePoint(month=endMonth, hour=endHour),
    )

def adjustDatetime(datetime_str: str):
    """Adjust datetime string to include month, day, and minute stamps."""
    dt_obj = datetime.fromisoformat(datetime_str)

    month_stamp = [dt_obj.month, dt_obj.month + 1]
    day_stamp = [dt_obj.day, dt_obj.day + 1]
    hour_stamp = [dt_obj.hour, dt_obj.hour + 1]
    minute_stamp = [dt_obj.minute, dt_obj.minute + 1]

    return {
        "month-stamp": month_stamp,
        "day-stamp": day_stamp,
        "hour-stamp": hour_stamp,
        "minute-stamp": minute_stamp,
    }

