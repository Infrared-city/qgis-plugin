
from enum import Enum, unique


@unique
class AnalysisType(str, Enum):
    WIND_SPEED = "wind-speed"
    PEDESTRIAN_WIND_COMFORT = "pedestrian-wind-comfort"
    THERMAL_COMFORT_INDEX = "thermal-comfort-index"
    THERMAL_COMFORT_STATISTICS = "thermal-comfort-statistics"
    SOLAR_RADIATION = "solar-radiation"
    DAYLIGHT_AVAILABILITY = "daylight-availability"
    DIRECT_SUN_HOURS = "direct-sun-hours"
    SKY_VIEW_FACTORS = "sky-view-factors"
    #SHADOW_MASK = "shadow-mask"


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

    def __str__(self):
        return self.value

@unique
class ThermalComfortStatisticsType(str, Enum):
    THERMAL_COMFORT = "thermal-comfort"
    HEAT_STRESS = "heat-stress"
    COLD_STRESS = "cold-stress"

    def __str__(self):
        return self.value

@unique
class GeometryTypes(str,Enum):
    BUILDINGS = "buildings"
    TREES = "trees"

    def __str__(self):
        return self.value
