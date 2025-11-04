
from enum import Enum, unique, auto

@unique
class AnalysisType(str, Enum):
    WIND_SPEED = "wind-speed"
    PEDESTRIAN_WIND_COMFORT = "pedestrian-wind-comfort"


    def __str__(self):
        return self.value

@unique
class PedestrianWindComfortType(str, Enum):
    LAWSON_1970 = "lawson-1970"
    LAWSON_2001 = "lawson-2001"
    LAWSON_LDDC = "lawson-lddc"
    DAVENPORT = "davenport"
    NEN_8100_COMFORT = "nen-8100-comfort"
    NEN_8100_SAFETY = "nen-8100-safety"
    VDI_3787 = "vdi-3787"
    
    def __str__(self):
        return self.value

@unique
class Seasons(str, Enum):
    SUMMER = "summer"
    WINTER = "winter"
    FALL = "fall"
    SPRING = "spring"
    ALLYEAR = "all-year"
    
