from enum import Enum, unique, auto

# NOTE:
# Only weather files whose string values match the TMYx filename pattern
#   ^[A-Z]{3}_[A-Z]{2}_.+\.\d{6}_TMYx\.2009-2023$
# are kept active in this enum. Entries that do not follow this pattern
# (for example IWEC or other formats) are commented out below so they
# are not offered in the QGIS dropdown, but can be re‑enabled later if needed.

class WeatherFile(str, Enum):
    # ARE_Abu_Dhabi_412170_IWEC = "ARE_Abu.Dhabi.412170_IWEC.2009-2023"
    ESP_CT_Barcelona_081800_TMYx = "ESP_CT_Barcelona.081800_TMYx.2009-2023"
    # CHN_Beijing_Beijing_545110_IWEC = "CHN_Beijing.Beijing.545110_IWEC.2009-2023"
    DNK_HS_Copenhagen_Kastrup_AP_061800_TMYx = "DNK_HS_Copenhagen-Kastrup.AP.061800_TMYx.2009-2023"
    QAT_DA_Doha_404280_TMYx = "QAT_DA_Doha.404280_TMYx.2009-2023"
    ARE_DU_Dubai_Intl_AP_411940_TMYx = "ARE_DU_Dubai.Intl.AP.411940_TMYx.2009-2023"
    # DEU_Frankfurt_am_Main_106370_IWEC = "DEU_Frankfurt.am.Main.106370_IWEC.2009-2023"
    # CHE_Geneva_067000_IWEC = "CHE_Geneva.067000_IWEC.2009-2023"
    DEU_HH_Hamburg_Schmidt_AP_101470_TMYx = "DEU_HH_Hamburg-Schmidt.AP.101470_TMYx.2009-2023"
    # CHN_Hong_Kong_SAR_450070_CityUHK = "CHN_Hong.Kong.SAR.450070_CityUHK.2009-2023"
    KWT_KU_Kuwait_City_405810_TMYx = "KWT_KU_Kuwait.City.405810_TMYx.2009-2023"
    GBR_ENG_London_Wea_Ctr_St_James_Park_037700_TMYx = "GBR_ENG_London.Wea.Ctr-St.James.Park.037700_TMYx.2009-2023"
    DEU_BY_Munich_Theresienwiese_108650_TMYx = "DEU_BY_Munich-Theresienwiese.108650_TMYx.2009-2023"
    NOR_OS_Oslo_Blindern_014920_TMYx = "NOR_OS_Oslo.Blindern.014920_TMYx.2009-2023"
    # SAU_Riyadh_404380_IWEC = "SAU_Riyadh.404380_IWEC.2009-2023"
    CHN_GD_Shenzhen_594930_TMYx = "CHN_GD_Shenzhen.594930_TMYx.2009-2023"
    # SGP_Singapore_486980_IWEC = "SGP_Singapore.486980_IWEC.2009-2023"
    SWE_ST_Stockholm_024850_TMYx = "SWE_ST_Stockholm.024850_TMYx.2009-2023"
    AUS_NSW_Sydney_Obs_Observatory_Hill_947680_TMYx = "AUS_NSW_Sydney.Obs-Observatory.Hill.947680_TMYx.2009-2023"
    AUT_WI_Wien_Innere_Stadt_110340_TMYx = "AUT_WI_Wien-Innere.Stadt.110340_TMYx.2009-2023"