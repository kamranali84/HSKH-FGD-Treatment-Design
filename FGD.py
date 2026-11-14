"""
HSKH FGD Treatment Design  -  single-file Streamlit app   (REVISED FLOWSHEET)
=============================================================================
Install:   pip install streamlit pandas numpy reportlab openpyxl
Run:       streamlit run hskh_fgd_treatment_design.py

Projects are saved as JSON files in a "projects" folder created next to this file.

Revised train (per key process diagram):
 Outdoor sump -> EQ tanks A/B -> feed pump skid -> Reaction tanks A>B>C (overflow; lime, FeCl3,
 polymer, organosulfide in each) -> splitter -> Clarifiers A/B -> Clearwell (HCl) -> sand filter
 feed pumps -> Sand filters -> Bio feed tank (nutrient) -> bio feed pumps -> strainer -> heat
 exchanger -> Stage 1 flow-control skid -> Stage 1 bioreactors -> Stage 2 bioreactors ->
 Stage 2 flow-control skid -> UF feed sump -> sump pumps -> UF feed pumps -> UF skids ->
 Effluent/backwash tank -> pump skid -> UV -> NPDES.  Sludge: clarifier underflow -> sludge pumps ->
 sludge tanks A/B -> press feed pumps -> vacuum filter presses A/B/C -> cake / filtrate to EQ.
"""
import hashlib
import inspect
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from reportlab.graphics.shapes import Drawing, Rect, Line, Polygon, String, Circle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
                                PageBreak, Table, TableStyle, KeepTogether, CondPageBreak)
from reportlab.platypus.tableofcontents import TableOfContents

st.set_page_config(page_title="HSKH FGD Treatment Design", page_icon="💧", layout="wide")

PAGES = {}   # st.Page objects, filled in main()


# ======================================================================
# DEFAULTS
# ======================================================================
# Parameters that are carried (and treated) through the train
TRAIN_PARAMS = [
    "tss", "hardness", "alkalinity", "iron", "aluminum", "copper", "zinc",
    "nickel", "lead", "cadmium", "arsenic", "selenium", "mercury",
    "nitrate", "nitrite", "ammonia", "bod", "cod",
]

# Parameters shown in the effluent table but not treated by this train
UNTREATED_PARAMS = [
    "ph", "temperature", "turbidity", "sdi", "tds", "conductivity",
    "calcium", "magnesium", "sulfate", "chloride", "silica", "oil_grease",
]

PARAM_INFO = {
    "tss": ("TSS", "mg/L"),
    "hardness": ("Hardness (as CaCO3)", "mg/L"),
    "alkalinity": ("Alkalinity (as CaCO3)", "mg/L"),
    "iron": ("Iron", "mg/L"),
    "aluminum": ("Aluminum", "mg/L"),
    "copper": ("Copper", "mg/L"),
    "zinc": ("Zinc", "mg/L"),
    "nickel": ("Nickel", "mg/L"),
    "lead": ("Lead", "mg/L"),
    "cadmium": ("Cadmium", "mg/L"),
    "arsenic": ("Arsenic", "mg/L"),
    "selenium": ("Selenium", "mg/L"),
    "mercury": ("Mercury", "mg/L"),
    "nitrate": ("Nitrate-N", "mg/L"),
    "nitrite": ("Nitrite-N", "mg/L"),
    "ammonia": ("Ammonia-N", "mg/L"),
    "bod": ("BOD", "mg/L"),
    "cod": ("COD", "mg/L"),
    "ph": ("pH", "-"),
    "temperature": ("Temperature", "°C"),
    "turbidity": ("Turbidity", "NTU"),
    "sdi": ("SDI", "-"),
    "tds": ("TDS", "mg/L"),
    "conductivity": ("Conductivity", "µS/cm"),
    "calcium": ("Calcium", "mg/L"),
    "magnesium": ("Magnesium", "mg/L"),
    "sulfate": ("Sulfate", "mg/L"),
    "chloride": ("Chloride", "mg/L"),
    "silica": ("Silica", "mg/L"),
    "oil_grease": ("Oil & Grease", "mg/L"),
}

STAGES = [
    ("inf", "Influent"),
    ("rt", "Reaction Tanks A-C"),
    ("cl", "Clarifier"),
    ("cw", "Clearwell"),
    ("sf", "Sand Filter"),
    ("bio", "Bioreactor"),
    ("uf", "UF"),
]

# (parameter, limit key)
COMPLIANCE = [
    ("tss", "limit_tss"), ("arsenic", "limit_arsenic"),
    ("selenium", "limit_selenium"), ("mercury", "limit_mercury"),
    ("nitrate", "limit_nitrate"), ("ammonia", "limit_ammonia"),
    ("bod", "limit_bod"), ("cod", "limit_cod"),
]

# Chemicals: specific gravity of the delivered / make-up solution, tank material
CHEMICALS = {
    "lime": dict(name="Lime (Ca(OH)2 slurry)", dose="lime_dose", sg=1.07,
                 material="FRP / HDPE (agitated)", mixed=True),
    "naoh": dict(name="NaOH", dose="naoh_dose", sg=1.52,
                 material="FRP / HDPE", mixed=False),
    "fecl3": dict(name="FeCl3 (coagulant)", dose="fecl3_dose", sg=1.42,
                  material="FRP / rubber-lined steel", mixed=False),
    "polymer": dict(name="Polymer (make-up solution)", dose="polymer_dose", sg=1.00,
                    material="SS316 / HDPE (agitated)", mixed=True),
    "organosulfide": dict(name="Organosulfide (TMT)", dose="organosulfide_dose", sg=1.10,
                          material="HDPE", mixed=False),
    "hcl": dict(name="HCl (clearwell pH adjustment)", dose="hcl_dose", sg=1.16,
                material="FRP / HDPE (vented to scrubber)", mixed=False),
    "methanol": dict(name="Methanol", dose="methanol_dose", sg=0.79,
                     material="Carbon steel (flameproof, N2 blanketed)", mixed=False),
    "nutrient": dict(name="Nutrient (H3PO4)", dose="nutrient_dose", sg=1.58,
                     material="HDPE", mixed=False),
}

DEFAULTS = {
    # ---------------- influent ----------------
    "flow": 5000.0, "ph": 7.0, "temperature": 25.0, "tss": 100.0,
    "turbidity": 50.0, "sdi": 5.0, "tds": 5000.0, "conductivity": 8000.0,
    "calcium": 200.0, "magnesium": 100.0, "hardness": 500.0, "alkalinity": 250.0,
    "iron": 5.0, "aluminum": 2.0, "copper": 0.1, "zinc": 0.5, "nickel": 0.3,
    "lead": 0.05, "cadmium": 0.02, "mercury": 0.005, "arsenic": 0.1, "selenium": 1.0,
    "nitrate": 20.0, "nitrite": 0.0, "ammonia": 5.0,
    "sulfate": 3000.0, "chloride": 1000.0, "silica": 20.0,
    "cod": 100.0, "bod": 50.0, "oil_grease": 10.0,
    "recovery_requirement": "Compliance", "final_water_use": "Discharge",

    # ---------------- global design basis ----------------
    "pump_eff": 0.65, "pump_margin_pct": 10.0, "tank_freeboard_pct": 15.0,

    # ---------------- equalization / influent pumping / outdoor sump ----------------
    "eq_hrt_hr": 4.0, "eq_mixer_w_m3": 6.0, "eq_feed_tdh": 25.0,
    "eq_feed_duty": 2.0, "eq_feed_standby": 1.0, "eq_tanks": 2.0,
    "eq_sump_frac": 10.0, "eq_sump_min": 15.0, "eq_sump_tdh": 15.0,

    # ---------------- reaction tanks A / B / C ----------------
    "lime_dose": 500.0, "naoh_dose": 0.0, "fecl3_dose": 100.0, "polymer_dose": 5.0,
    "rt_react_min": 20.0, "rt_react_g": 100.0,
    "rt_rapid_min": 20.0, "rt_rapid_g": 100.0,
    "rt_floc_min": 20.0, "rt_floc_g": 60.0,
    "lime_conc": 10.0, "lime_days": 3.0,
    "naoh_conc": 50.0, "naoh_days": 14.0,
    "fecl3_conc": 40.0, "fecl3_days": 14.0,
    "polymer_conc": 0.25, "polymer_days": 1.0,
    "organosulfide_conc": 15.0, "organosulfide_days": 14.0,
    "methanol_conc": 100.0, "methanol_days": 14.0,
    "nutrient_conc": 75.0, "nutrient_days": 30.0,

    # ---------------- clarifier ----------------
    "clarifier_sor": 2.0, "clarifier_swd": 3.5, "clarifier_no": 2.0,
    "clarifier_freeboard": 0.5,
    "cl_tss_removal": 90.0, "cl_arsenic_removal": 20.0, "cl_selenium_removal": 10.0,
    "cl_mercury_removal": 10.0, "cl_lead_removal": 10.0, "cl_cadmium_removal": 10.0,
    "cl_sludge_tdh": 20.0, "cl_sludge_run_hr": 12.0, "cl_sludge_standby": 1.0,
    "cl_sludge_duty": 2.0,

    # ---------------- clearwell (HCl) ----------------
    "hcl_dose": 150.0, "hcl_conc": 32.0, "hcl_days": 14.0, "hcl_cost": 0.20,
    "cw_min": 15.0, "cw_mixer_w_m3": 6.0,

    # ---------------- sand filter ----------------
    "sf_filtration_rate": 12.0, "sf_num_filters": 4.0, "sf_bed_depth": 1.2,
    "sf_backwash_rate": 40.0,
    "sf_tss_removal": 80.0, "sf_arsenic_removal": 10.0, "sf_selenium_removal": 5.0,
    "sf_mercury_removal": 5.0, "sf_lead_removal": 5.0, "sf_cadmium_removal": 5.0,
    "sf_air_rate": 55.0, "sf_air_dp_kpa": 50.0, "sf_bw_min": 10.0,
    "sf_sump_min": 10.0, "sf_feed_tdh": 20.0, "sf_feed_duty": 3.0,
    "sf_feed_standby": 1.0, "sf_bw_tdh": 15.0, "sf_bw_return_hr": 4.0,
    "sf_bw_return_tdh": 15.0,

    # ---------------- organosulfide (dosed into reaction tanks) ----------------
    "organosulfide_dose": 25.0, "organosulfide_reaction_time": 20.0,
    "organosulfide_mix_energy": 50.0,
    "os_hg_removal": 98.0, "os_cd_removal": 95.0, "os_pb_removal": 95.0,
    "os_as_removal": 20.0, "os_se_removal": 10.0,

    # ---------------- bioreactor ----------------
    "bio_stage1_hrt": 3.0, "bio_stage2_hrt": 6.0,
    "bio_stage1_reactors": 7.0, "bio_stage2_reactors": 20.0,
    "bio_nitrate_removal": 95.0, "bio_selenium_removal": 90.0,
    "bio_ammonia_removal": 85.0, "bio_bod_removal": 95.0, "bio_cod_removal": 80.0,
    "methanol_dose": 100.0, "nutrient_dose": 1.0, "bio_design_se_loading": 50.0,
    "bio_feed_tdh": 25.0, "bio_feed_duty": 3.0, "bio_feed_standby": 1.0,
    "bio_recirc_ratio": 0.5, "bio_recirc_tdh": 8.0,
    "bio_depth": 4.0, "bio_bw_rate": 15.0, "bio_bw_min": 10.0, "bio_bw_tdh": 15.0,
    "bio_feed_tank_min": 15.0, "bio_feed_tank_w_m3": 6.0, "bio_strainer_um": 500.0,
    "bio_hx_target_c": 25.0, "nutrient_pump_duty": 2.0,

    # ---------------- UF ----------------
    "uf_flux": 50.0, "uf_recovery": 95.0, "uf_module_area": 80.0, "uf_train_count": 6.0,
    "uf_tss_removal": 99.0, "uf_arsenic_removal": 20.0, "uf_selenium_removal": 20.0,
    "uf_mercury_removal": 20.0, "uf_bod_removal": 0.0, "uf_cod_removal": 0.0,
    "uf_bw_flux_ratio": 2.5, "uf_feed_tank_min": 10.0, "uf_tank_min": 15.0,
    "uf_feed_tdh": 30.0, "uf_feed_standby": 1.0, "uf_bw_tdh": 25.0,
    "uf_cip_vol_per_module": 0.25, "uf_cip_flow_per_module": 3.0,
    "uf_ceb_tank_m3": 2.0, "uf_ceb_pump_lph": 300.0, "uf_sump_tdh": 10.0,

    # ---------------- final effluent ----------------
    "eff_tank_min": 15.0, "eff_pump_tdh": 20.0, "eff_pump_duty": 2.0,
    "eff_pump_standby": 1.0, "uv_kwh_m3": 0.03,

    # ---------------- sludge ----------------
    "sludge_solids_percent": 5.0, "cake_solids_pct": 35.0, "sludge_hold_days": 1.0,
    "sludge_press_hr": 16.0, "sludge_feed_tdh": 40.0, "sludge_filtrate_tdh": 15.0,
    "sludge_tanks": 2.0, "sludge_presses": 3.0,

    # ---------------- chemical unit cost ($/kg) ----------------
    "lime_cost": 0.15, "naoh_cost": 0.50, "fecl3_cost": 0.40, "polymer_cost": 3.00,
    "organosulfide_cost": 4.50, "methanol_cost": 0.45, "nutrient_cost": 1.20,

    # ---------------- compliance ----------------
    "limit_tss": 10.0, "limit_arsenic": 0.01, "limit_selenium": 0.005,
    "limit_mercury": 0.00005, "limit_nitrate": 1.0, "limit_ammonia": 1.0,
    "limit_bod": 10.0, "limit_cod": 30.0,

    # ---------------- CAPEX / OPEX ----------------
    "reaction_tank_cost": 400000.0, "clarifier_cost": 500000.0,
    "sand_filter_cost": 250000.0, "organosulfide_cost_capex": 150000.0,
    "bioreactor_cost": 2500000.0, "uf_cost": 1200000.0, "sludge_system_cost": 350000.0,
    "pump_cost_per_kw": 1200.0, "tank_cost_per_m3": 600.0,
    "install_pct": 20.0, "eng_pct": 10.0, "cont_pct": 15.0,
    "power_basis": "Calculated from equipment list", "electricity_cost": 250000.0,
    "power_tariff": 0.08,
    "sludge_basis": "Calculated ($/tonne cake)", "sludge_disposal_cost": 100000.0,
    "disposal_per_t": 50.0,
    "labor_cost": 300000.0, "maintenance_percent": 3.0,
    "discount_rate": 8.0, "life_years": 20.0,
}


# ======================================================================
# FMT
# ======================================================================
def fmt_num(v):
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if v != v:
            return ""
        a = abs(v)
        if a == 0:
            return "0"
        if a >= 1000:
            return f"{v:,.0f}"
        if a >= 100:
            return f"{v:.1f}"
        if a >= 1:
            return f"{v:.2f}"
        if a >= 0.01:
            return f"{v:.4f}"
        return f"{v:.6f}"
    return "" if v is None else str(v)


def fmt_df(df):
    return df.apply(lambda col: col.map(fmt_num)) if len(df) else df


# ======================================================================
# CALC
# ======================================================================
GPM_TO_M3H = 0.2271      # same factor as the original app
MU_WATER = 0.00089       # Pa.s, dynamic viscosity of water (25 C)


# ------------------------------------------------------------------ helpers
def gf(d, key):
    """Float input with fallback to the default (handles NaN / junk)."""
    try:
        v = float(d.get(key, DEFAULTS[key]))
        if v != v:
            raise ValueError
        return v
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def gi(d, key):
    return max(int(round(gf(d, key))), 0)


def gs(d, key):
    return str(d.get(key, DEFAULTS.get(key, "")))


def apply_removal(feed, removal_pct):
    return feed * (1 - removal_pct / 100.0)


def influent(d):
    return {p: gf(d, p) for p in TRAIN_PARAMS}


# ------------------------------------------------------------------ reaction tank removal curves
_FE = [0, 25, 50, 75, 100, 125, 150]
_TSS = [0, 40, 55, 65, 70, 78, 85]
_AS = [0, 40, 60, 70, 80, 88, 92]
_SE = [0, 10, 15, 22, 30, 38, 45]
_PB = [0, 35, 55, 65, 75, 83, 90]
_CD = [0, 40, 60, 70, 80, 88, 92]
_LIME = [0, 100, 200, 300, 400, 500, 600, 700]
_IRON = [0, 50, 70, 80, 90, 95, 97, 98]
_HARD = [0, 10, 20, 30, 40, 50, 60, 70]
_HG_X = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130]
_HG_Y = [0, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 100]
_POLY = [0, 1, 3, 5, 7, 10]
_POLY_BOOST = [0, 2, 5, 8, 10, 12]


def rt_removals(d):
    fe, lime, poly = gf(d, "fecl3_dose"), gf(d, "lime_dose"), gf(d, "polymer_dose")
    ip = np.interp
    r = {
        "tss": min(95.0, float(ip(fe, _FE, _TSS)) + float(ip(poly, _POLY, _POLY_BOOST))),
        "arsenic": float(ip(fe, _FE, _AS)),
        "selenium": float(ip(fe, _FE, _SE)),
        "lead": float(ip(fe, _FE, _PB)),
        "cadmium": float(ip(fe, _FE, _CD)),
        "mercury": float(ip(fe, _HG_X, _HG_Y)),
        "iron": float(ip(lime, _LIME, _IRON)),
        "hardness": float(ip(lime, _LIME, _HARD)),
        "aluminum": 95.0, "copper": 80.0, "zinc": 80.0, "nickel": 60.0,
        "alkalinity": 20.0,
    }
    return r


# ------------------------------------------------------------------ bioreactor performance (original factor model)
def bio_perf(d, feed):
    q_m3d = gf(d, "flow") * GPM_TO_M3H * 24
    total_hrt = gf(d, "bio_stage1_hrt") + gf(d, "bio_stage2_hrt")
    methanol = gf(d, "methanol_dose")
    carbon = 0.20 + 0.80 * min(max(methanol / 1000.0, 0.0), 1.0)
    base = gf(d, "bio_selenium_removal")
    hrt_f = min(1 - math.exp(-total_hrt / 4), 1.0)
    se_load = feed["selenium"] * q_m3d / 1000.0
    load_f = min(gf(d, "bio_design_se_loading") / max(se_load, 0.001), 1.0)
    no3 = feed["nitrate"]
    nitrate_f = 1.0 if no3 <= 10 else 0.9 if no3 <= 50 else 0.8 if no3 <= 100 else 0.7
    t = max(gf(d, "temperature"), gf(d, "bio_hx_target_c"))   # heat exchanger sets bioreactor temperature
    temp_f = 1.0 if t >= 20 else 0.9 if t >= 15 else 0.75 if t >= 10 else 0.6
    actual = min(max(base * hrt_f * load_f * nitrate_f * temp_f * carbon, 0.0), base)
    no3_base = gf(d, "bio_nitrate_removal")
    no3_act = min(no3_base * carbon * hrt_f * temp_f, no3_base)
    required_meoh = max(feed["nitrate"] * 2.5 + feed["selenium"] * 50, 25.0)
    return {
        "total_hrt": total_hrt, "carbon_factor": carbon, "hrt_factor": hrt_f,
        "loading_factor": load_f, "nitrate_factor": nitrate_f, "temp_factor": temp_f,
        "se_load_kg_day": se_load, "base_se_removal": base, "actual_se_removal": actual,
        "actual_nitrate_removal": no3_act, "required_methanol": required_meoh,
        "removals": {
            "selenium": actual, "nitrate": no3_act, "nitrite": no3_act,
            "ammonia": gf(d, "bio_ammonia_removal"),
            "bod": gf(d, "bio_bod_removal"), "cod": gf(d, "bio_cod_removal"),
        },
    }


# ------------------------------------------------------------------ whole train
def run_train(d):
    q = gf(d, "flow") * GPM_TO_M3H
    R = {"flow_gpm": gf(d, "flow"), "q_m3h": q, "q_m3d": q * 24,
         "feed": {}, "removal": {}, "stream": {}}
    s = influent(d)
    R["stream"]["inf"] = dict(s)

    def stage(key, rem):
        nonlocal s
        R["feed"][key] = dict(s)
        R["removal"][key] = {p: float(rem.get(p, 0.0)) for p in TRAIN_PARAMS}
        s = {p: apply_removal(s[p], R["removal"][key][p]) for p in TRAIN_PARAMS}
        R["stream"][key] = dict(s)

    # Reaction tanks A-C: FeCl3 / lime / polymer curves AND organosulfide act together
    r_fe = rt_removals(d)
    r_os = {"mercury": gf(d, "os_hg_removal"), "cadmium": gf(d, "os_cd_removal"),
            "lead": gf(d, "os_pb_removal"), "arsenic": gf(d, "os_as_removal"),
            "selenium": gf(d, "os_se_removal")}
    r_rt = {p: 100.0 * (1 - (1 - r_fe.get(p, 0.0) / 100.0) * (1 - r_os.get(p, 0.0) / 100.0))
            for p in TRAIN_PARAMS}
    # informational "os" view = the organosulfide contribution inside the reaction tanks
    R["feed"]["os"] = {p: apply_removal(s[p], r_fe.get(p, 0.0)) for p in TRAIN_PARAMS}
    R["removal"]["os"] = {p: float(r_os.get(p, 0.0)) for p in TRAIN_PARAMS}
    stage("rt", r_rt)
    R["stream"]["os"] = dict(R["stream"]["rt"])

    stage("cl", {"tss": gf(d, "cl_tss_removal"), "arsenic": gf(d, "cl_arsenic_removal"),
                 "selenium": gf(d, "cl_selenium_removal"), "mercury": gf(d, "cl_mercury_removal"),
                 "lead": gf(d, "cl_lead_removal"), "cadmium": gf(d, "cl_cadmium_removal")})
    stage("cw", {})                                   # clearwell: pH adjustment only
    stage("sf", {"tss": gf(d, "sf_tss_removal"), "arsenic": gf(d, "sf_arsenic_removal"),
                 "selenium": gf(d, "sf_selenium_removal"), "mercury": gf(d, "sf_mercury_removal"),
                 "lead": gf(d, "sf_lead_removal"), "cadmium": gf(d, "sf_cadmium_removal")})
    R["bio"] = bio_perf(d, s)
    stage("bio", R["bio"]["removals"])
    stage("uf", {"tss": gf(d, "uf_tss_removal"), "arsenic": gf(d, "uf_arsenic_removal"),
                 "selenium": gf(d, "uf_selenium_removal"), "mercury": gf(d, "uf_mercury_removal"),
                 "bod": gf(d, "uf_bod_removal"), "cod": gf(d, "uf_cod_removal")})
    R["final"] = dict(s)
    return R


# ------------------------------------------------------------------ unit sizing
# every design function returns {"rows": [(label, value, unit, fmt)], ...raw values}
def mixing(q_m3h, minutes, G):
    """Tank volume (m3) and mixing power (kW) from detention time and velocity gradient."""
    v = q_m3h * minutes / 60.0
    return v, (G ** 2) * MU_WATER * v / 1000.0


def design_eq(d, R):
    n = max(gi(d, "eq_tanks"), 1)
    hrt = gf(d, "eq_hrt_hr")
    v = R["q_m3h"] * hrt
    return {"volume": v, "n": n, "each": v / n, "rows": [
        ("Design flow", R["q_m3h"], "m³/h", ",.1f"),
        ("EQ tank HRT (total)", hrt, "hr", ".1f"),
        ("EQ tank total working volume", v, "m³", ",.0f"),
        ("Number of EQ tanks (A / B)", n, "-", "d"),
        ("Working volume each", v / n, "m³", ",.0f"),
    ]}


def design_rt(d, R):
    q = R["q_m3h"]
    v1, p1 = mixing(q, gf(d, "rt_react_min"), gf(d, "rt_react_g"))
    v2, p2 = mixing(q, gf(d, "rt_rapid_min"), gf(d, "rt_rapid_g"))
    v3, p3 = mixing(q, gf(d, "rt_floc_min"), gf(d, "rt_floc_g"))
    return {"v_react": v1, "v_rapid": v2, "v_floc": v3, "p_react": p1, "p_rapid": p2,
            "p_floc": p3, "rows": [
        ("Reaction tank A volume", v1, "m³", ",.0f"),
        ("Reaction tank B volume", v2, "m³", ",.0f"),
        ("Reaction tank C volume", v3, "m³", ",.0f"),
        ("Total volume (A + B + C)", v1 + v2 + v3, "m³", ",.0f"),
        ("Tank A mixing power", p1, "kW", ".1f"),
        ("Tank B mixing power", p2, "kW", ".1f"),
        ("Tank C mixing power", p3, "kW", ".2f"),
    ]}


def design_cl(d, R):
    n = max(gi(d, "clarifier_no"), 1)
    sor, swd, fb = gf(d, "clarifier_sor"), gf(d, "clarifier_swd"), gf(d, "clarifier_freeboard")
    q_each = R["q_m3h"] / n
    area = q_each / max(sor, 0.01)
    dia = math.sqrt(4 * area / math.pi)
    vol = area * swd
    hrt = vol / q_each if q_each else 0
    weir = math.pi * dia
    wlr = q_each * 24 / weir if weir else 0
    drive = max(0.75, 0.01 * dia ** 2)          # rake drive allowance
    return {"n": n, "q_each": q_each, "area": area, "diameter": dia, "volume": vol,
            "hrt": hrt, "weir": weir, "wlr": wlr, "total_depth": swd + fb, "drive_kw": drive,
            "rows": [
        ("Design flow (total)", R["q_m3h"], "m³/h", ",.1f"),
        ("Flow per clarifier", q_each, "m³/h", ",.1f"),
        ("Area required (each)", area, "m²", ",.1f"),
        ("Diameter (each)", dia, "m", ".1f"),
        ("Volume (each)", vol, "m³", ",.1f"),
        ("Detention time", hrt, "hr", ".2f"),
        ("Weir length (each)", weir, "m", ".1f"),
        ("Weir loading rate", wlr, "m³/m·day", ",.0f"),
        ("Total tank depth", swd + fb, "m", ".1f"),
        ("Rake drive (each, approx.)", drive, "kW", ".2f"),
    ]}


def design_sf(d, R):
    rate, n = gf(d, "sf_filtration_rate"), max(gi(d, "sf_num_filters"), 1)
    depth, bw_rate = gf(d, "sf_bed_depth"), gf(d, "sf_backwash_rate")
    total_area = R["q_m3h"] / max(rate, 0.01)
    area_each = total_area / n
    dia = math.sqrt(4 * area_each / math.pi)
    media = total_area * depth
    bw_one = area_each * bw_rate                # one filter backwashed at a time
    bw_min = gf(d, "sf_bw_min")
    bw_vol = bw_one * bw_min / 60.0
    air = area_each * gf(d, "sf_air_rate")
    return {"n": n, "total_area": total_area, "area_each": area_each, "diameter": dia,
            "media_volume": media, "bw_one": bw_one, "bw_vol": bw_vol, "air_flow": air,
            "rows": [
        ("Total filter area", total_area, "m²", ",.1f"),
        ("Area per filter", area_each, "m²", ",.1f"),
        ("Filter diameter", dia, "m", ".2f"),
        ("Media volume", media, "m³", ",.1f"),
        ("Backwash flow (1 filter)", bw_one, "m³/h", ",.0f"),
        ("Backwash flow (1 filter)", bw_one / GPM_TO_M3H, "gpm", ",.0f"),
        ("Backwash volume / event", bw_vol, "m³", ",.0f"),
        ("Air scour flow (1 filter)", air, "m³/h", ",.0f"),
    ]}


def design_os(d, R):
    dose = gf(d, "organosulfide_dose")
    return {"volume": 0.0, "power": 0.0, "rows": [
        ("Organosulfide dose (total)", dose, "mg/L", ".1f"),
        ("Injection points (Reaction Tanks A, B, C)", 3, "-", "d"),
        ("Dose per tank (equal split)", dose / 3.0, "mg/L", ".1f"),
        ("Consumption", dose * R["q_m3d"] / 1000.0, "kg/day", ",.0f"),
    ]}


def design_bio(d, R):
    q = R["q_m3h"]
    h1, h2 = gf(d, "bio_stage1_hrt"), gf(d, "bio_stage2_hrt")
    n1, n2 = max(gi(d, "bio_stage1_reactors"), 1), max(gi(d, "bio_stage2_reactors"), 1)
    v1, v2 = q * h1, q * h2
    r1, r2 = v1 / n1, v2 / n2
    depth = max(gf(d, "bio_depth"), 0.5)
    a1, a2 = r1 / depth, r2 / depth
    a_max = max(a1, a2)
    bw_one = a_max * gf(d, "bio_bw_rate")
    b = R["bio"]
    return {"v1": v1, "v2": v2, "n1": n1, "n2": n2, "r1": r1, "r2": r2, "a1": a1, "a2": a2,
            "total": v1 + v2, "bw_one": bw_one,
            "bw_vol": bw_one * gf(d, "bio_bw_min") / 60.0, "rows": [
        ("Flow", q, "m³/h", ",.1f"),
        ("Stage 1 total volume", v1, "m³", ",.0f"),
        ("Stage 2 total volume", v2, "m³", ",.0f"),
        ("Total bioreactor volume", v1 + v2, "m³", ",.0f"),
        ("Stage 1 volume / reactor", r1, "m³", ",.1f"),
        ("Stage 2 volume / reactor", r2, "m³", ",.1f"),
        ("Stage 1 plan area / reactor", a1, "m²", ",.1f"),
        ("Stage 2 plan area / reactor", a2, "m²", ",.1f"),
        ("Total HRT", b["total_hrt"], "hr", ".1f"),
        ("Backwash flow (1 reactor)", bw_one, "m³/h", ",.0f"),
    ]}


def design_uf(d, R):
    feed = R["q_m3h"]
    rec = gf(d, "uf_recovery")
    perm = feed * rec / 100.0
    reject = feed - perm
    flux = max(gf(d, "uf_flux"), 0.1)
    area = perm * 1000.0 / flux                 # permeate (L/h) / flux (LMH)
    mod_area = max(gf(d, "uf_module_area"), 0.1)
    trains = max(gi(d, "uf_train_count"), 1)
    modules = math.ceil(area / mod_area)
    per_train = math.ceil(modules / trains)
    area_train = per_train * mod_area
    bw_flow = gf(d, "uf_bw_flux_ratio") * flux * area_train / 1000.0
    return {"feed": feed, "permeate": perm, "reject": reject, "area": area,
            "modules": modules, "modules_per_train": per_train, "trains": trains,
            "area_train": area_train, "bw_flow_train": bw_flow, "rows": [
        ("Feed flow", feed, "m³/h", ",.1f"),
        ("Permeate flow", perm, "m³/h", ",.1f"),
        ("Reject / backwash flow", reject, "m³/h", ",.1f"),
        ("Recovery", rec, "%", ".1f"),
        ("Design flux", flux, "LMH", ".0f"),
        ("Total membrane area (required)", area, "m²", ",.0f"),
        ("Modules required", modules, "-", ",d"),
        ("Modules per train", per_train, "-", ",d"),
        ("Number of trains", trains, "-", ",d"),
        ("Backwash flow per train", bw_flow, "m³/h", ",.0f"),
    ]}


# ------------------------------------------------------------------ chemicals
def chem_use(d, R):
    out = {}
    for k, c in CHEMICALS.items():
        dose = gf(d, c["dose"])
        kg_day = dose * R["q_m3d"] / 1000.0
        conc, days = gf(d, f"{k}_conc"), gf(d, f"{k}_days")
        lph = kg_day / 24.0 / (max(conc, 1e-6) / 100.0 * c["sg"])
        m3_day = lph * 24 / 1000.0
        out[k] = {"key": k, "name": c["name"], "dose": dose, "kg_day": kg_day,
                  "conc": conc, "sg": c["sg"], "days": days, "lph": lph,
                  "m3_day": m3_day, "tank_m3": m3_day * days,
                  "unit_cost": gf(d, f"{k}_cost"),
                  "cost_day": kg_day * gf(d, f"{k}_cost"),
                  "material": c["material"], "mixed": c["mixed"]}
    return out


# ------------------------------------------------------------------ sludge
FE_OH3_PER_FECL3 = 0.66      # kg Fe(OH)3 per kg FeCl3 dosed


def sludge(d, R):
    q = R["q_m3d"]
    inf, rt, cl = R["stream"]["inf"], R["stream"]["rt"], R["stream"]["cl"]

    def kg(mg_l):
        return max(mg_l, 0.0) * q / 1000.0

    tss = kg(inf["tss"] - cl["tss"])
    hardness = kg(inf["hardness"] - rt["hardness"])
    metals = kg((inf["iron"] - rt["iron"]) + (inf["aluminum"] - rt["aluminum"]))
    fe_oh = kg(gf(d, "fecl3_dose") * FE_OH3_PER_FECL3)
    dry = tss + hardness + metals + fe_oh
    pct = max(gf(d, "sludge_solids_percent"), 0.1)
    wet_kg = dry * 100.0 / pct
    wet_m3 = wet_kg / 1000.0
    cake_pct = max(gf(d, "cake_solids_pct"), 0.1)
    cake_kg = dry * 100.0 / cake_pct
    return {"tss": tss, "hardness": hardness, "metals": metals, "fe_oh": fe_oh,
            "dry": dry, "wet_kg": wet_kg, "wet_m3": wet_m3,
            "cake_kg": cake_kg, "cake_t_year": cake_kg * 365 / 1000.0,
            "dry_t_year": dry * 365 / 1000.0}


# ======================================================================
# EQUIPMENT
# ======================================================================
STD_MOTORS = [0.37, 0.55, 0.75, 1.1, 1.5, 2.2, 3, 4, 5.5, 7.5, 11, 15, 18.5, 22, 30, 37,
              45, 55, 75, 90, 110, 132, 160, 200, 250, 315, 355, 400, 450, 500, 560, 630]

G_INF = "Influent / Equalization"
G_RT = "Reaction Tank"
G_CL = "Clarifier"
G_CW = "Clearwell / pH Adjustment"
G_SF = "Sand Filter"
G_BF = "Bio Feed & Nutrient"
G_OS = "Organosulfide"          # kept for compatibility (organosulfide now lives in G_RT)
G_BIO = "Bioreactor"
G_UF = "UF System"
G_EFF = "Final Effluent"
G_SLG = "Sludge Handling"
GROUPS = [G_INF, G_RT, G_CL, G_SLG, G_CW, G_SF, G_BF, G_BIO, G_UF, G_EFF]


def std_motor(kw):
    for m in STD_MOTORS:
        if m >= kw:
            return m
    return math.ceil(kw / 50.0) * 50.0


# ------------------------------------------------------------------ row builders
def pump(d, group, tag, service, q_each, tdh, *, ptype="Centrifugal", duty=1, standby=1,
         sg=1.0, eff=None, load=1.0):
    eff = eff or gf(d, "pump_eff")
    q = max(q_each, 0.0) * (1 + gf(d, "pump_margin_pct") / 100.0)
    abs_kw = q * tdh * sg / (367.0 * eff)
    return {"kind": "pump", "group": group, "tag": tag, "service": service, "type": ptype,
            "duty": int(duty), "standby": int(standby), "q": q, "tdh": tdh,
            "abs_kw": abs_kw, "motor_kw": std_motor(abs_kw * 1.15),
            "load": min(max(load, 0.0), 1.0)}


def dosing_pump(d, group, tag, service, lph, duty=1):
    duty = max(int(duty), 1)
    q_each = lph / duty / 1000.0 * 1.25 / (1 + gf(d, "pump_margin_pct") / 100.0)  # 25 % turndown margin
    ptype = "Metering (diaphragm)" if lph / duty < 1000 else "Progressive cavity"
    return pump(d, group, tag, service, q_each, 30.0, ptype=ptype, duty=duty, standby=1,
                eff=0.40)


def tank(d, group, tag, service, v_work, *, ttype="Storage", material="FRP / HDPE", qty=1, ar=1.0,
         costed=True):
    v_work = max(v_work, 0.0)
    v_des = v_work * (1 + gf(d, "tank_freeboard_pct") / 100.0)
    dia = (4 * v_des / (math.pi * ar)) ** (1 / 3) if v_des > 0 else 0.0
    return {"kind": "tank", "group": group, "tag": tag, "service": service, "type": ttype,
            "qty": int(qty), "v_work": v_work, "v_design": v_des, "dia": dia,
            "height": dia * ar, "material": material, "costed": costed}


def mixer(d, group, tag, service, v, *, G=None, w_m3=None, load=1.0, qty=1):
    if G is not None:
        watts = (G ** 2) * MU_WATER * v          # W  (G^2 * mu * V)
        spec = f"Mixer, G = {G:.0f} 1/s"
    else:
        watts = w_m3 * v
        spec = f"Mixer, {w_m3:.0f} W/m³"
    absorbed = watts / 1000.0 / 0.75        # drive efficiency
    return {"kind": "other", "group": group, "tag": tag, "service": service, "spec": spec,
            "qty": int(qty), "kw": absorbed, "motor_kw": std_motor(absorbed * 1.15), "load": load}


def other(group, tag, service, spec, qty=1, kw=0.0, load=1.0):
    return {"kind": "other", "group": group, "tag": tag, "service": service, "spec": spec,
            "qty": int(qty), "kw": kw, "motor_kw": std_motor(kw * 1.15) if kw > 0 else 0.0,
            "load": load}


def chem_system(d, chem, key, group, t_tag, p_tag, m_tag=None, duty=1):
    """Storage tank + dosing pump (+ agitator for slurries / make-up solutions)."""
    c = chem[key]
    if c["kg_day"] <= 0:
        return []
    rows = [tank(d, group, t_tag, f"{c['name']} storage" + (" / make-up" if c["mixed"] else ""),
                 c["tank_m3"], ttype=f"Chemical, {c['days']:.0f} d storage", material=c["material"]),
            dosing_pump(d, group, p_tag, f"{c['name']} dosing pump skid", c["lph"], duty=duty)]
    if c["mixed"] and m_tag:
        rows.append(mixer(d, group, m_tag, f"{c['name']} tank agitator", c["tank_m3"], w_m3=10.0))
    for r in rows:
        r["chem"] = True
    return rows


def blower_kw(q_m3h, dp_kpa, eff=0.60, p1=101.325, gamma=1.4):
    q = q_m3h / 3600.0
    ratio = (p1 + dp_kpa) / p1
    return gamma / (gamma - 1) * p1 * q / eff * (ratio ** ((gamma - 1) / gamma) - 1)


# ------------------------------------------------------------------ group builders
def build_equipment(d, R, des, chem, sl):
    q = R["q_m3h"]
    rows = []

    # ---------------- outdoor sump / equalization tanks A-B / feed pump skid
    g = G_INF
    eq = des["eq"]
    n, sb = max(gi(d, "eq_feed_duty"), 1), gi(d, "eq_feed_standby")
    sump_q = q * gf(d, "eq_sump_frac") / 100.0
    rows += [
        tank(d, g, "T-101", "Equalization tanks A / B", eq["each"], ttype="Equalization (mixed)",
             material="Concrete / coated carbon steel", qty=eq["n"]),
        mixer(d, g, "M-101", "EQ tank mixers (one per tank)", eq["each"],
              w_m3=gf(d, "eq_mixer_w_m3"), qty=eq["n"]),
        pump(d, g, "P-101", "Wastewater treatment feed pump skid", q / n, gf(d, "eq_feed_tdh"),
             duty=n, standby=sb),
        tank(d, g, "T-102", "Outdoor sump (gypsum / sludge dewatering, leachate, recycles)",
             sump_q * gf(d, "eq_sump_min") / 60.0, ttype="Sump", material="Concrete"),
        pump(d, g, "P-102", "Outdoor sump pumps (to EQ tanks)", sump_q, gf(d, "eq_sump_tdh"),
             ptype="Centrifugal (solids handling)", duty=1, standby=1, load=0.5),
    ]

    # ---------------- reaction tanks A > B > C (overflow in series)
    g = G_RT
    r = des["rt"]
    rows += [
        tank(d, g, "T-201", "Reaction tank A", r["v_react"], ttype="Mixed reactor (overflow to B)",
             material="Concrete / coated carbon steel"),
        mixer(d, g, "M-201", "Reaction tank A mixer", r["v_react"], G=gf(d, "rt_react_g")),
        tank(d, g, "T-202", "Reaction tank B", r["v_rapid"], ttype="Mixed reactor (overflow to C)",
             material="Concrete / coated carbon steel"),
        mixer(d, g, "M-202", "Reaction tank B mixer", r["v_rapid"], G=gf(d, "rt_rapid_g")),
        tank(d, g, "T-203", "Reaction tank C", r["v_floc"], ttype="Mixed reactor (overflow to splitter)",
             material="Concrete / coated carbon steel"),
        mixer(d, g, "M-203", "Reaction tank C mixer", r["v_floc"], G=gf(d, "rt_floc_g")),
    ]
    rows += chem_system(d, chem, "lime", g, "T-204", "P-204", "M-204")
    rows += chem_system(d, chem, "naoh", g, "T-205", "P-205")
    rows += chem_system(d, chem, "fecl3", g, "T-206", "P-206")
    rows += chem_system(d, chem, "polymer", g, "T-207", "P-207", "M-207")
    rows += chem_system(d, chem, "organosulfide", g, "T-208", "P-208")

    # ---------------- clarifiers A / B (splitter, rake, sludge pump skid)
    g = G_CL
    c = des["cl"]
    run_hr = max(gf(d, "cl_sludge_run_hr"), 1.0)
    n_sl = max(gi(d, "cl_sludge_duty"), 1)
    rows += [
        other(g, "SPL-301", "Splitter box (to clarifiers)", "Equal flow split", qty=1, kw=0.0),
        other(g, "CL-301", "Clarifiers (with rake mechanisms)",
              f"Dia {c['diameter']:.1f} m x SWD {gf(d, 'clarifier_swd'):.1f} m, "
              f"SOR {gf(d, 'clarifier_sor'):.1f} m/h", qty=c["n"], kw=c["drive_kw"]),
        pump(d, g, "P-301", "Clarifier sludge pump skid", sl["wet_m3"] / run_hr / n_sl,
             gf(d, "cl_sludge_tdh"), ptype="Progressive cavity", duty=n_sl,
             standby=gi(d, "cl_sludge_standby"), sg=1.05, eff=0.45, load=run_hr / 24.0),
    ]

    # ---------------- clearwell tank (HCl)
    g = G_CW
    cw_v = q * gf(d, "cw_min") / 60.0
    rows += [
        tank(d, g, "T-301", "Clearwell tank (HCl pH adjustment)", cw_v, ttype="Mixed break tank",
             material="Concrete / FRP (acid-resistant lining)"),
        mixer(d, g, "M-301", "Clearwell tank mixer", cw_v, w_m3=gf(d, "cw_mixer_w_m3")),
    ]
    rows += chem_system(d, chem, "hcl", g, "T-302", "P-302")
    if chem["hcl"]["kg_day"] > 0:
        rows.append(other(g, "SCR-302", "Vapor scrubber (HCl tank vent)", "Packed scrubber, 4 in vent",
                          qty=1, kw=0.75))

    # ---------------- sand filters
    g = G_SF
    s = des["sf"]
    n, sb = max(gi(d, "sf_feed_duty"), 1), gi(d, "sf_feed_standby")
    bw_min = gf(d, "sf_bw_min")
    nf = s["n"]
    bw_load = min(nf * bw_min / 1440.0, 1.0)
    ret_hr = max(gf(d, "sf_bw_return_hr"), 0.5)
    air_kw = blower_kw(s["air_flow"], gf(d, "sf_air_dp_kpa"))
    rows += [
        pump(d, g, "P-401", "Sand filter feed pump skid", q / n, gf(d, "sf_feed_tdh"), duty=n,
             standby=sb),
        pump(d, g, "P-402", "Filter backwash pump", s["bw_one"], gf(d, "sf_bw_tdh"), duty=1,
             standby=1, load=bw_load),
        other(g, "B-401", "Air scour blower",
              f"{s['air_flow']:,.0f} m³/h @ {gf(d, 'sf_air_dp_kpa'):.0f} kPa", qty=1, kw=air_kw,
              load=min(nf * 5.0 / 1440.0, 1.0)),
        tank(d, g, "T-402", "Backwash water tank", s["bw_vol"], ttype="Backwash supply",
             material="Concrete / carbon steel"),
        tank(d, g, "T-403", "Backwash waste tank", s["bw_vol"], ttype="Waste equalization",
             material="Concrete"),
        pump(d, g, "P-403", "Backwash waste return pumps (to outdoor sump)", s["bw_vol"] / ret_hr,
             gf(d, "sf_bw_return_tdh"), ptype="Centrifugal (solids handling)", duty=1, standby=1,
             load=min(nf * ret_hr / 24.0, 1.0)),
    ]

    # ---------------- bio feed tank, strainer, heat exchanger, bioreactor stages 1 & 2
    g = G_BIO
    gb = G_BF
    b = des["bio"]
    n, sb = max(gi(d, "bio_feed_duty"), 1), gi(d, "bio_feed_standby")
    nres = b["n1"] + b["n2"]
    feed_v = q * gf(d, "bio_feed_tank_min") / 60.0
    t_in, t_tgt = gf(d, "temperature"), gf(d, "bio_hx_target_c")
    dT = max(t_tgt - t_in, 0.0)
    duty_kw = q * 1000.0 * 4.18 * dT / 3600.0
    rows += [
        tank(d, gb, "T-601", "Bio feed tank (nutrient mixed in)", feed_v, ttype="Mixed break tank",
             material="Concrete / FRP"),
        mixer(d, gb, "M-601", "Bio feed tank mixer", feed_v, w_m3=gf(d, "bio_feed_tank_w_m3")),
        pump(d, gb, "P-601", "Bio feed pump skid", q / n, gf(d, "bio_feed_tdh"), duty=n, standby=sb),
        other(gb, "S-601", "Bio feed strainer skid (self-cleaning)",
              f"{q:,.0f} m³/h, {gf(d, 'bio_strainer_um'):.0f} um; backwash to header sump",
              qty=1, kw=0.75),
        other(gb, "HX-601", "Heat exchanger skid",
              f"Thermal duty {duty_kw:,.0f} kW ({t_in:.0f} -> {max(t_in, t_tgt):.0f} C); "
              f"thermal, not in electrical power", qty=1, kw=0.0),
        other(g, "FCS-601", "Stage 1 flow control skid (1A-1G)", f"{b['n1']} valve / meter runs",
              qty=1, kw=0.0),
        tank(d, g, "R-601", "Bioreactor - Stage 1 (reactors 1A-1G)", b["r1"],
             ttype="Fixed-film bioreactor", material="Carbon steel, lined", qty=b["n1"],
             costed=False),
        tank(d, g, "R-602", "Bioreactor - Stage 2 (reactors 2A-2V)", b["r2"],
             ttype="Fixed-film bioreactor", material="Carbon steel, lined", qty=b["n2"],
             costed=False),
        other(g, "FCS-602", "Stage 2 flow control skid (2A-2V)", f"{b['n2']} valve / meter runs",
              qty=1, kw=0.0),
    ]
    ratio = gf(d, "bio_recirc_ratio")
    if ratio > 0:
        rows += [
            pump(d, g, "P-602", "Stage 1 recirculation pumps", q * ratio, gf(d, "bio_recirc_tdh"),
                 duty=1, standby=1),
            pump(d, g, "P-603", "Stage 2 recirculation pumps", q * ratio, gf(d, "bio_recirc_tdh"),
                 duty=1, standby=1),
        ]
    rows += [
        tank(d, g, "T-604", "Bioreactor backwash waste tank", b["bw_vol"], ttype="Waste equalization",
             material="Concrete"),
        pump(d, g, "P-605", "Bioreactor backwash waste pumps (to header sump / outdoor sump)",
             b["bw_vol"] / 4.0, 15.0, ptype="Centrifugal (solids handling)", duty=1, standby=1,
             load=min(nres * 4.0 / (7 * 24.0), 1.0)),
    ]
    rows += chem_system(d, chem, "methanol", g, "T-605", "P-606")
    rows += chem_system(d, chem, "nutrient", gb, "T-606", "P-607", duty=max(gi(d, "nutrient_pump_duty"), 1))

    # ---------------- UF: feed sump, sump pumps, feed pumps, skids, effluent / backwash tank
    g = G_UF
    u = des["uf"]
    tr = u["trains"]
    cip_v = max(1.0, u["modules_per_train"] * gf(d, "uf_cip_vol_per_module"))
    bw_flow = max(u["bw_flow_train"], b["bw_one"])          # one skid serves UF + bioreactor backwash
    bw_tdh = max(gf(d, "uf_bw_tdh"), gf(d, "bio_bw_tdh"))
    eff_v = max(u["permeate"] * gf(d, "eff_tank_min") / 60.0,
                u["permeate"] * gf(d, "uf_tank_min") / 60.0, b["bw_vol"])
    rows += [
        tank(d, g, "T-701", "Stage 2 UF feed sump", q * gf(d, "uf_feed_tank_min") / 60.0,
             ttype="Sump", material="Concrete"),
        pump(d, g, "P-707", "Stage 2 UF feed sump pumps A / B", q, gf(d, "uf_sump_tdh"), duty=1,
             standby=1),
        pump(d, g, "P-701", "Ultrafiltration feed pumps (one per skid)", q / tr, gf(d, "uf_feed_tdh"),
             duty=tr, standby=gi(d, "uf_feed_standby")),
        other(g, "UF-701", "Ultrafilter (UF) skids",
              f"{u['modules_per_train']} modules / skid, {u['area_train']:,.0f} m2 / skid",
              qty=tr, kw=0.0),
        pump(d, g, "P-702", "Backwash pump (UF and bioreactor backwash)", bw_flow, bw_tdh, duty=1,
             standby=1, load=min(tr / 30.0, 1.0)),
        tank(d, g, "T-702", "Effluent / backwash tank", eff_v, ttype="Effluent + backwash supply",
             material="Concrete / FRP"),
        tank(d, g, "T-703", "CIP tank", cip_v, ttype="CIP solution", material="HDPE / SS316"),
        pump(d, g, "P-703", "CIP pump", u["modules_per_train"] * gf(d, "uf_cip_flow_per_module"),
             gf(d, "uf_bw_tdh"), duty=1, standby=0, load=0.01),
        tank(d, g, "T-704", "CIP neutralization tank", 2 * cip_v, ttype="Neutralization",
             material="HDPE"),
        tank(d, g, "T-705", "CEB chemical day tank (allowance)", gf(d, "uf_ceb_tank_m3"),
             ttype="Chemical", material="HDPE"),
        dosing_pump(d, g, "P-704", "CEB chemical dosing pump (allowance)", gf(d, "uf_ceb_pump_lph")),
        tank(d, g, "T-706", "UF reject / backwash waste tank",
             u["reject"] * 15.0 / 60.0 + u["bw_flow_train"] / 60.0, ttype="Waste equalization",
             material="Concrete"),
        pump(d, g, "P-705", "UF reject / backwash waste pumps (to outdoor sump)", u["reject"], 20.0,
             duty=1, standby=1),
    ]

    # ---------------- effluent pump skid, UV, NPDES
    g = G_EFF
    n = max(gi(d, "eff_pump_duty"), 1)
    rows += [
        pump(d, g, "P-801", "Backwash / effluent pump skid (effluent to UV and NPDES)",
             u["permeate"] / n, gf(d, "eff_pump_tdh"), duty=n, standby=gi(d, "eff_pump_standby")),
        other(g, "UV-801", "Ultraviolet treatment",
              f"{u['permeate']:,.0f} m³/h @ {gf(d, 'uv_kwh_m3'):.3f} kWh/m3", qty=1,
              kw=u["permeate"] * gf(d, "uv_kwh_m3")),
    ]

    # ---------------- sludge tanks A/B, press feed pumps, vacuum filter presses A/B/C
    g = G_SLG
    press_hr = max(gf(d, "sludge_press_hr"), 1.0)
    nt, npress = max(gi(d, "sludge_tanks"), 1), max(gi(d, "sludge_presses"), 1)
    hold_v = sl["wet_m3"] * gf(d, "sludge_hold_days") / nt
    rows += [
        tank(d, g, "T-901", "Sludge tanks A / B", hold_v, ttype="Thickener (mixed)",
             material="Concrete / carbon steel", qty=nt),
        mixer(d, g, "M-901", "Sludge tank mixers", hold_v, w_m3=10.0, qty=nt),
        pump(d, g, "P-901", "Filter press feed pump skid", sl["wet_m3"] / press_hr,
             gf(d, "sludge_feed_tdh"), ptype="Progressive cavity / diaphragm", duty=1, standby=1,
             sg=1.05, eff=0.45, load=press_hr / 24.0),
        other(g, "FP-901", "Vacuum dewatering filter presses A / B / C",
              f"{sl['dry']:,.0f} kg dry solids/day -> {sl['cake_kg'] / 1000:,.1f} t cake/day "
              f"@ {gf(d, 'cake_solids_pct'):.0f}% solids (total, {npress} presses)",
              qty=npress, kw=0.0),
        pump(d, g, "P-902", "Filtrate pumps (to dewatering sump / EQ tanks)", sl["wet_m3"] / press_hr,
             gf(d, "sludge_filtrate_tdh"), duty=1, standby=1, load=press_hr / 24.0),
    ]
    return rows


# ------------------------------------------------------------------ summaries
def power_summary(rows):
    installed = running = kwh = 0.0
    by_group = {}
    for r in rows:
        if r["kind"] == "pump":
            inst = r["motor_kw"] * (r["duty"] + r["standby"])
            run = r["abs_kw"] * r["duty"] * r["load"]
        elif r["kind"] == "other":
            inst = r["motor_kw"] * r["qty"]
            run = r["kw"] * r["qty"] * r["load"]
        else:
            continue
        installed += inst
        running += run
        kwh += run * 8760.0
        g = by_group.setdefault(r["group"], {"installed_kw": 0.0, "running_kw": 0.0})
        g["installed_kw"] += inst
        g["running_kw"] += run
    return {"installed_kw": installed, "running_kw": running, "kwh_year": kwh, "by_group": by_group}


def filter_rows(rows, groups=None):
    if groups is None:
        return rows
    if isinstance(groups, str):
        groups = [groups]
    return [r for r in rows if r["group"] in groups]


def equipment_frames(rows, groups=None, include_group=False):
    """Return (pumps_df, tanks_df, other_df) with display-ready columns."""
    rows = filter_rows(rows, groups)
    pumps, tanks, others = [], [], []
    for r in rows:
        grp = {"Area": r["group"]} if include_group else {}
        if r["kind"] == "pump":
            pumps.append({**grp, "Tag": r["tag"], "Service": r["service"], "Type": r["type"],
                          "Qty (duty+stby)": f"{r['duty']}+{r['standby']}",
                          "Flow each (m³/h)": r["q"], "TDH (m)": r["tdh"],
                          "Absorbed kW (each)": r["abs_kw"], "Motor kW (each)": r["motor_kw"],
                          "Run factor": r["load"]})
        elif r["kind"] == "tank":
            tanks.append({**grp, "Tag": r["tag"], "Service": r["service"], "Type": r["type"],
                          "Qty": r["qty"], "Working vol each (m³)": r["v_work"],
                          "Design vol each (m³)": r["v_design"], "Dia (m)": r["dia"],
                          "Height (m)": r["height"], "Material": r["material"]})
        else:
            others.append({**grp, "Tag": r["tag"], "Service": r["service"],
                           "Specification": r["spec"], "Qty": r["qty"],
                           "Absorbed kW (each)": r["kw"], "Motor kW (each)": r["motor_kw"]})
    return pd.DataFrame(pumps), pd.DataFrame(tanks), pd.DataFrame(others)


# ======================================================================
# COSTS
# ======================================================================
def crf(rate_pct, years):
    r, n = rate_pct / 100.0, max(years, 1)
    if r <= 0:
        return 1.0 / n
    return r * (1 + r) ** n / ((1 + r) ** n - 1)


def build_costs(d, R, rows, chem, sl):
    power = power_summary(rows)

    pump_kw = sum(r["motor_kw"] * (r["duty"] + r["standby"]) for r in rows if r["kind"] == "pump")
    tank_m3 = sum(r["v_design"] * r["qty"] for r in rows if r["kind"] == "tank" and r.get("costed", True))
    pump_cost = pump_kw * gf(d, "pump_cost_per_kw")
    tank_cost = tank_m3 * gf(d, "tank_cost_per_m3")

    capex_items = [
        ("Reaction tank system", gf(d, "reaction_tank_cost")),
        ("Clarifier", gf(d, "clarifier_cost")),
        ("Sand filter", gf(d, "sand_filter_cost")),
        ("Organosulfide system", gf(d, "organosulfide_cost_capex")),
        ("Bioreactor", gf(d, "bioreactor_cost")),
        ("UF system", gf(d, "uf_cost")),
        ("Sludge dewatering system", gf(d, "sludge_system_cost")),
        ("Pumps (calculated from equipment list)", pump_cost),
        ("Tanks (calculated; excl. bioreactor vessels)", tank_cost),
    ]
    direct = sum(v for _, v in capex_items)
    install = direct * gf(d, "install_pct") / 100.0
    eng = (direct + install) * gf(d, "eng_pct") / 100.0
    cont = (direct + install + eng) * gf(d, "cont_pct") / 100.0
    total_capex = direct + install + eng + cont

    chem_year = sum(c["cost_day"] for c in chem.values()) * 365.0
    if gs(d, "power_basis").startswith("Calc"):
        power_cost = power["kwh_year"] * gf(d, "power_tariff")
    else:
        power_cost = gf(d, "electricity_cost")
    if gs(d, "sludge_basis").startswith("Calc"):
        sludge_cost = sl["cake_t_year"] * gf(d, "disposal_per_t")
    else:
        sludge_cost = gf(d, "sludge_disposal_cost")
    labor = gf(d, "labor_cost")
    maint = direct * gf(d, "maintenance_percent") / 100.0

    opex_items = [("Chemicals", chem_year), ("Sludge disposal", sludge_cost),
                  ("Electricity", power_cost), ("Labor", labor), ("Maintenance", maint)]
    total_opex = sum(v for _, v in opex_items)

    annual_m3 = R["q_m3d"] * 365.0
    ann_capex = total_capex * crf(gf(d, "discount_rate"), gf(d, "life_years"))
    return {
        "power": power, "pump_kw": pump_kw, "tank_m3": tank_m3,
        "capex_items": capex_items, "direct": direct, "install": install,
        "eng": eng, "cont": cont, "total_capex": total_capex,
        "opex_items": opex_items, "total_opex": total_opex,
        "chem_year": chem_year, "power_cost": power_cost, "sludge_cost": sludge_cost,
        "annual_m3": annual_m3, "annualized_capex": ann_capex,
        "opex_per_m3": total_opex / annual_m3 if annual_m3 else 0,
        "total_per_m3": (total_opex + ann_capex) / annual_m3 if annual_m3 else 0,
    }


# ======================================================================
# MODEL
# ======================================================================
def analyze(d):
    R = run_train(d)
    des = {
        "eq": design_eq(d, R), "rt": design_rt(d, R), "cl": design_cl(d, R),
        "sf": design_sf(d, R), "os": design_os(d, R), "bio": design_bio(d, R),
        "uf": design_uf(d, R),
    }
    chem = chem_use(d, R)
    sl = sludge(d, R)
    eq = build_equipment(d, R, des, chem, sl)
    cost = build_costs(d, R, eq, chem, sl)
    return {"R": R, "des": des, "chem": chem, "sludge": sl, "eq": eq, "cost": cost}


# ======================================================================
# TABLES
# ======================================================================
SECTION_TITLES = [
    ("summary", "Project Summary & Design Basis"),
    ("description", "Process Description"),
    ("influent", "Influent Water Quality"),
    ("pfd", "Process Flow Diagram"),
    ("units", "Unit Process Design"),
    ("quality", "Water Quality Through the Treatment Train"),
    ("equipment", "Equipment List - Pumps, Tanks & Mixers"),
    ("chemicals", "Chemical Consumption & Cost"),
    ("sludge", "Sludge Production"),
    ("cost", "CAPEX & OPEX"),
    ("effluent", "Final Effluent Water Quality & Compliance"),
    ("notes", "Assumptions & Notes"),
]


def kv_df(rows):
    out = []
    for label, val, unit, fmt in rows:
        try:
            s = f"{val:{fmt}}"
        except (ValueError, TypeError):
            s = str(val)
        out.append({"Parameter": label, "Value": s, "Unit": unit})
    return pd.DataFrame(out)


def compliance_df(d, R):
    rows = []
    for p, lk in COMPLIANCE:
        feed, fin, lim = R["stream"]["inf"][p], R["final"][p], gf(d, lk)
        rem = (feed - fin) / feed * 100 if feed > 0 else 0.0
        rows.append({"Parameter": PARAM_INFO[p][0], "Unit": PARAM_INFO[p][1], "Feed": feed,
                     "Final Effluent": fin, "Removal (%)": rem, "Limit": lim,
                     "Status": "PASS" if fin <= lim else "FAIL"})
    return pd.DataFrame(rows)


def stage_profile_df(R, params=None):
    params = params or TRAIN_PARAMS
    rows = []
    for p in params:
        row = {"Parameter": PARAM_INFO[p][0], "Unit": PARAM_INFO[p][1]}
        for key, label in STAGES:
            row[label] = R["stream"][key][p]
        rows.append(row)
    return pd.DataFrame(rows)


def first_stage_meeting(R, param, limit):
    for key, label in STAGES:
        if R["stream"][key][param] <= limit:
            return label
    return None


def influent_df(d, R):
    rows = []
    for p in TRAIN_PARAMS:
        rows.append({"Parameter": PARAM_INFO[p][0], "Unit": PARAM_INFO[p][1],
                     "Value": R["stream"]["inf"][p]})
    for p in UNTREATED_PARAMS:
        rows.append({"Parameter": PARAM_INFO[p][0], "Unit": PARAM_INFO[p][1], "Value": gf(d, p)})
    return pd.DataFrame(rows)


def chem_df(chem):
    rows = []
    for c in chem.values():
        rows.append({"Chemical": c["name"], "Dose (mg/L)": c["dose"],
                     "Consumption (kg/day)": c["kg_day"], "Strength (%)": c["conc"],
                     "Product flow (L/h)": c["lph"], "Storage (days)": c["days"],
                     "Storage tank (m³)": c["tank_m3"], "Unit cost ($/kg)": c["unit_cost"],
                     "Cost ($/day)": c["cost_day"]})
    df = pd.DataFrame(rows)
    return df


def sludge_df(sl, d):
    return pd.DataFrame({
        "Source": ["TSS removed", "Hardness precipitate", "Metal hydroxides (Fe, Al)",
                   "Ferric hydroxide from FeCl3", "TOTAL DRY SOLIDS"],
        "Dry solids (kg/day)": [sl["tss"], sl["hardness"], sl["metals"], sl["fe_oh"], sl["dry"]],
    })


def sludge_summary_df(sl, d):
    return kv_df([
        ("Dry solids", sl["dry"], "kg/day", ",.0f"),
        ("Dry solids", sl["dry_t_year"], "t/year", ",.0f"),
        ("Sludge solids content (thickened)", gf(d, "sludge_solids_percent"), "%", ".1f"),
        ("Wet sludge", sl["wet_kg"], "kg/day", ",.0f"),
        ("Wet sludge volume", sl["wet_m3"], "m³/day", ",.1f"),
        ("Filter cake solids", gf(d, "cake_solids_pct"), "%", ".1f"),
        ("Filter cake", sl["cake_kg"] / 1000.0, "t/day", ",.1f"),
        ("Filter cake", sl["cake_t_year"], "t/year", ",.0f"),
    ])


def capex_df(cost):
    rows = [{"Item": n, "Cost ($)": v} for n, v in cost["capex_items"]]
    rows += [{"Item": "DIRECT COST", "Cost ($)": cost["direct"]},
             {"Item": "Installation & indirects", "Cost ($)": cost["install"]},
             {"Item": "Engineering", "Cost ($)": cost["eng"]},
             {"Item": "Contingency", "Cost ($)": cost["cont"]},
             {"Item": "TOTAL CAPEX", "Cost ($)": cost["total_capex"]}]
    return pd.DataFrame(rows)


def opex_df(cost):
    rows = [{"Item": n, "Cost ($/year)": v} for n, v in cost["opex_items"]]
    rows.append({"Item": "TOTAL OPEX", "Cost ($/year)": cost["total_opex"]})
    return pd.DataFrame(rows)


def unit_cost_df(cost):
    return kv_df([
        ("Annual treated volume", cost["annual_m3"], "m³/year", ",.0f"),
        ("OPEX per m³", cost["opex_per_m3"], "$/m³", ".3f"),
        ("Annualized CAPEX", cost["annualized_capex"], "$/year", ",.0f"),
        ("Total treatment cost (OPEX + annualized CAPEX)", cost["total_per_m3"], "$/m³", ".3f"),
        ("Installed power (pumps, mixers, blowers)", cost["power"]["installed_kw"], "kW", ",.0f"),
        ("Average running power", cost["power"]["running_kw"], "kW", ",.0f"),
        ("Annual energy", cost["power"]["kwh_year"], "kWh/year", ",.0f"),
    ])


def power_by_group_df(rows):
    ps = power_summary(rows)
    out = [{"Area": g, "Installed kW": v["installed_kw"], "Average running kW": v["running_kw"]}
           for g, v in ps["by_group"].items()]
    out.append({"Area": "TOTAL", "Installed kW": ps["installed_kw"],
                "Average running kW": ps["running_kw"]})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ the report
INSTRUMENTS = [
    ("pH-201", "pH", "Reaction tank A (T-201)", "Lime / NaOH dosing pH control"),
    ("pH-202", "pH", "Reaction tank B (T-202)", "pH verification after lime"),
    ("pH-203", "pH", "Reaction tank C (T-203)", "Final precipitation pH before splitter"),
    ("ORP-203", "ORP", "Reaction tank C (T-203)", "Organosulfide dosing control / residual sulfide"),
    ("pH-301", "pH", "Clearwell tank (T-301)", "HCl dosing control (neutralization)"),
    ("H2S-301", "H2S", "Clearwell tank / vent (T-301)", "H2S safety monitor (acid + residual sulfide); interlock HCl pumps"),
    ("pH-601", "pH", "Bio feed tank (T-601)", "Feed pH to bioreactors / nutrient dosing check"),
    ("DO-601", "DO", "Bio feed tank (T-601)", "Dissolved oxygen entering bioreactors (methanol demand)"),
    ("ORP-601", "ORP", "Bioreactor Stage 1 (R-601)", "Reducing (anoxic) condition monitoring"),
    ("H2S-601", "H2S", "Bioreactor Stage 1 (R-601)", "H2S / headspace safety monitor"),
    ("ORP-602", "ORP", "Bioreactor Stage 2 (R-602)", "Reducing condition monitoring"),
    ("DO-602", "DO", "Bioreactor Stage 2 (R-602)", "Dissolved oxygen leaving bioreactors"),
    ("H2S-602", "H2S", "Bioreactor Stage 2 (R-602)", "H2S / headspace safety monitor"),
    ("H2S-701", "H2S", "UF feed sump (T-701)", "H2S safety monitor (sump headspace)"),
    ("pH-801", "pH", "Effluent to NPDES discharge", "Discharge pH compliance monitor"),
]


def instrument_df():
    return pd.DataFrame([{"Tag": t, "Type": k, "Location": loc, "Purpose": pur} for t, k, loc, pur in INSTRUMENTS])


def build_report(project, A):
    d, meta = project["data"], project["meta"]
    R, des, chem, sl, eq, cost = A["R"], A["des"], A["chem"], A["sludge"], A["eq"], A["cost"]
    q_m3h, q_gpm = R["q_m3h"], R["flow_gpm"]
    comp = compliance_df(d, R)
    n_fail = int((comp["Status"] == "FAIL").sum())
    S = {}

    # ---- summary
    S["summary"] = [
        ("table", kv_df([
            ("Design flow", q_gpm, "gpm", ",.0f"), ("Design flow", q_m3h, "m³/h", ",.1f"),
            ("Design flow", R["q_m3d"], "m³/day", ",.0f"),
            ("pH", gf(d, "ph"), "-", ".1f"), ("Temperature", gf(d, "temperature"), "°C", ".1f"),
        ]), "Design basis"),
        ("table", pd.DataFrame({
            "Item": ["Project", "Client", "Location", "Final water use", "Recovery requirement",
                     "Treatment train"],
            "Description": [meta.get("name", ""), meta.get("client", ""), meta.get("location", ""),
                            gs(d, "final_water_use"), gs(d, "recovery_requirement"),
                            "Equalization A/B -> Reaction Tanks A-C (lime / FeCl3 / polymer / organosulfide) -> "
                            "Clarifiers A/B -> Clearwell (HCl) -> Sand Filters -> Bio Feed Tank (nutrient) -> "
                            "Bioreactor Stage 1 -> Stage 2 -> UF -> UV -> NPDES discharge"],
        }), "Project information"),
        ("text", f"Overall result: the plant {'meets all permit limits' if n_fail == 0 else f'FAILS {n_fail} of {len(comp)} permit limits'}. "
                 f"Total CAPEX ${cost['total_capex']:,.0f}; annual OPEX ${cost['total_opex']:,.0f}; "
                 f"total treatment cost ${cost['total_per_m3']:.2f}/m³."),
    ]

    # ---- description
    b, u = R["bio"], des["uf"]
    S["description"] = [
        ("text", f"Equalization: raw FGD wastewater ({q_gpm:,.0f} gpm), including returns from the outdoor sump "
                 f"(gypsum dewatering, sludge dewatering, leachate, backwash and reject streams), is buffered "
                 f"in {des['eq']['n']} mixed equalization tank(s) A/B ({des['eq']['each']:,.0f} m³ each, "
                 f"{gf(d, 'eq_hrt_hr'):.1f} h total) and pumped forward by the wastewater treatment feed pump "
                 f"skid (P-101)."),
        ("text", f"Reaction tanks: three mixed tanks A, B and C operate in series by overflow. Lime "
                 f"({gf(d, 'lime_dose'):.0f} mg/L), NaOH ({gf(d, 'naoh_dose'):.0f} mg/L), FeCl3 "
                 f"({gf(d, 'fecl3_dose'):.0f} mg/L), polymer ({gf(d, 'polymer_dose'):.1f} mg/L) and organosulfide "
                 f"({gf(d, 'organosulfide_dose'):.0f} mg/L) are dosed to precipitate hardness, metals and "
                 f"suspended solids. Sand filter overflow returns to Reaction Tank A."),
        ("text", f"Clarifiers: a splitter distributes flow to {des['cl']['n']} clarifier(s), "
                 f"{des['cl']['diameter']:.1f} m diameter at {gf(d, 'clarifier_sor'):.1f} m/h surface overflow "
                 f"rate. Underflow is pumped by the clarifier sludge pump skid (P-301) to the sludge tanks."),
        ("text", f"Clearwell: clarified water flows to the clearwell tank (T-301), where HCl "
                 f"({gf(d, 'hcl_dose'):.0f} mg/L) is mixed in to neutralize pH; the HCl tank vent goes to a "
                 f"vapor scrubber."),
        ("text", f"Sand filtration: the sand filter feed pump skid (P-401) feeds {des['sf']['n']} filters at "
                 f"{gf(d, 'sf_filtration_rate'):.0f} m/h. Backwash water (T-402/P-402) and air scour (B-401) "
                 f"clean the media; backwash waste goes to the outdoor sump."),
        ("text", f"Biological treatment: filtered water enters the bio feed tank (T-601), where nutrient (H3PO4) is "
                 f"mixed in from the nutrient tank (T-606) by the nutrient injection pump skid (P-607, 2 working + 1 spare). The bio feed pump skid (P-601) sends it through the bio feed strainer skid (S-601) "
                 f"and heat exchanger (HX-601) to the Stage 1 flow control skid, then Stage 1 "
                 f"({des['bio']['n1']} reactors) and Stage 2 ({des['bio']['n2']} reactors), {b['total_hrt']:.1f} h "
                 f"total HRT, with methanol ({gf(d, 'methanol_dose'):.0f} mg/L) to reduce nitrate and selenium."),
        ("text", f"Ultrafiltration: Stage 2 effluent passes the Stage 2 flow control skid into the UF feed sump "
                 f"(T-701); sump pumps (P-707) and UF feed pumps (P-701) feed {u['trains']} UF skids "
                 f"({u['modules']} modules, {u['area']:,.0f} m²) at {gf(d, 'uf_flux'):.0f} LMH. Permeate "
                 f"flows to the effluent / backwash tank (T-702); the backwash / effluent pump skid supplies "
                 f"UF and bioreactor backwash water and sends effluent through UV (UV-801) to the NPDES "
                 f"discharge. Reject and backwash waste return to the outdoor sump."),
        ("text", "Sludge handling: clarifier underflow is held in sludge tanks A/B, pumped by the filter press feed "
                 "pump skid (P-901) to the vacuum dewatering filter presses A/B/C; filtrate returns to the EQ "
                 "tanks and cake is discharged to disposal."),
    ]

    # ---- influent
    S["influent"] = [("table", influent_df(d, R), "Influent water characteristics")]

    # ---- pfd
    S["pfd"] = [("pfd",)]

    # ---- units
    def unit(name, key, stage, params, extra=None):
        blocks = [("h2", name), ("table", kv_df(des[key]["rows"]), f"{name} - design")]
        if extra is not None:
            blocks.append(extra)
        if stage:
            blocks.append(("table", _removal_df(R, stage, params), f"{name} - performance"))
        return blocks

    units = [("h2", "Equalization")]
    units += [("table", kv_df(des["eq"]["rows"]), "Equalization tank")]
    units += unit("Reaction Tank", "rt", "rt",
                  ["tss", "hardness", "iron", "aluminum", "copper", "zinc", "nickel", "lead",
                   "cadmium", "arsenic", "selenium", "mercury"],
                  ("table", pd.DataFrame({
                      "Chemical": ["Lime", "NaOH", "FeCl3", "Polymer", "Organosulfide"],
                      "Dose (mg/L)": [gf(d, "lime_dose"), gf(d, "naoh_dose"), gf(d, "fecl3_dose"),
                                      gf(d, "polymer_dose"), gf(d, "organosulfide_dose")]}),
                   "Reaction tanks - chemical doses"))
    units += unit("Clarifier", "cl", "cl", ["tss", "arsenic", "selenium", "mercury", "lead", "cadmium"])
    units += unit("Sand Filter", "sf", "sf", ["tss", "arsenic", "selenium", "mercury", "lead", "cadmium"])
    units += unit("Organosulfide", "os", "os", ["mercury", "cadmium", "lead", "arsenic", "selenium"])
    units += unit("Bioreactor", "bio", "bio", ["selenium", "nitrate", "nitrite", "ammonia", "bod", "cod"],
                  ("table", kv_df([
                      ("Base Se removal", b["base_se_removal"], "%", ".1f"),
                      ("Adjusted (actual) Se removal", b["actual_se_removal"], "%", ".1f"),
                      ("HRT factor", b["hrt_factor"], "-", ".2f"),
                      ("Loading factor", b["loading_factor"], "-", ".2f"),
                      ("Nitrate factor", b["nitrate_factor"], "-", ".2f"),
                      ("Temperature factor", b["temp_factor"], "-", ".2f"),
                      ("Carbon (methanol) factor", b["carbon_factor"], "-", ".2f"),
                      ("Selenium load", b["se_load_kg_day"], "kg/day", ".1f"),
                      ("Methanol dose", gf(d, "methanol_dose"), "mg/L", ".1f"),
                      ("Required methanol (indicative)", b["required_methanol"], "mg/L", ".1f"),
                  ]), "Bioreactor - selenium performance factors"))
    units += unit("UF System", "uf", "uf", ["tss", "arsenic", "selenium", "mercury", "bod", "cod"])
    S["units"] = units

    # ---- quality
    S["quality"] = [("table", stage_profile_df(R), "Concentration after each unit (mg/L)"),
                    ("text", "Concentrations are steady-state, flow-weighted values; internal recycle "
                             "streams (backwash waste, UF reject) are assumed to be handled within the "
                             "removal efficiencies entered for each unit.")]

    # ---- equipment
    pumps, tanks, others = equipment_frames(eq, include_group=True)
    S["equipment"] = [
        ("h2", "Pumps"), ("table", pumps, "Pump schedule"),
        ("h2", "Tanks & Vessels"), ("table", tanks, "Tank / vessel schedule"),
        ("h2", "Mixers, Blowers & Mechanical Equipment"), ("table", others, "Mechanical equipment"),
        ("h2", "Online Instruments"), ("table", instrument_df(), "Online analyzers (pH / ORP / H2S / DO)"),
        ("h2", "Power Summary"), ("table", power_by_group_df(eq), "Connected and average power"),
    ]

    # ---- chemicals
    S["chemicals"] = [("table", chem_df(chem), "Chemical consumption, storage and cost"),
                      ("text", f"Total chemical cost: ${cost['chem_year'] / 365:,.0f}/day, "
                               f"${cost['chem_year'] / 12:,.0f}/month, ${cost['chem_year']:,.0f}/year.")]

    # ---- sludge
    S["sludge"] = [("table", sludge_df(sl, d), "Dry solids by source"),
                   ("table", sludge_summary_df(sl, d), "Sludge and cake summary")]

    # ---- cost
    S["cost"] = [("table", capex_df(cost), "Capital cost"), ("table", opex_df(cost), "Operating cost"),
                 ("table", unit_cost_df(cost), "Unit cost and energy")]

    # ---- effluent
    final_all = pd.DataFrame([{"Parameter": PARAM_INFO[p][0], "Unit": PARAM_INFO[p][1],
                               "Influent": R["stream"]["inf"][p], "Final effluent": R["final"][p],
                               "Overall removal (%)": ((R["stream"]["inf"][p] - R["final"][p]) /
                                                       R["stream"]["inf"][p] * 100
                                                       if R["stream"]["inf"][p] > 0 else 0.0)}
                              for p in TRAIN_PARAMS])
    untreated = pd.DataFrame([{"Parameter": PARAM_INFO[p][0], "Unit": PARAM_INFO[p][1],
                               "Value (unchanged)": gf(d, p)} for p in UNTREATED_PARAMS
                              if p not in ("ph", "temperature")])
    S["effluent"] = [
        ("table", comp, "Permit compliance"),
        ("table", final_all, "Final effluent - all modelled parameters"),
        ("table", untreated, "Parameters not treated by this train (assumed unchanged)"),
        ("text", f"Treated water flow (UF permeate): {des['uf']['permeate']:,.1f} m³/h "
                 f"({des['uf']['permeate'] / 0.2271:,.0f} gpm)."),
    ]

    # ---- notes
    S["notes"] = [("text", t) for t in [
        "Removal efficiencies for the reaction tanks follow the FeCl3 / lime / polymer response curves "
        "built into the model, combined multiplicatively with the organosulfide removal; all other units "
        "use the removal percentages entered on their pages.",
        "Bioreactor selenium removal = base removal x HRT x loading x nitrate x temperature x carbon factors; "
        "the temperature used is the higher of the influent temperature and the heat-exchanger target.",
        "Pump power = Q x H x SG / (367 x efficiency); motors selected from IEC standard sizes with a "
        "15% margin. Mixer power = G² x µ x V. Blower power by adiabatic compression.",
        "Pump flows include the global design margin; tanks include the global freeboard allowance.",
        "Chemical tank sizes are based on storage days at the stated delivered strength; "
        "UF CEB chemical tank and pump are placeholders (allowance) pending vendor data. "
        "HCl dose, UV energy and outdoor sump flow are placeholder values.",
        "Costs are order-of-magnitude budget estimates (+/-30%); pump and tank costs use the unit rates "
        "entered on the CAPEX & OPEX page.",
        "Backwash waste, strainer waste and UF reject streams are returned via the outdoor sump to the EQ "
        "tanks; sludge from these returns is included in the clarifier sludge mass balance, not counted twice.",
    ]]

    sections = []
    for sid, title in SECTION_TITLES:
        sections.append({"id": sid, "title": title, "blocks": S[sid]})
    return sections


def _removal_df(R, stage, params):
    rows = []
    for p in params:
        rows.append({"Parameter": PARAM_INFO[p][0], "Unit": PARAM_INFO[p][1],
                     "Feed": R["feed"][stage][p], "Removal (%)": R["removal"][stage][p],
                     "Effluent": R["stream"][stage][p]})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ Excel
def report_to_excel(sections):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for sec in sections:
            sheet = sec["title"][:28].replace("&", "and").replace("/", "-").replace(":", "")
            row = 0
            wrote = False
            for blk in sec["blocks"]:
                if blk[0] == "table" and blk[1] is not None and len(blk[1]):
                    blk[1].to_excel(xw, sheet_name=sheet, startrow=row + 1, index=False)
                    ws = xw.sheets[sheet]
                    ws.cell(row=row + 1, column=1, value=blk[2])
                    row += len(blk[1]) + 4
                    wrote = True
            if wrote:
                ws = xw.sheets[sheet]
                for col in ws.columns:
                    width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                    ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)
        if not xw.sheets:
            pd.DataFrame({"info": ["no data"]}).to_excel(xw, sheet_name="Report", index=False)
    return buf.getvalue()


# ------------------------------------------------------------------ single-unit sections (page PDFs)
UNIT_META = {
    "rt": ("Reaction Tank", "rt", "rt", ["tss", "hardness", "iron", "aluminum", "copper", "zinc", "nickel",
                                         "lead", "cadmium", "arsenic", "selenium", "mercury"], G_RT),
    "cl": ("Clarifier", "cl", "cl", ["tss", "arsenic", "selenium", "mercury", "lead", "cadmium"], G_CL),
    "sf": ("Sand Filter", "sf", "sf", ["tss", "arsenic", "selenium", "mercury", "lead", "cadmium"], G_SF),
    "os": ("Organosulfide System", "os", "os", ["mercury", "cadmium", "lead", "arsenic", "selenium"], G_OS),
    "bio": ("Bioreactor", "bio", "bio", ["selenium", "nitrate", "nitrite", "ammonia", "bod", "cod"], G_BIO),
    "uf": ("UF System", "uf", "uf", ["tss", "arsenic", "selenium", "mercury", "bod", "cod"], G_UF),
}


def unit_section(project, A, key):
    """Design + performance + pumps/tanks for one unit (used by the per-page PDF buttons)."""
    d, R = project["data"], A["R"]
    if key == "eq":
        blocks = [("table", kv_df(A["des"]["eq"]["rows"]), "Equalization tank - design"),
                  ("table", influent_df(d, R), "Influent water quality")]
        title, group = "Influent & Equalization", G_INF
    elif key == "cw":
        blocks = [("table", chem_df({"hcl": A["chem"]["hcl"]}), "HCl dosing")]
        title, group = "Clearwell", G_CW
    elif key == "bf":
        blocks = [("table", chem_df({"nutrient": A["chem"]["nutrient"]}), "Nutrient dosing")]
        title, group = "Bio Feed & Nutrient", G_BF
    elif key == "sludge":
        blocks = [("table", sludge_df(A["sludge"], d), "Dry solids by source"),
                  ("table", sludge_summary_df(A["sludge"], d), "Sludge and cake summary")]
        title, group = "Sludge Handling", G_SLG
    else:
        title, des_key, stage, params, group = UNIT_META[key]
        blocks = [("table", kv_df(A["des"][des_key]["rows"]), f"{title} - design"),
                  ("table", _removal_df(R, stage, params), f"{title} - performance")]
    pumps, tanks, others = equipment_frames(A["eq"], group)
    blocks += [("h2", "Pumps, Tanks & Mixers"), ("table", pumps, "Pumps"),
               ("table", tanks, "Tanks / vessels"), ("table", others, "Mixers & mechanical equipment")]
    return {"id": key, "title": title, "blocks": blocks}


def pick(sections, ids):
    return [s for s in sections if s["id"] in ids]


# ======================================================================
# PFD  (pictorial, P&ID-style SVG)   -- PFD BEGIN
# ======================================================================
PFD_W, PFD_H = 1640, 1420
PFD_FS = 1.3          # font scale used for every label in the flow diagram only

_PFD_DEFS = """<defs>
<linearGradient id="gTank" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#4F7BA3"/><stop offset=".35" stop-color="#B7D3EC"/><stop offset=".7" stop-color="#8FB4D6"/><stop offset="1" stop-color="#46698F"/></linearGradient>
<linearGradient id="gVessel" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#8EA6BC"/><stop offset=".35" stop-color="#EAF1F7"/><stop offset=".7" stop-color="#B5C7D8"/><stop offset="1" stop-color="#7C93A9"/></linearGradient>
<linearGradient id="gMem" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F6B98A"/><stop offset=".5" stop-color="#E07B39"/><stop offset="1" stop-color="#B05A1C"/></linearGradient>
<linearGradient id="gMotor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9CA3AF"/><stop offset="1" stop-color="#374151"/></linearGradient>
<radialGradient id="gPump" cx=".4" cy=".35" r=".8"><stop offset="0" stop-color="#93C5FD"/><stop offset="1" stop-color="#1D4ED8"/></radialGradient>
<marker id="mArr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="3" markerHeight="3" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1E5F8E"/></marker>
<marker id="mArrT" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="3" markerHeight="3" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#0E7490"/></marker>
<marker id="mArrD" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1E5F8E"/></marker>
</defs>"""

_PIPE = {"main": ("#1E5F8E", "#4DA6E0"), "treated": ("#0E7490", "#67E8F9"),
         "sludge": ("#5B4636", "#B08968")}
_BLUE, _RED, _BROWN = "#1E40AF", "#991B1B", "#7C2D12"


def _tx(x, y, s, size=10, anchor="middle", weight="normal", fill="#1F2937"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size * PFD_FS:.1f}" text-anchor="{anchor}" '
            f'font-weight="{weight}" fill="{fill}">{escape(str(s))}</text>')


def _chevrons(pts):
    out = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 70:
            continue
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        for f in ((0.3, 0.75) if L > 120 else (0.5,)):
            out.append(f'<path d="M-5,-5 L5,0 L-5,5 z" fill="#FFFFFF" fill-opacity=".9" '
                       f'transform="translate({x1 + (x2 - x1) * f:.1f},{y1 + (y2 - y1) * f:.1f}) '
                       f'rotate({ang:.1f})"/>')
    return "".join(out)


def _pipe(pts, kind="main", arrow=False):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    if kind == "recycle":
        return (f'<path d="{d}" fill="none" stroke="#1E5F8E" stroke-width="3" stroke-dasharray="9 6" '
                f'stroke-linejoin="round"' + (' marker-end="url(#mArrD)"' if arrow else "") + "/>")
    dark, light = _PIPE[kind]
    mk = f' marker-end="url(#mArr{"T" if kind == "treated" else ""})"' if arrow else ""
    return (f'<path d="{d}" fill="none" stroke="{dark}" stroke-width="10" stroke-linejoin="round"/>'
            f'<path d="{d}" fill="none" stroke="{light}" stroke-width="6" stroke-linejoin="round"{mk}/>'
            + _chevrons(pts))


def _lines(x, y_first, lines, size=10, fill="#0F2A44"):
    return "".join(_tx(x, y_first + i * (size * PFD_FS + 3), ln, size, weight="bold" if i == 0 else "normal",
                       fill=fill) for i, ln in enumerate(lines))


def _tank(x, y, w, h, lines, size=10):
    ry, cx = 8, x + w / 2
    s = (f'<path d="M{x},{y + ry} L{x},{y + h - ry} A{w / 2},{ry} 0 0 0 {x + w},{y + h - ry} '
         f'L{x + w},{y + ry} Z" fill="url(#gTank)" stroke="#1E3A5F" stroke-width="1.5"/>'
         f'<ellipse cx="{cx}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" fill="#D6E8F7" stroke="#1E3A5F" '
         f'stroke-width="1.5"/>')
    return s + _lines(cx, y + h - 14 - (len(lines) - 1) * (size * PFD_FS + 3), lines, size)


def _vessel(x, y, w, h, lines, size=10):
    cx = x + w / 2
    s = (f'<rect x="{x + 8}" y="{y + h - 6}" width="10" height="14" fill="#6B7280" stroke="#374151"/>'
         f'<rect x="{x + w - 18}" y="{y + h - 6}" width="10" height="14" fill="#6B7280" stroke="#374151"/>'
         f'<rect x="{cx - 6}" y="{y - 7}" width="12" height="9" fill="#374151"/>'
         f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{w * 0.4:.0f}" fill="url(#gVessel)" '
         f'stroke="#334155" stroke-width="1.5"/>')
    return s + _lines(cx, y + h / 2 - (len(lines) - 1) * (size * PFD_FS + 3) / 2 + 4, lines, size)


def _mixer(cx, ytop, depth, tag=None):
    s = (f'<rect x="{cx - 9}" y="{ytop - 24}" width="18" height="12" rx="2" fill="url(#gMotor)" stroke="#111827"/>'
         f'<rect x="{cx - 4}" y="{ytop - 12}" width="8" height="8" fill="#9CA3AF" stroke="#374151"/>'
         f'<line x1="{cx}" y1="{ytop - 4}" x2="{cx}" y2="{ytop + depth}" stroke="#374151" stroke-width="2.5"/>'
         f'<rect x="{cx - 13}" y="{ytop + depth - 2}" width="26" height="4" rx="1" fill="#374151"/>')
    return s + (_tx(cx + 13, ytop - 14, tag, 8, "start", fill="#374151") if tag else "")


def _gauge(cx, cy, pipe_y):
    return (f'<line x1="{cx}" y1="{cy + 7}" x2="{cx}" y2="{pipe_y}" stroke="#374151" stroke-width="2"/>'
            f'<circle cx="{cx}" cy="{cy}" r="7" fill="#FFFFFF" stroke="#111827" stroke-width="1.5"/>'
            f'<line x1="{cx}" y1="{cy}" x2="{cx + 3}" y2="{cy - 4}" stroke="#DC2626" stroke-width="1.5"/>')


def _meter(x, y, label, pipe_y):
    return (f'<line x1="{x + 12}" y1="{y + 14}" x2="{x + 12}" y2="{pipe_y}" stroke="#374151" stroke-width="2"/>'
            f'<rect x="{x}" y="{y}" width="24" height="14" rx="2" fill="#7CB342" stroke="#33691E"/>'
            + _tx(x + 12, y + 10.5, label, 8, weight="bold", fill="#0B2E0B"))


def _pump(cx, cy, tag, gauge=True, vertical=False):
    s = _gauge(cx + 27, cy - 30, cy) if gauge else ""
    s += (f'<rect x="{cx - 9}" y="{cy - 30}" width="18" height="18" rx="2" fill="url(#gMotor)" stroke="#111827"/>'
          f'<line x1="{cx - 5}" y1="{cy - 26}" x2="{cx + 5}" y2="{cy - 26}" stroke="#111827"/>'
          f'<line x1="{cx - 5}" y1="{cy - 21}" x2="{cx + 5}" y2="{cy - 21}" stroke="#111827"/>'
          f'<rect x="{cx - 3}" y="{cy - 12}" width="6" height="6" fill="#9CA3AF" stroke="#374151"/>'
          f'<circle cx="{cx}" cy="{cy}" r="12" fill="url(#gPump)" stroke="#1E3A8A" stroke-width="1.5"/>'
          f'<circle cx="{cx}" cy="{cy}" r="4" fill="#FFFFFF" opacity=".85"/>')
    s += (_tx(cx + 18, cy + 4, tag, 9, "start", "bold", "#1E3A8A") if vertical
          else _tx(cx, cy + 27, tag, 9, weight="bold", fill="#1E3A8A"))
    return s


def _dose(x, y0, y1, name, tag):
    return (f'<path d="M{x - 3},{y0} L{x + 3},{y0} L{x + 3},{y1 - 12} L{x + 8},{y1 - 12} L{x},{y1} '
            f'L{x - 8},{y1 - 12} L{x - 3},{y1 - 12} Z" fill="#DC2626" stroke="#7F1D1D"/>'
            + _tx(x, y0 - 14, name, 12, weight="bold", fill=_RED) + _tx(x, y0 - 3, tag, 8, fill="#7F1D1D"))


def _membrane(x, y, w, h, label):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2}" fill="url(#gMem)" '
            f'stroke="#7C2D12" stroke-width="1.5"/>'
            f'<rect x="{x + 12}" y="{y}" width="4" height="{h}" fill="#7C2D12" opacity=".45"/>'
            f'<rect x="{x + w - 16}" y="{y}" width="4" height="{h}" fill="#7C2D12" opacity=".45"/>'
            + _tx(x + w / 2, y + h / 2 + 4, label, 11, weight="bold", fill="#FFFFFF"))


def _legend(x, y):
    s = (f'<rect x="{x}" y="{y}" width="960" height="128" rx="4" fill="#FFFFFF" stroke="#5B3E8F" stroke-width="2.5"/>'
         + _tx(x + 14, y + 20, "Caption:", 12, "start", "bold", "#5B3E8F"))
    c = [x + 24, x + 264, x + 504, x + 744]
    r = [y + 44, y + 68, y + 92, y + 116]

    def lab(ci, ri, t):
        return _tx(c[ci] + 38, r[ri] + 3, t, 10, "start")

    # column 1: equipment
    s += (f'<circle cx="{c[0] + 12}" cy="{r[0]}" r="7" fill="url(#gPump)" stroke="#1E3A8A"/>'
          f'<rect x="{c[0] + 8}" y="{r[0] - 17}" width="8" height="8" fill="url(#gMotor)" stroke="#111827"/>' + lab(0, 0, "Pump (motor + casing)"))
    s += (f'<rect x="{c[0] + 3}" y="{r[1] - 6}" width="18" height="14" fill="url(#gTank)" stroke="#1E3A5F"/>'
          f'<ellipse cx="{c[0] + 12}" cy="{r[1] - 6}" rx="9" ry="3" fill="#D6E8F7" stroke="#1E3A5F"/>' + lab(0, 1, "Tank / basin (mixed)"))
    s += (f'<path d="M{c[0] + 10},{r[2] - 8} L{c[0] + 14},{r[2] - 8} L{c[0] + 14},{r[2]} L{c[0] + 18},{r[2]} '
          f'L{c[0] + 12},{r[2] + 7} L{c[0] + 6},{r[2]} L{c[0] + 10},{r[2]} Z" fill="#DC2626" stroke="#7F1D1D"/>' + lab(0, 2, "Chemical dosing"))
    s += (f'<rect x="{c[0] + 3}" y="{r[3] - 6}" width="18" height="12" rx="6" fill="url(#gVessel)" stroke="#334155"/>' + lab(0, 3, "Vessel / filter / reactor"))
    # column 2: lines
    for i, (kind, t) in enumerate([("main", "Process water"), ("treated", "Treated water (permeate)"),
                                   ("sludge", "Sludge line")]):
        dk, lt = _PIPE[kind]
        s += (f'<line x1="{c[1]}" y1="{r[i]}" x2="{c[1] + 28}" y2="{r[i]}" stroke="{dk}" stroke-width="10"/>'
              f'<line x1="{c[1]}" y1="{r[i]}" x2="{c[1] + 28}" y2="{r[i]}" stroke="{lt}" stroke-width="6"/>' + lab(1, i, t))
    s += (f'<line x1="{c[1]}" y1="{r[3]}" x2="{c[1] + 28}" y2="{r[3]}" stroke="#1E5F8E" stroke-width="3" '
          f'stroke-dasharray="7 4"/>' + lab(1, 3, "Recycle / backwash return"))
    # column 3: instruments
    s += (f'<rect x="{c[2]}" y="{r[0] - 7}" width="24" height="14" rx="2" fill="#7CB342" stroke="#33691E"/>'
          + _tx(c[2] + 12, r[0] + 3, "FIT", 8, weight="bold", fill="#0B2E0B") + lab(2, 0, "Flow meter (FIT)"))
    s += (f'<rect x="{c[2]}" y="{r[1] - 7}" width="24" height="14" rx="2" fill="#7CB342" stroke="#33691E"/>'
          + _tx(c[2] + 12, r[1] + 3, "pH", 8, weight="bold", fill="#0B2E0B") + lab(2, 1, "pH meter"))
    s += (f'<circle cx="{c[2] + 12}" cy="{r[2]}" r="7" fill="#FFFFFF" stroke="#111827" stroke-width="1.5"/>'
          f'<line x1="{c[2] + 12}" y1="{r[2]}" x2="{c[2] + 15}" y2="{r[2] - 4}" stroke="#DC2626" stroke-width="1.5"/>' + lab(2, 2, "Pressure gauge"))
    s += (f'<rect x="{c[2]}" y="{r[3] - 7}" width="26" height="14" rx="7" fill="url(#gMem)" stroke="#7C2D12"/>' + lab(2, 3, "UF membrane module"))
    for i, (k, t) in enumerate([("ORP", "ORP analyzer"), ("H2S", "H2S gas monitor"), ("DO", "DO analyzer")]):
        fill, stroke, txt = _INST[k]
        s += (f'<rect x="{c[3]}" y="{r[i] - 7}" width="30" height="14" rx="3" fill="{fill}" stroke="{stroke}"/>'
              + _tx(c[3] + 15, r[i] + 3, k, 8, weight="bold", fill=txt) + lab(3, i, t))
    return s


_PFD_HL = {"eq": (90, 185, 195, 185), "rt": (410, 100, 350, 280), "os": (410, 100, 350, 280),
           "cl": (850, 100, 150, 420), "cw": (1090, 110, 120, 260), "sf": (1235, 200, 215, 190),
           "bf": (1200, 495, 415, 400), "bio": (680, 620, 510, 320), "uf": (90, 970, 930, 200),
           "sludge": (250, 455, 590, 205)}


def _highlight(key):
    if key not in _PFD_HL:
        return ""
    x, y, w, h = _PFD_HL[key]
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#FDE68A" fill-opacity=".35" '
            f'stroke="#F59E0B" stroke-width="4" stroke-dasharray="14 7"/>')


_INST = {"pH": ("#7CB342", "#33691E", "#0B2E0B"), "ORP": ("#A78BFA", "#5B21B6", "#FFFFFF"),
         "H2S": ("#F97316", "#9A3412", "#FFFFFF"), "DO": ("#22D3EE", "#0E7490", "#083344")}


def _inst(cx, y, kind, tag, y_target):
    """Online analyzer: coloured box + tag, sensing line to the tank / vessel / pipe at y_target."""
    fill, stroke, txt = _INST[kind]
    up = y_target < y
    y0, y1 = (y, y_target) if up else (y + 14, y_target)
    ty = y + 24 if up else y - 3
    return (f'<line x1="{cx}" y1="{y0}" x2="{cx}" y2="{y1}" stroke="#374151" stroke-width="2"/>'
            f'<rect x="{cx - 15}" y="{y}" width="30" height="14" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            + _tx(cx, y + 10.5, kind, 8, weight="bold", fill=txt)
            + _tx(cx, ty, tag, 7, fill="#374151"))


def _skid(x, y, w, h, lines, fill="#E5E7EB", size=9):
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{fill}" stroke="#374151" '
         f'stroke-width="1.5"/>'
         f'<rect x="{x + 6}" y="{y + h}" width="8" height="7" fill="#6B7280"/>'
         f'<rect x="{x + w - 14}" y="{y + h}" width="8" height="7" fill="#6B7280"/>')
    return s + _lines(x + w / 2, y + h / 2 - (len(lines) - 1) * (size * PFD_FS + 3) / 2 + 3, lines, size)


def _clarifier(x, y, w, lines):
    cx = x + w / 2
    return (f'<rect x="{x}" y="{y + 40}" width="{w}" height="90" fill="url(#gTank)" stroke="#1E3A5F" stroke-width="1.5"/>'
            f'<polygon points="{x},{y + 130} {x + w},{y + 130} {cx},{y + 175}" fill="#6F97BE" stroke="#1E3A5F" stroke-width="1.5"/>'
            f'<rect x="{x}" y="{y + 40}" width="{w}" height="6" fill="#D6E8F7" stroke="#1E3A5F"/>'
            f'<rect x="{cx - 12}" y="{y + 16}" width="24" height="16" rx="2" fill="url(#gMotor)" stroke="#111827"/>'
            f'<line x1="{cx}" y1="{y + 32}" x2="{cx}" y2="{y + 150}" stroke="#374151" stroke-width="3"/>'
            f'<line x1="{cx - 38}" y1="{y + 146}" x2="{cx + 38}" y2="{y + 146}" stroke="#374151" stroke-width="3"/>'
            + _lines(cx, y + 66, lines))


def pfd_svg(A, d, scale=1.0, highlight=None):
    R, chem, des = A["R"], A["chem"], A["des"]
    S = R["stream"]
    se = lambda k: f"Se {fmt_num(S[k]['selenium'])} mg/L"
    eq, rt, cl, sf, b, u = des["eq"], des["rt"], des["cl"], des["sf"], des["bio"], des["uf"]
    two = cl["n"] >= 2
    h1, h2 = gf(d, "bio_stage1_hrt"), gf(d, "bio_stage2_hrt")
    P, E = [], []

    # ------------------------------------------------------------ process pipes
    P += [_pipe([(45, 300), (100, 300)], arrow=True),
          _pipe([(275, 300), (808, 300)]),
          _pipe([(808, 300), (835, 300), (835, 195), (860, 195)])]
    if two:
        P.append(_pipe([(835, 300), (835, 420), (860, 420)]))
    P.append(_pipe([(990, 170), (1030, 170), (1030, 300), (1100, 300)]))
    if two:
        P.append(_pipe([(990, 395), (1030, 395), (1030, 300)]))
    P += [_pipe([(1200, 300), (1340, 300)]),
          _pipe([(1440, 300), (1610, 300), (1610, 800), (1580, 800)]),
          _pipe([(1480, 800), (150, 800), (150, 985)]),
          _pipe([(190, 1060), (395, 1060)]), _pipe([(395, 1045), (395, 1085)]),
          _pipe([(395, 1045), (420, 1045)]), _pipe([(395, 1085), (420, 1085)]),
          _pipe([(580, 1045), (605, 1045), (605, 1085), (580, 1085)], "treated"),
          _pipe([(605, 1065), (690, 1065)], "treated"),
          _pipe([(800, 1065), (1150, 1065)], "treated", arrow=True)]
    # sludge
    P.append(_pipe([(925, 285), (925, 325), (800, 325), (800, 550)], "sludge"))
    if two:
        P.append(_pipe([(925, 510), (925, 550), (800, 550)], "sludge"))
    P.append(_pipe([(800, 550), (400, 550)], "sludge"))
    # recycle / returns
    P += [_pipe([(1390, 213), (1390, 70), (428, 70), (428, 230)], "recycle", True),
          _pipe([(260, 550), (70, 550)], "recycle"),
          _pipe([(70, 1160), (70, 300), (100, 300)], "recycle", True),
          _pipe([(440, 1102), (440, 1195), (110, 1195)], "recycle", True),
          _pipe([(850, 1080), (850, 1150), (500, 1150), (500, 1102)], "recycle", True),
          _pipe([(830, 1065), (830, 935)], "recycle", True)]

    # ------------------------------------------------------------ influent / EQ A-B
    E.append(_tx(60, 268, "Influent", 12, weight="bold", fill="#003366"))
    E.append(_tx(60, 282, f"{R['flow_gpm']:,.0f} gpm", 9))
    for i in range(min(eq["n"], 2)):
        x = 100 + i * 95
        E.append(_tank(x, 220, 80, 140, [f"T-101{'AB'[i]}", "EQ tank", f"{eq['each']:,.0f} m³"], 9))
        E.append(_mixer(x + 40, 220, 80))
    E.append(_pump(320, 300, "P-101"))
    E.append(_meter(385, 268, "FIT", 300))

    # ------------------------------------------------------------ reaction tanks A > B > C
    vols = (rt["v_react"], rt["v_rapid"], rt["v_floc"])
    for i, x in enumerate((420, 540, 660)):
        E.append(_tank(x, 230, 90, 130, [f"T-20{i + 1}", f"RT {'ABC'[i]}", f"{vols[i]:,.0f} m³"], 9))
        E.append(_mixer(x + 25, 230, 80))
        E.append(_dose(x + 70, 160, 228, "", ""))
    E.append('<line x1="490" y1="160" x2="730" y2="160" stroke="#DC2626" stroke-width="4"/>')
    E.append('<line x1="610" y1="140" x2="610" y2="160" stroke="#DC2626" stroke-width="4"/>')
    E.append(_tx(610, 118, "Lime · FeCl3 · Polymer · Organosulfide", 12, weight="bold", fill=_RED))
    E.append(_tx(610, 134, "T-204 · T-206 · T-207 · T-208 (1 duty + 1 standby)", 9, fill="#7F1D1D"))
    E.append(_tx(585, 428, se("rt"), 11, weight="bold", fill=_BLUE))
    E.append(_tx(585, 444, "Reaction Tanks A → B → C (overflow)", 9, fill="#475569"))
    E.append(_skid(772, 282, 40, 36, ["SPL"], size=8))
    E.append(_tx(792, 336, "SPL-301", 9))

    # ------------------------------------------------------------ clarifiers A / B
    nm = f" x{cl['n']}" if cl["n"] > 2 else ""
    E.append(_clarifier(860, 110, 130, ["CL-301 A", "Clarifier", f"Ø {cl['diameter']:.1f} m{nm}"]))
    if two:
        E.append(_clarifier(860, 335, 130, ["CL-301 B", "Clarifier", f"Ø {cl['diameter']:.1f} m"]))
    E.append(_tx(1000, 430, se("cl"), 11, "start", "bold", _BLUE))
    E.append(_tx(862, 575, "Sludge underflow", 9, fill=_BROWN))

    # ------------------------------------------------------------ clearwell (HCl)
    E += [_tank(1100, 230, 100, 130, ["T-301", "Clearwell"], 10), _mixer(1125, 230, 80)]
    if chem["hcl"]["kg_day"] > 0:
        E.append(_dose(1160, 150, 228, "HCl", "T-302 / P-302"))
        E.append(_tx(1160, 112, "Vapor scrubber SCR-302 (vent)", 9, fill="#475569"))
    E.append(_tx(1112, 440, se("cw"), 11, "start", "bold", _BLUE))

    # ------------------------------------------------------------ sand filters
    E += [_pump(1250, 300, "P-401"),
          _vessel(1340, 220, 100, 160, ["SF", "Sand filters", f"{sf['n']} x Ø{sf['diameter']:.1f} m"], 10),
          _tx(1390, 405, se("sf"), 11, weight="bold", fill=_BLUE),
          _tx(1390, 424, "B-401 air · T-402 / P-402 backwash", 9),
          _tx(1390, 438, "BW waste T-403 / P-403 → sump T-102", 9),
          _tx(780, 62, "Recycle: sand filter overflow returns to Reaction Tank A", 9, fill="#1E5F8E")]

    # ------------------------------------------------------------ bio feed tank > strainer > HX > bioreactors
    E += [_tank(1480, 730, 100, 140, ["T-601", "Bio feed tank"], 10), _mixer(1500, 730, 70),
          _pump(1440, 800, "P-601"),
          _skid(1320, 770, 80, 60, ["S-601", "Strainer"]),
          _skid(1210, 770, 80, 60, ["HX-601", "Heat exch."]),
          _skid(1100, 770, 80, 60, ["FCS-601", "Stage 1", "flow ctrl"], size=8),
          _vessel(950, 700, 110, 200, ["R-601", "Stage 1", f"{b['n1']} reactors", f"HRT {h1:.1f} h"], 10),
          _vessel(790, 700, 110, 200, ["R-602", "Stage 2", f"{b['n2']} reactors", f"HRT {h2:.1f} h"], 10),
          _skid(690, 770, 70, 60, ["FCS-602", "Stage 2", "flow ctrl"], size=8),
          _tx(1005, 925, se("bio"), 11, weight="bold", fill=_BLUE),
          _tx(840, 950, "to bioreactor backwash / clearwell T-301", 9, "start", fill="#1E5F8E"),
          _tx(1230, 890, "Recirculation: P-602 / P-603", 9, fill="#475569"),
          _tx(1230, 906, "Stage 1: 1A–1G → Stage 2: 2A–2V", 9, fill="#475569")]
    if chem["nutrient"]["kg_day"] > 0:
        E.append(_dose(1555, 632, 728, "", ""))
        E.append(_tank(1515, 530, 80, 90, ["T-606", "Nutrient"], 9))
        E.append(_tx(1555, 514, "Nutrient (H3PO4)", 10, weight="bold", fill=_RED))
        E.append(_pump(1555, 678, "P-607", gauge=False, vertical=True))
        E.append(_tx(1555, 722, "2 duty + 1 standby", 8, "end", fill="#7F1D1D"))
    if chem["methanol"]["kg_day"] > 0:
        E.append(_dose(1005, 630, 698, "Methanol", "T-605 / P-606"))

    # ------------------------------------------------------------ UF sump > pumps > UF skids > effluent tank > UV
    E += [_tank(100, 980, 90, 120, ["T-701", "UF sump"], 10),
          _pump(240, 1060, "P-707"), _pump(330, 1060, "P-701"),
          _membrane(420, 1030, 160, 30, f"UF skids x{u['trains']}"),
          _membrane(420, 1070, 160, 30, f"{u['modules']} modules"),
          _tx(500, 1018, f"UF: {u['trains']} skids / {u['modules']} modules", 11, weight="bold", fill=_BROWN),
          _tx(500, 1002, se("uf"), 11, weight="bold", fill=_BLUE),
          _tank(690, 1000, 110, 130, ["T-702", "Effluent /", "backwash tank"], 10),
          _pump(850, 1065, "P-801"),
          _skid(920, 1035, 90, 60, ["UV-801", "UV treatment"], fill="#EDE9FE"),
          _meter(1060, 1030, "FIT", 1065),
          _tx(1160, 1058, "NPDES discharge", 12, "start", "bold", "#003366"),
          _tx(1160, 1076, f"{u['permeate']:,.0f} m³/h ({u['permeate'] / GPM_TO_M3H:,.0f} gpm)", 10, "start", "bold"),
          _tx(1160, 1092, f"Final {se('uf')}", 10, "start", "bold", _BLUE),
          _tx(675, 1143, "UF backwash (P-702 / P-801 skid)", 9, fill="#1E5F8E"),
          _tx(270, 1188, "UF reject / BW waste (T-706 / P-705)", 9, fill="#1E5F8E")]

    # ------------------------------------------------------------ sludge tanks > press feed > vacuum filter presses
    plates = "".join(f'<rect x="{272 + i * 14}" y="512" width="8" height="76" fill="#D1D5DB" stroke="#6B7280"/>'
                     for i in range(8))
    E += [_tank(530, 490, 70, 110, ["T-901A", "Sludge"], 9), _mixer(565, 490, 60),
          _tank(620, 490, 70, 110, ["T-901B", "Sludge"], 9), _mixer(655, 490, 60),
          _pump(740, 550, "P-301"), _pump(470, 550, "P-901"),
          '<rect x="260" y="500" width="14" height="100" fill="url(#gMotor)" stroke="#111827"/>',
          '<rect x="386" y="500" width="14" height="100" fill="url(#gMotor)" stroke="#111827"/>',
          '<line x1="260" y1="512" x2="400" y2="512" stroke="#374151" stroke-width="3"/>',
          '<line x1="260" y1="588" x2="400" y2="588" stroke="#374151" stroke-width="3"/>',
          plates,
          _tx(330, 474, "Vacuum filter presses", 9, fill=_BROWN),
          _tx(330, 488, "FP-901 A / B / C", 10, weight="bold", fill=_BROWN),
          '<path d="M327,604 L333,604 L333,622 L338,622 L330,634 L322,622 L327,622 Z" fill="#92400E" stroke="#451A03"/>',
          _tx(330, 652, f"Cake {A['sludge']['cake_kg'] / 1000:,.1f} t/d", 9, fill=_BROWN),
          _tx(330, 666, "to disposal / gypsum storage", 9, fill=_BROWN),
          _pump(180, 550, "P-902", gauge=False),
          _tx(165, 585, "Filtrate → dewatering sump → EQ", 9, fill="#1E5F8E")]

    # ------------------------------------------------------------ outdoor sump
    E += [_tank(30, 1160, 80, 70, ["T-102", "Sump"], 9), _pump(70, 1100, "P-102", gauge=False),
          _tx(120, 1225, "Outdoor sump: gypsum dewatering, leachate, low-volume waste, returns", 9,
              "start", fill="#1E5F8E")]

    # ------------------------------------------------------------ online analyzers: pH / ORP / H2S / DO
    E += [_inst(470, 372, "pH", "pH-201", 345), _inst(590, 372, "pH", "pH-202", 345),
          _inst(685, 372, "pH", "pH-203", 345), _inst(730, 372, "ORP", "ORP-203", 345),
          _inst(1125, 372, "pH", "pH-301", 345), _inst(1175, 372, "H2S", "H2S-301", 345),
          _inst(1500, 882, "pH", "pH-601", 860), _inst(1550, 882, "DO", "DO-601", 860),
          _inst(970, 672, "ORP", "ORP-601", 705), _inst(1045, 672, "H2S", "H2S-601", 705),
          _inst(805, 672, "ORP", "ORP-602", 705), _inst(847, 672, "DO", "DO-602", 705),
          _inst(885, 672, "H2S", "H2S-602", 705),
          _inst(115, 950, "H2S", "H2S-701", 985), _inst(1115, 1030, "pH", "pH-801", 1065)]

    frame = (f'<rect x="4" y="4" width="{PFD_W - 8}" height="{PFD_H - 8}" fill="#FFFFFF" stroke="#111827" stroke-width="2"/>'
             + _tx(PFD_W / 2, 34, "FGD Wastewater Treatment - Process Flow Diagram (revised)", 20,
                   weight="bold", fill="#003366"))
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{PFD_W * scale:.0f}" height="{PFD_H * scale:.0f}" '
            f'viewBox="0 0 {PFD_W} {PFD_H}" font-family="Arial, Helvetica, sans-serif">')
    return head + _PFD_DEFS + frame + _highlight(highlight) + "".join(P) + "".join(E) + _legend(20, 1260) + "</svg>"
# PFD END


def _svg_img(svg):
    import base64
    b64 = base64.b64encode(svg.encode("utf-8")).decode()
    return (f'<img alt="Process flow diagram" style="width:100%;height:auto;display:block;border-radius:4px;'
            f'box-shadow:0 2px 8px rgba(0,0,0,.25)" src="data:image/svg+xml;base64,{b64}"/>')


def render_pfd(A, d, key="pfd", highlight=None, expand_large=True):
    """Whole PFD scaled to fit the page width (one page, no scrolling)."""
    st.markdown(_svg_img(pfd_svg(A, d, 1.0, highlight)), unsafe_allow_html=True)
    st.download_button("⬇️ Download PFD (SVG)", data=pfd_svg(A, d, 1.0, highlight),
                       file_name="process_flow_diagram.svg", mime="image/svg+xml", key=f"pfd_svg:{key}")
    if expand_large:
        with st.expander("🔍 Large view (100 %, scrollable)"):
            components.html(f'<div style="overflow:auto;background:#fff">{pfd_svg(A, d, 1.0, highlight)}</div>',
                            height=PFD_H + 24, scrolling=True)


def unit_pfd(A, d, hl):
    """Compact PFD shown at the top of each process page, current unit highlighted."""
    with st.expander("🗺️ Process flow diagram (current unit highlighted)", expanded=True):
        render_pfd(A, d, f"unit_{hl}", highlight=hl, expand_large=False)


# ======================================================================
# PDF
# ======================================================================
NAVY = colors.HexColor("#003366")
BLUE = colors.HexColor("#0066CC")
LIGHT = colors.HexColor("#EAF2FB")
GREEN = colors.HexColor("#D1FAE5")
RED = colors.HexColor("#FEE2E2")

_REPL = {"₂": "2", "₃": "3", "₄": "4", "→": "->", "≤": "<=", "≥": ">=", "✅": "", "❌": "",
         "−": "-", "‑": "-", "“": '"', "”": '"', "’": "'", "‘": "'", "…": "...", "·": ".", "µ": "u"}


def safe(s):
    s = str(s)
    for k, v in _REPL.items():
        s = s.replace(k, v)
    return s.encode("cp1252", "replace").decode("cp1252")


def esc(s):
    """XML-escape and turn m3 / m2 superscripts into markup (renders in every PDF viewer)."""
    out = escape(safe(s))
    return out.replace("³", "<super>3</super>").replace("²", "<super>2</super>")


# ------------------------------------------------------------------ styles
def make_styles():
    ss = getSampleStyleSheet()
    st = {
        "title": ParagraphStyle("CoverTitle", parent=ss["Title"], fontSize=26, leading=32,
                                textColor=NAVY, alignment=TA_CENTER),
        "sub": ParagraphStyle("CoverSub", parent=ss["Normal"], fontSize=13, leading=18,
                              textColor=BLUE, alignment=TA_CENTER),
        "H1": ParagraphStyle("H1", parent=ss["Heading1"], fontSize=15, leading=19,
                             textColor=NAVY, spaceBefore=6, spaceAfter=6),
        "H2": ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11.5, leading=14,
                             textColor=BLUE, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("Body", parent=ss["BodyText"], fontSize=9, leading=12.5, spaceAfter=5),
        "cap": ParagraphStyle("Cap", parent=ss["Normal"], fontSize=8, leading=10,
                              textColor=colors.HexColor("#475569"), spaceBefore=4, spaceAfter=2,
                              fontName="Helvetica-Oblique"),
        "cell": ParagraphStyle("Cell", parent=ss["Normal"], fontSize=7, leading=8.5),
        "cellb": ParagraphStyle("CellB", parent=ss["Normal"], fontSize=7, leading=8.5,
                                fontName="Helvetica-Bold", textColor=colors.white),
        "toc1": ParagraphStyle("toc1", parent=ss["Normal"], fontSize=10, leading=16),
    }
    return st


# ------------------------------------------------------------------ tables
def make_table(df, styles, avail_width):
    df = fmt_df(df)
    cols = list(df.columns)
    CH = 3.7                                    # approx. width of one 7 pt character
    min_w, ideal_w = [], []
    for c in cols:
        cells = [len(safe(v)) for v in df[c].tolist()] or [0]
        word = max(len(w) for w in safe(c).split() or [""])
        min_w.append(max(word * 4.3, min(max(cells), 12) * CH) + 9)
        ideal_w.append(max(min(max(max(cells), word), 46) * CH + 9, min_w[-1]))
    if sum(ideal_w) <= avail_width:
        widths = [w * avail_width / sum(ideal_w) for w in ideal_w]
    else:
        spare = max(avail_width - sum(min_w), 0.0)
        extra = [i - m for i, m in zip(ideal_w, min_w)]
        tot = sum(extra) or 1.0
        widths = [m + spare * e / tot for m, e in zip(min_w, extra)]
        scale = avail_width / sum(widths)
        widths = [w * scale for w in widths]

    header = [Paragraph(esc(c), styles["cellb"]) for c in cols]
    data = [header]
    for _, row in df.iterrows():
        data.append([Paragraph(esc(row[c]), styles["cell"]) for c in cols])
    t = Table(data, colWidths=widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for ci, c in enumerate(cols):
        if c == "Status":
            for ri, v in enumerate(df[c].tolist(), start=1):
                cmds.append(("BACKGROUND", (ci, ri), (ci, ri), GREEN if v == "PASS" else RED))
    for ri, row in enumerate(df.itertuples(index=False), start=1):
        if "TOTAL" in safe(row[0]).upper():
            cmds.append(("BACKGROUND", (0, ri), (-1, ri), colors.HexColor("#DBEAFE")))
    t.setStyle(TableStyle(cmds))
    return t


# ------------------------------------------------------------------ process flow drawing
def pfd_drawing(A, d, width):
    R = A["R"]
    chem = A["chem"]
    W, H = width, 370
    dr = Drawing(W, H)
    bw, bh = 82, 46
    gap = (W - 40 - 4 * bw) / 3.0
    xs = [i * (bw + gap) for i in range(4)]
    y1, y2, y3 = 285, 165, 45
    navy, light = NAVY, colors.HexColor("#E0F2FE")
    grey = colors.HexColor("#334155")
    brown = colors.HexColor("#92400E")

    def box(x, y, title, sub=None, fill=light):
        dr.add(Rect(x, y, bw, bh, rx=6, ry=6, fillColor=fill, strokeColor=navy, strokeWidth=1))
        dr.add(String(x + bw / 2, y + bh - 15, title, fontName="Helvetica-Bold", fontSize=7.2,
                      textAnchor="middle", fillColor=navy))
        if sub:
            dr.add(String(x + bw / 2, y + 9, sub, fontName="Helvetica", fontSize=6.3,
                          textAnchor="middle", fillColor=grey))

    def arrow(x1, y1_, x2, y2_):
        dr.add(Line(x1, y1_, x2, y2_, strokeColor=grey, strokeWidth=1))
        s = 4
        if abs(y1_ - y2_) < 1e-6:
            sgn = 1 if x2 > x1 else -1
            dr.add(Polygon([x2, y2_, x2 - sgn * 2 * s, y2_ + s, x2 - sgn * 2 * s, y2_ - s],
                           fillColor=grey, strokeColor=None))
        else:
            sgn = 1 if y2_ > y1_ else -1
            dr.add(Polygon([x2, y2_, x2 - s, y2_ - sgn * 2 * s, x2 + s, y2_ - sgn * 2 * s],
                           fillColor=grey, strokeColor=None))

    def pump_sym(cx, cy, tag):
        dr.add(Circle(cx, cy, 6, fillColor=colors.HexColor("#FDE68A"), strokeColor=navy))
        dr.add(String(cx, cy - 2.3, "P", fontName="Helvetica-Bold", fontSize=6.5, textAnchor="middle"))
        dr.add(String(cx, cy + 9, tag, fontName="Helvetica", fontSize=5.8, textAnchor="middle",
                      fillColor=navy))

    se = lambda k: f"Se {fmt_num(R['stream'][k]['selenium'])} mg/L"
    m1, m2, m3 = y1 + bh / 2, y2 + bh / 2, y3 + bh / 2

    # row 1 (left -> right): influent, EQ A/B, reaction tanks A-C, clarifiers A/B
    box(xs[0], y1, "Influent", f"{R['flow_gpm']:,.0f} gpm")
    box(xs[1], y1, "Equalization A/B", "T-101 / M-101")
    box(xs[2], y1, "Reaction Tanks A>B>C", se("rt"))
    box(xs[3], y1, "Clarifiers A/B", se("cl"))
    arrow(xs[0] + bw, m1, xs[1], m1)
    arrow(xs[1] + bw, m1, xs[2], m1)
    pump_sym((xs[1] + bw + xs[2]) / 2, m1, "P-101")
    arrow(xs[2] + bw, m1, xs[3], m1)
    names = [n for n, k in (("Lime", "lime"), ("NaOH", "naoh"), ("FeCl3", "fecl3"),
                            ("Polymer", "polymer"), ("Organosulfide", "organosulfide"))
             if chem[k]["kg_day"] > 0]
    if names:
        cx = xs[2] + bw / 2
        arrow(cx, y1 + bh + 22, cx, y1 + bh)
        dr.add(String(cx, y1 + bh + 27, ", ".join(names) + " (each tank)", fontName="Helvetica",
                      fontSize=6.2, textAnchor="middle", fillColor=brown))

    # row 2 (right -> left): clearwell, sand filters, bio feed tank, bioreactor stage 1 > 2
    box(xs[3], y2, "Clearwell", "T-301 + HCl")
    box(xs[2], y2, "Sand Filters", se("sf"))
    box(xs[1], y2, "Bio Feed Tank", "T-601 + nutrient")
    box(xs[0], y2, "Bioreactor 1 > 2", se("bio"))
    arrow(xs[3] + bw / 2, y1, xs[3] + bw / 2, y2 + bh)
    arrow(xs[3], m2, xs[2] + bw, m2)
    pump_sym((xs[3] + xs[2] + bw) / 2, m2, "P-401")
    arrow(xs[2], m2, xs[1] + bw, m2)
    arrow(xs[1], m2, xs[0] + bw, m2)
    pump_sym((xs[1] + xs[0] + bw) / 2, m2, "P-601")
    dr.add(String((xs[1] + xs[0] + bw) / 2, m2 - 15, "S-601 / HX-601 / FCS", fontName="Helvetica",
                  fontSize=5.6, textAnchor="middle", fillColor=grey))
    if chem["hcl"]["kg_day"] > 0:
        arrow(xs[3] + bw + 30, m2, xs[3] + bw, m2)
        dr.add(String(xs[3] + bw + 2, m2 + 6, "HCl", fontName="Helvetica", fontSize=6.5,
                      textAnchor="start", fillColor=brown))
    if chem["methanol"]["kg_day"] > 0:
        dr.add(String(xs[0] + bw / 2, y2 + bh + 5, "+ Methanol (T-605)", fontName="Helvetica",
                      fontSize=6, textAnchor="middle", fillColor=brown))

    # row 3 (left -> right): UF skids, effluent / backwash tank, UV, discharge
    box(xs[0], y3, "UF Skids", se("uf"))
    box(xs[1], y3, "Effluent / BW Tank", "T-702")
    box(xs[2], y3, "UV Treatment", "UV-801")
    box(xs[3], y3, "NPDES Discharge", f"{A['des']['uf']['permeate']:,.0f} m3/h")
    arrow(xs[0] + bw / 2, y2, xs[0] + bw / 2, y3 + bh)
    dr.add(String(xs[0] + bw / 2 + 4, (y2 + y3 + bh) / 2 - 2, "UF sump P-707 / feed P-701",
                  fontName="Helvetica", fontSize=5.6, textAnchor="start", fillColor=grey))
    arrow(xs[0] + bw, m3, xs[1], m3)
    arrow(xs[1] + bw, m3, xs[2], m3)
    pump_sym((xs[1] + bw + xs[2]) / 2, m3, "P-801")
    arrow(xs[2] + bw, m3, xs[3], m3)

    dr.add(String(0, 240, "Sludge: clarifier underflow -> P-301 -> sludge tanks A/B -> press feed P-901 -> "
                          "vacuum filter presses A/B/C -> cake; filtrate -> EQ",
                  fontName="Helvetica", fontSize=6.2, textAnchor="start", fillColor=brown))
    dr.add(String(0, 122, "Recycles: sand filter overflow -> Reaction Tank A; backwash / reject / strainer "
                          "waste -> outdoor sump -> EQ tanks",
                  fontName="Helvetica", fontSize=6.2, textAnchor="start", fillColor=colors.HexColor("#1E5F8E")))
    dr.add(String(0, 106, "Online analyzers: pH (RT A-C, clearwell, bio feed, discharge); ORP (RT C, bioreactor 1/2); "
                         "H2S (clearwell, bioreactor 1/2, UF sump); DO (bio feed tank, bioreactor 2)",
                  fontName="Helvetica", fontSize=6.2, textAnchor="start", fillColor=colors.HexColor("#5B21B6")))
    dr.add(String(W / 2, 10, "Block flow diagram - Se = selenium leaving each unit; P = pump",
                  fontName="Helvetica-Oblique", fontSize=7, textAnchor="middle",
                  fillColor=colors.HexColor("#475569")))
    return dr


# ------------------------------------------------------------------ document
class ReportDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "H1":
            text = flowable.getPlainText()
            key = f"h1_{self.page}_{abs(hash(text)) % 10**6}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, 0, 0)
            self.notify("TOCEntry", (0, text, self.page))


def build_pdf(project, A, sections, prepared_by="", company="", cover=True):
    d, meta = project["data"], project["meta"]
    buf = io.BytesIO()
    st = make_styles()
    lm = rm = 1.6 * cm
    doc = ReportDoc(buf, pagesize=A4, leftMargin=lm, rightMargin=rm, topMargin=2.0 * cm,
                    bottomMargin=1.8 * cm, title=f"Design Report - {meta.get('name', '')}",
                    author=prepared_by or "HSKH FGD Treatment Design")
    avail = A4[0] - lm - rm
    pname = safe(meta.get("name", ""))

    def decorate(canv, _doc):
        canv.saveState()
        if _doc.page > 1:
            canv.setFont("Helvetica", 8)
            canv.setFillColor(colors.HexColor("#475569"))
            canv.drawString(lm, A4[1] - 1.2 * cm, f"HSKH FGD Treatment Design - {pname}")
            canv.setStrokeColor(colors.HexColor("#CBD5E1"))
            canv.line(lm, A4[1] - 1.4 * cm, A4[0] - rm, A4[1] - 1.4 * cm)
        canv.setFont("Helvetica", 8)
        canv.setFillColor(colors.HexColor("#475569"))
        canv.drawRightString(A4[0] - rm, 1.0 * cm, f"Page {_doc.page}")
        canv.drawString(lm, 1.0 * cm, datetime.now().strftime("%Y-%m-%d"))
        canv.restoreState()

    frame = Frame(lm, 1.8 * cm, avail, A4[1] - 3.8 * cm, id="f", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])

    comp_fail = sum(1 for p, lk in COMPLIANCE
                    if A["R"]["final"][p] > gf(d, lk))
    story = []

    if cover:
        # ---- cover page + contents
        story += [Spacer(1, 4.5 * cm), Paragraph("Water Treatment Design Report", st["title"]),
                  Spacer(1, 0.4 * cm), Paragraph("FGD Wastewater Treatment System", st["sub"]),
                  Spacer(1, 1.2 * cm)]
        info = [["Project", meta.get("name", "")], ["Client", meta.get("client", "")],
                ["Location", meta.get("location", "")],
                ["Design flow", f"{A['R']['flow_gpm']:,.0f} gpm  ({A['R']['q_m3h']:,.1f} m³/h)"],
                ["Prepared by", f"{prepared_by} {('- ' + company) if company else ''}".strip(" -")],
                ["Date", datetime.now().strftime("%d %B %Y")],
                ["Compliance", "All permit limits met" if comp_fail == 0
                 else f"{comp_fail} limit(s) exceeded"]]
        t = Table([[Paragraph(f"<b>{esc(a)}</b>", st["body"]), Paragraph(esc(b), st["body"])]
                   for a, b in info], colWidths=[4 * cm, avail - 4 * cm - 3 * cm])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        outer = Table([[t]], colWidths=[avail - 3 * cm])
        outer.hAlign = "CENTER"
        story += [outer, PageBreak()]
        toc = TableOfContents()
        toc.levelStyles = [st["toc1"]]
        story += [Paragraph("Contents", st["H1"]), toc, PageBreak()]
    else:
        story += [Paragraph(esc(f"{meta.get('name', '')} - {sections[0]['title'] if sections else ''}"),
                            st["title"]),
                  Paragraph(esc(datetime.now().strftime("%d %B %Y")), st["sub"]), Spacer(1, 0.6 * cm)]

    # ---- sections
    for n, sec in enumerate(sections, start=1):
        story.append(Paragraph(esc(f"{n}. {sec['title']}" if cover else sec["title"]), st["H1"]))
        for blk in sec["blocks"]:
            kind = blk[0]
            if kind == "h2":
                story.append(Paragraph(esc(blk[1]), st["H2"]))
            elif kind == "text":
                story.append(Paragraph(esc(blk[1]), st["body"]))
            elif kind == "table":
                df, cap = blk[1], blk[2]
                if df is None or len(df) == 0:
                    continue
                tbl = make_table(df, st, avail)
                cap_p = Paragraph(esc(cap), st["cap"])
                story.append(KeepTogether([cap_p, tbl]) if len(df) <= 14 else cap_p)
                if len(df) > 14:
                    story.append(tbl)
                story.append(Spacer(1, 6))
            elif kind == "pfd":
                story.append(pfd_drawing(A, d, avail))
        story.append(Spacer(1, 10))
        if n < len(sections):
            story.append(CondPageBreak(7 * cm))

    doc.multiBuild(story)
    return buf.getvalue()


# ======================================================================
# STATE
# ======================================================================
PROJECT_DIR = Path(__file__).resolve().parent / "projects"
PROJECT_DIR.mkdir(exist_ok=True)
LAST_FILE = PROJECT_DIR / ".last_project"
WIDGET_PREFIX = "w:"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slugify(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name).strip()).strip("_")
    return s[:80] or "project"


def _path(slug):
    return PROJECT_DIR / f"{slug}.json"


def _unique_slug(name):
    base, slug, i = slugify(name), slugify(name), 2
    while _path(slug).exists():
        slug = f"{base}_{i}"
        i += 1
    return slug


def _digest(project):
    blob = json.dumps({"meta": {k: v for k, v in project["meta"].items() if k != "modified"},
                       "data": project["data"]}, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()


def _write(project):
    path = _path(project["slug"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"meta": project["meta"], "data": project["data"]},
                              indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ listing
def list_projects():
    out = []
    for p in PROJECT_DIR.glob("*.json"):
        if p.stem.startswith(".") or not p.stem.strip():      # ignore hidden / nameless files
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
        except Exception:
            continue
        meta, data = raw.get("meta", {}), raw.get("data", {})
        out.append({
            "slug": p.stem, "name": meta.get("name", p.stem),
            "client": meta.get("client", ""), "location": meta.get("location", ""),
            "description": meta.get("description", ""),
            "created": meta.get("created", ""), "modified": meta.get("modified", ""),
            "flow": data.get("flow", DEFAULTS["flow"]),
        })
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out


# ------------------------------------------------------------------ open / close
def _clear_widget_state():
    for k in [k for k in st.session_state.keys() if str(k).startswith(WIDGET_PREFIX)]:
        del st.session_state[k]


def _activate(project):
    _clear_widget_state()
    st.session_state["project"] = project
    st.session_state["_digest"] = _digest(project)
    LAST_FILE.write_text(project["slug"])


def open_project(slug):
    raw = json.loads(_path(slug).read_text(encoding="utf-8"))
    _activate({"slug": slug, "meta": raw.get("meta", {"name": slug}), "data": raw.get("data", {})})


def clear_widget_keys(keys):
    """Forget cached widget values so the widgets re-read the (updated) project data."""
    cur = current()
    if not cur:
        return
    for k in keys:
        st.session_state.pop(f"{WIDGET_PREFIX}{cur['slug']}:{k}", None)


def read_raw(slug):
    try:
        return _path(slug).read_text(encoding="utf-8")
    except OSError:
        return "{}"


def close_project():
    _clear_widget_state()
    st.session_state.pop("project", None)
    st.session_state.pop("_digest", None)
    if LAST_FILE.exists():
        LAST_FILE.unlink()


def resume_last():
    """Re-open the last project once per browser session."""
    if st.session_state.get("_resume_done"):
        return
    st.session_state["_resume_done"] = True
    if "project" in st.session_state or not LAST_FILE.exists():
        return
    slug = LAST_FILE.read_text().strip()
    if slug and _path(slug).exists():
        try:
            open_project(slug)
        except Exception:
            pass


DRAFT_SLUG = "_draft"


def current():
    return st.session_state.get("project")


def active():
    """The open *saved* project, or None (an unsaved draft does not count)."""
    p = current()
    return None if (not p or p["meta"].get("draft")) else p


def ensure_draft():
    """Pages stay fully usable before any project exists: they work on an in-memory draft
    with default values. The draft is never written to disk."""
    p = current()
    if p:
        return p
    p = {"slug": DRAFT_SLUG,
         "meta": {"name": "Untitled project", "client": "", "location": "", "description": "",
                  "created": now(), "modified": now(), "draft": True},
         "data": {}}
    st.session_state["project"] = p
    return p


# ------------------------------------------------------------------ create / import / copy / delete
def create_project(name, client="", location="", description="", flow=None, data=None):
    slug = _unique_slug(name)
    cur = current()
    if data is not None:
        d = dict(data)
    elif cur and cur["meta"].get("draft"):          # keep whatever was entered on the draft
        d = dict(cur["data"])
    else:
        d = {}
    if flow is not None:
        d["flow"] = float(flow)
    project = {"slug": slug,
               "meta": {"name": name.strip() or slug, "client": client, "location": location,
                        "description": description, "created": now(), "modified": now()},
               "data": d}
    _write(project)
    _activate(project)
    return slug


def import_project(raw_bytes, filename="imported_project"):
    """Accepts new-format project files and the old flat water_data JSON."""
    raw = json.loads(raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes)
    if not isinstance(raw, dict):
        raise ValueError("Project file must contain a JSON object.")
    stem = Path(filename).stem
    if "data" in raw and isinstance(raw["data"], dict):
        meta = dict(raw.get("meta", {}))
        data = raw["data"]
    else:                                   # legacy flat dictionary
        meta, data = {}, raw
    name = meta.get("name") or stem
    return create_project(name, meta.get("client", ""), meta.get("location", ""),
                          meta.get("description", "Imported project"), data=data)


def duplicate_project(slug, new_name):
    raw = json.loads(_path(slug).read_text(encoding="utf-8"))
    meta = dict(raw.get("meta", {}))
    return create_project(new_name, meta.get("client", ""), meta.get("location", ""),
                          meta.get("description", ""), data=raw.get("data", {}))


def delete_project(slug):
    p = _path(slug)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    cur = current()
    if cur and cur["slug"] == slug:
        close_project()


def export_json(project):
    return json.dumps({"meta": project["meta"], "data": project["data"]}, indent=2, default=str)


# ------------------------------------------------------------------ autosave
def autosave():
    """Write the project to disk only if something changed since the last save."""
    p = current()
    if not p or p["meta"].get("draft"):
        return False
    dg = _digest(p)
    if dg == st.session_state.get("_digest"):
        return False
    p["meta"]["modified"] = now()
    _write(p)
    st.session_state["_digest"] = dg
    return True


# ======================================================================
# UI
# ======================================================================
# ------------------------------------------------------------------ theme
def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


THEME_CSS = """
<style>
:root { --bar-h: 3.7rem; --side: 0rem;
        --page-bg: linear-gradient(135deg, #C9D3DF 0%, #A8D5C2 28%, #BDB0E8 62%, #9CC4F2 100%); }
/* width of the open sidebar (Streamlit default) - used to align the title with the content panel */
.stApp:has(section[data-testid="stSidebar"][aria-expanded="true"]) { --side: 21rem; }
html { color-scheme: light; }

/* ---------- ONE continuous page: grey / green / purple / blue gradient ---------- */
.stApp {
    background: var(--page-bg) fixed !important;
}
header[data-testid="stHeader"] {
    background: transparent !important; border: none !important; box-shadow: none !important;
    height: 2.2rem !important; min-height: 2.2rem !important;
}
[data-testid="stToolbar"] { background: transparent !important; }
[data-testid="stAppDeployButton"], .stDeployButton, [data-testid="stDecoration"] { display: none !important; }

/* ---------- all text black ---------- */
.stApp, .stApp p, .stApp span, .stApp label, .stApp li, .stApp div, .stApp td, .stApp th,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp a, .stApp small, .stApp summary,
.stApp [data-testid="stMarkdownContainer"] * { color: #000000 !important; }
[data-baseweb="popover"] *, [data-baseweb="menu"] * { color: #000000 !important; }
[data-baseweb="popover"], [data-baseweb="menu"], ul[role="listbox"] { background: #FFFFFF !important; }

/* ---------- fixed title + project details, always at the top of the window ---------- */
div[data-testid="stElementContainer"]:has(.hskh-sticky), div.element-container:has(.hskh-sticky) {
    position: fixed !important; top: 0; left: 0; right: 0; width: 100% !important;
    z-index: 1000; margin: 0 !important; padding: 0 !important;
}
.hskh-sticky {
    height: var(--bar-h); box-sizing: border-box; padding: 0.3rem 0 0.2rem 0;
    background: var(--page-bg) fixed;   /* identical gradient => title bar is invisible against the page */
}
/* title sits exactly over the content panel (same width, same left edge) */
.hskh-sticky .hskh-title {
    width: calc((100vw - var(--side)) * __PAGE_FRAC__); margin-left: calc(var(--side) + (100vw - var(--side)) * ((1 - __PAGE_FRAC__) / 2));
}
.hskh-title { display: flex; align-items: center; gap: 0.7rem; height: 2.9rem; }
.hskh-logo { height: 2.9rem; width: auto; filter: drop-shadow(0 3px 3px rgba(0,0,0,0.35)); }
/* extruded (3-D) title lettering */
.stApp .hskh-title .hskh-title-text {
    font-size: 1.75rem; font-weight: 900; letter-spacing: 0.6px; line-height: 2.9rem;
    color: #0B3A66 !important;
    text-shadow: 1px 1px 0 #3B7DB8, 2px 2px 0 #2F6AA0, 3px 3px 0 #255A8B, 4px 4px 0 #1B4A75,
                 5px 6px 8px rgba(0, 0, 0, 0.45), -1px -1px 0 rgba(255, 255, 255, 0.7);
}
.hskh-side { margin: 0 0 14px 0; padding: 8px 10px; background: rgba(255,255,255,0.45); border-radius: 6px; }
.hskh-side .hd { font-weight: 800; font-size: 14px; margin-bottom: 4px; }
.hskh-side .lb { font-size: 12px; font-weight: 700; margin-top: 6px; }
.hskh-side .fld {
    background: #FFFFFF; border: 1px solid #64748B; border-radius: 2px; padding: 2px 8px;
    font-size: 13px; min-height: 1.3rem; word-break: break-word;
}

/* ---------- content: translucent panel floating on the gradient ---------- */
.block-container, [data-testid="stMainBlockContainer"] {
    max-width: __PAGE_WIDTH__ !important; margin-left: auto !important; margin-right: auto !important;
    background: rgba(255, 255, 255, 0.58); border: none; border-radius: 8px;
    box-shadow: 0 4px 18px rgba(30, 41, 59, 0.18);
    padding: 1.4rem 1.6rem 2.5rem 1.6rem !important;
    padding-top: 1.4rem !important; margin-top: calc(var(--bar-h) + 0.4rem) !important;
    margin-bottom: 1.5rem;
}
[data-testid="stMain"], [data-testid="stAppViewContainer"] > .main { padding-top: 0 !important; }
[data-testid="stVerticalBlock"] { gap: 0.8rem; }

/* ---------- section bars ---------- */
h1 { font-weight: 700 !important; }
h2, h3 {
    background: rgba(255, 255, 255, 0.72); border: 1px solid #94A3B8; border-left: 5px solid #5B6CC9;
    border-radius: 4px; padding: 5px 12px !important; font-weight: 700 !important;
}
h2 { font-size: 1.3rem !important; }
h3 { font-size: 1.05rem !important; border-left-color: #3E9B7A; }

/* ---------- inputs ---------- */
[data-testid="stWidgetLabel"] p, label p { font-size: 13px !important; font-weight: 600 !important; }
div[data-baseweb="input"], div[data-baseweb="base-input"], div[data-baseweb="select"] > div,
textarea, .stNumberInput input, .stTextInput input {
    background-color: #FFFFFF !important; border-radius: 3px !important;
}
/* inputs: thin light-black outline with a sunken 3-D look */
div[data-baseweb="input"], div[data-baseweb="select"] > div, div[data-baseweb="textarea"] {
    border: 1px solid rgba(0, 0, 0, 0.5) !important; border-radius: 4px !important;
    background: #FFFFFF !important; outline: none !important;
    box-shadow: inset 2px 2px 4px rgba(0, 0, 0, 0.30), inset -1px -1px 2px rgba(255, 255, 255, 0.9),
                0 1px 0 rgba(255, 255, 255, 0.9) !important;
}
div[data-baseweb="base-input"], div[data-baseweb="input"] input, div[data-baseweb="textarea"] textarea {
    border: none !important; box-shadow: none !important; outline: none !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="select"] > div:focus-within,
div[data-baseweb="textarea"]:focus-within { border: 1px solid #000000 !important; }

/* + / - stepper: raised 3-D keys with black signs */
.stNumberInput button {
    background: linear-gradient(180deg, #FFFFFF 0%, #D3DAE5 100%) !important;
    border: none !important; border-left: 1px solid rgba(0, 0, 0, 0.45) !important; border-radius: 0 !important;
    box-shadow: inset 1px 1px 0 rgba(255, 255, 255, 0.95), inset -1px -1px 0 rgba(0, 0, 0, 0.20) !important;
}
.stNumberInput button:hover { background: linear-gradient(180deg, #FFFFFF 0%, #C3CDDC 100%) !important; }
.stNumberInput button:active { box-shadow: inset 2px 2px 4px rgba(0, 0, 0, 0.35) !important; }
.stNumberInput button, .stNumberInput button *, .stNumberInput button svg, .stNumberInput button svg * {
    color: #000000 !important; fill: #000000 !important; opacity: 1 !important;
}
.stNumberInput button:disabled, .stNumberInput button:disabled * { opacity: 0.4 !important; }
input, textarea { color: #000 !important; -webkit-text-fill-color: #000 !important; }
input:disabled, textarea:disabled { background-color: #E5E7EB !important; -webkit-text-fill-color: #000 !important; }

/* ---------- tabs ---------- */
div[data-baseweb="tab-list"] { background: rgba(255,255,255,0.45); border-radius: 6px; padding: 3px; gap: 3px; }
button[data-baseweb="tab"] { background: transparent !important; border-radius: 4px; font-weight: 600; }
button[data-baseweb="tab"][aria-selected="true"] { background: #FFFFFF !important; font-weight: 800; box-shadow: 0 1px 3px rgba(0,0,0,0.2); }
div[data-baseweb="tab-highlight"] { background-color: #5B6CC9 !important; }
div[data-baseweb="tab-border"] { background-color: transparent !important; }

/* ---------- cards, expanders, tables ---------- */
div[data-testid="stMetric"] {
    background: rgba(255,255,255,0.8); border: 1px solid #CBD5E1; border-left: 5px solid #3E9B7A;
    border-radius: 4px; padding: 8px 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.12);
}
div[data-testid="stMetricValue"] { font-size: 19px; font-weight: 700; }
div[data-testid="stMetricLabel"] { font-size: 12.5px; }
details { background: rgba(255,255,255,0.6); border: 1px solid #CBD5E1 !important; border-radius: 4px; }
[data-testid="stDataFrame"] { background: #FFFFFF; border: 1px solid #94A3B8; border-radius: 3px; }
div[data-testid="stVerticalBlockBorderWrapper"] { background: rgba(255,255,255,0.5); }
div[data-testid="stAlert"] { border-radius: 4px; }

/* ---------- buttons ---------- */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] button,
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {
    background: linear-gradient(180deg, #F8FAFC 0%, #B9C4F0 100%) !important;
    border: 1px solid #64748B !important; border-radius: 14px !important;
    font-weight: 700 !important; box-shadow: 0 1px 3px rgba(0,0,0,0.25);
}
.stButton > button:hover, .stDownloadButton > button:hover {
    background: linear-gradient(180deg, #FFFFFF 0%, #A5B4F5 100%) !important;
}

/* ---------- sidebar: no boundary, sits under the fixed title bar ---------- */
section[data-testid="stSidebar"] { background: transparent !important; border: none !important; box-shadow: none !important; }
section[data-testid="stSidebar"] > div { background: transparent !important; padding-top: calc(var(--bar-h) - 1.6rem) !important; }
section[data-testid="stSidebar"] a[aria-current="page"] { background: rgba(255,255,255,0.75) !important; font-weight: 700; border-radius: 4px; }
section[data-testid="stSidebar"] hr { border-color: rgba(0,0,0,0.25); }

.hskh-banner { background: rgba(255,255,255,0.6); padding: 18px 24px; border-radius: 6px; margin-bottom: 16px; }
</style>
"""


PAGE_WIDTH = "75%"      # width of the content panel (and of the title above it)


def inject_theme():
    st.markdown(THEME_CSS.replace("__PAGE_WIDTH__", PAGE_WIDTH)
                .replace("__PAGE_FRAC__", str(float(PAGE_WIDTH.strip("%")) / 100)), unsafe_allow_html=True)


def _strip_html():
    p = current()
    if not p:
        return ""
    m, d = p["meta"], p["data"]
    t = d.get("temperature", DEFAULTS["temperature"])
    fields = [("Project", m.get("name", p["slug"])), ("Client", m.get("client", "")),
              ("Location", m.get("location", "")),
              ("Flow", f"{float(d.get('flow', DEFAULTS['flow'])):,.0f} gpm"),
              ("Temperature", f"{float(t):.1f} °C"),
              ("Final use", d.get("final_water_use", DEFAULTS["final_water_use"])),
              ("Saved", m.get("modified", ""))]
    rows = "".join(f"<div class='lb'>{k}</div><div class='fld'>{_esc(v) or '&nbsp;'}</div>"
                   for k, v in fields)
    return f'<div class="hskh-side"><div class="hd">Project details</div>{rows}</div>'


def sidebar_status():
    """Nothing to show: project details live on the Project Manager page."""
    return None


def banner(title, subtitle=""):
    st.markdown(f'<div class="hskh-banner"><h1>{title}</h1><p>{subtitle}</p></div>',
                unsafe_allow_html=True)


# ------------------------------------------------------------------ project access
def require_project():
    """Always returns a project dict: the open project, or an unsaved draft with default values."""
    return ensure_draft()


def data():
    return current()["data"]


APP_TITLE = "HSKH FGD Treatment Design"

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80" width="80" height="80">
<defs>
<radialGradient id="lgSph" cx=".34" cy=".28" r=".85"><stop offset="0" stop-color="#BAE6FD"/><stop offset=".35" stop-color="#2E9BE0"/><stop offset=".75" stop-color="#0B4F8A"/><stop offset="1" stop-color="#06274A"/></radialGradient>
<linearGradient id="lgRim" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F8FAFC"/><stop offset=".5" stop-color="#94A3B8"/><stop offset="1" stop-color="#334155"/></linearGradient>
<linearGradient id="lgDrop" x1=".2" y1="0" x2=".8" y2="1"><stop offset="0" stop-color="#FFFFFF"/><stop offset=".45" stop-color="#7DD3FC"/><stop offset="1" stop-color="#0284C7"/></linearGradient>
<radialGradient id="lgGloss" cx=".5" cy=".1" r=".9"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".85"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>
<radialGradient id="lgShadow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#000" stop-opacity=".55"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
</defs>
<ellipse cx="40" cy="73" rx="26" ry="5" fill="url(#lgShadow)"/>
<circle cx="40" cy="38" r="35" fill="url(#lgRim)"/>
<circle cx="40" cy="38" r="31.5" fill="url(#lgSph)"/>
<path d="M40 14 C40 14 24 33 24 44 a16 16 0 0 0 32 0 C56 33 40 14 40 14 Z" fill="url(#lgDrop)" stroke="#0369A1" stroke-width="1"/>
<path d="M33 40 C31 46 33 52 38 55" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" opacity=".85"/>
<path d="M30 52 q10 6 20 0" fill="none" stroke="#0369A1" stroke-width="1.5" opacity=".5"/>
<ellipse cx="32" cy="19" rx="18" ry="10" fill="url(#lgGloss)" transform="rotate(-22 32 19)"/>
<circle cx="40" cy="38" r="31.5" fill="none" stroke="#FFFFFF" stroke-opacity=".35" stroke-width="1"/>
</svg>"""


def _logo_uri():
    import base64
    return "data:image/svg+xml;base64," + base64.b64encode(LOGO_SVG.encode("utf-8")).decode()


def header(title, caption=None):
    """Fixed top bar = app title only. Project details live in the sidebar; the page name is shown
    by the sidebar menu (`title` is accepted for compatibility but not displayed)."""
    st.markdown(f'<div class="hskh-sticky"><div class="hskh-title"><img class="hskh-logo" src="{_logo_uri()}"/>'
                f'<span class="hskh-title-text">{_esc(APP_TITLE)}</span></div></div>',
                unsafe_allow_html=True)
    if caption:
        st.caption(caption)


def finish():
    if autosave():
        st.toast("Project auto-saved", icon="💾")


# ------------------------------------------------------------------ project-bound widgets
def _wkey(key):
    return f"{WIDGET_PREFIX}{current()['slug']}:{key}"


def num(label, key, container=None, **kw):
    d = data()
    try:
        v = float(d.get(key, DEFAULTS[key]))
        if v != v:
            raise ValueError
    except (TypeError, ValueError):
        v = float(DEFAULTS[key])
    if "min_value" in kw:
        v = max(v, float(kw["min_value"]))
    if "max_value" in kw:
        v = min(v, float(kw["max_value"]))
    for k in ("min_value", "max_value", "step"):
        if k in kw:
            kw[k] = float(kw[k])
    out = (container or st).number_input(label, value=v, key=_wkey(key), **kw)
    d[key] = float(out)
    return float(out)


def inum(label, key, container=None, **kw):
    d = data()
    try:
        v = int(round(float(d.get(key, DEFAULTS[key]))))
    except (TypeError, ValueError):
        v = int(DEFAULTS[key])
    kw.setdefault("min_value", 0)
    v = max(v, int(kw["min_value"]))
    if "max_value" in kw:
        v = min(v, int(kw["max_value"]))
    kw["step"] = 1
    out = (container or st).number_input(label, value=v, key=_wkey(key), **kw)
    d[key] = int(out)
    return int(out)


def text(label, key, container=None):
    d = data()
    out = (container or st).text_input(label, value=str(d.get(key, DEFAULTS.get(key, ""))),
                                       key=_wkey(key))
    d[key] = out
    return out


def choice(label, key, options, container=None):
    d = data()
    cur = d.get(key, DEFAULTS.get(key, options[0]))
    idx = options.index(cur) if cur in options else 0
    out = (container or st).selectbox(label, options, index=idx, key=_wkey(key))
    d[key] = out
    return out


def input_row(spec, ncols=4, kind="num", **kw):
    """spec = [(label, key), ...]; lays the widgets out in columns."""
    cols = st.columns(ncols)
    for i, item in enumerate(spec):
        label, key = item[0], item[1]
        extra = dict(kw)
        if len(item) > 2:
            extra.update(item[2])
        fn = inum if kind == "int" else num
        fn(label, key, container=cols[i % ncols], **extra)


# ------------------------------------------------------------------ display helpers
def fmt_value(v, fmt):
    try:
        return f"{v:{fmt}}"
    except (ValueError, TypeError):
        return str(v)


def metrics(rows, ncols=4):
    """rows = [(label, value, unit, fmt)]"""
    for i in range(0, len(rows), ncols):
        cols = st.columns(ncols)
        for c, (label, val, unit, fmt) in zip(cols, rows[i:i + ncols]):
            c.metric(label, f"{fmt_value(val, fmt)} {unit}".strip())


def _new_width_api(fn):
    """True when the Streamlit version takes width="stretch" (newer releases)."""
    import inspect
    try:
        prm = inspect.signature(fn).parameters.get("width")
    except (TypeError, ValueError):
        return False
    return prm is not None and isinstance(prm.default, str)


def show_df(df, fmt=True, **kw):
    if df is None or len(df) == 0:
        st.caption("— none —")
        return
    out = fmt_df(df) if fmt else df
    if _new_width_api(st.dataframe):
        st.dataframe(out, hide_index=True, width="stretch", **kw)
    else:
        st.dataframe(out, hide_index=True, use_container_width=True, **kw)


def removal_table(R, stage, params, labels=None):
    """Feed / removal / effluent table for one unit."""
    rows = []
    for p in params:
        lab = PARAM_INFO[p][0]
        rows.append({"Parameter": lab, "Unit": PARAM_INFO[p][1],
                     "Feed": R["feed"][stage][p], "Removal (%)": R["removal"][stage][p],
                     "Effluent": R["stream"][stage][p]})
    return pd.DataFrame(rows)


def equipment_panel(A, groups, title="Pumps, Tanks & Mixers"):
    rows = filter_rows(A["eq"], groups)
    pumps, tanks, others = equipment_frames(rows)
    ps = power_summary(rows)
    st.subheader(title)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pumps (installed)", f"{sum(r['duty'] + r['standby'] for r in rows if r['kind'] == 'pump')}")
    c2.metric("Tanks / vessels", f"{sum(r['qty'] for r in rows if r['kind'] == 'tank')}")
    c3.metric("Installed power", f"{ps['installed_kw']:,.0f} kW")
    c4.metric("Average running power", f"{ps['running_kw']:,.1f} kW")
    if len(pumps):
        st.markdown("**Pumps**")
        show_df(pumps)
    if len(tanks):
        st.markdown("**Tanks / Vessels**")
        show_df(tanks)
    if len(others):
        st.markdown("**Mixers, blowers & mechanical equipment**")
        show_df(others)


# ------------------------------------------------------------------ more helpers
def removal_metrics(R, stage, params, ncols=4):
    rows = [(f"{PARAM_INFO[p][0]} removal", R["removal"][stage][p], "%", ".1f") for p in params]
    metrics(rows, ncols)


def effluent_metrics(R, stage, params, ncols=4):
    rows = []
    for p in params:
        v = R["stream"][stage][p]
        rows.append((f"{PARAM_INFO[p][0]} out", v, PARAM_INFO[p][1], ".6f" if v < 0.1 else ".3f"))
    metrics(rows, ncols)


def _sig(project, extra=""):
    import hashlib
    import json
    blob = json.dumps(project["data"], sort_keys=True, default=str) + str(extra)
    return hashlib.md5(blob.encode()).hexdigest()


def pdf_button(label, build_fn, filename, key, extra=""):
    """Two-step PDF download: prepare -> download. Re-prepare is required after inputs change."""
    p = current()
    sig = _sig(p, extra)
    skey = f"pdf:{p['slug']}:{key}"
    c1, c2 = st.columns([1, 2])
    if c1.button(f"📄 Prepare {label}", key=f"prep:{key}"):
        with st.spinner("Building PDF..."):
            st.session_state[skey] = (sig, build_fn())
    ready = st.session_state.get(skey)
    if ready and ready[0] == sig:
        c2.download_button(f"⬇️ Download {label}", data=ready[1], file_name=filename,
                           mime="application/pdf", key=f"dl:{key}")
    elif ready:
        c2.caption("Inputs changed since this PDF was prepared - prepare it again.")


def unit_pdf(A, key, title):
    p = current()
    pdf_button(f"{title} PDF",
               lambda: build_pdf(p, A, [unit_section(p, A, key)], cover=False),
               f"{slugify(p['meta'].get('name', 'project'))}_{key}.pdf", f"unit_{key}")


def section_pdf(A, ids, title, key):
    p = current()
    pdf_button(f"{title} PDF",
               lambda: build_pdf(p, A, pick(build_report(p, A), ids), cover=False),
               f"{slugify(p['meta'].get('name', 'project'))}_{key}.pdf", f"sec_{key}")


# ======================================================================
# PAGE: Project Manager
# ======================================================================
def page_project_manager():
    header("Project Manager", "Create, open, upload and manage projects. Projects are saved inside the app "
              f"(folder: `{PROJECT_DIR.name}/`) and every change is auto-saved.")

    cur = active()
    if cur:
        st.success(f"Open project: **{cur['meta'].get('name', cur['slug'])}**  "
                   f"(last saved {cur['meta'].get('modified', '-')})")
    else:
        st.info("No saved project is open. Every page still works with default values (unsaved draft) - "
                "create or open a project to save your work. Values you entered on the draft are kept when "
                "you create a new project.")

    tab_new, tab_open, tab_up, tab_det = st.tabs(["➕ Create New Project", "📂 Open Saved Project",
                                                  "⬆️ Upload Project", "📋 Project Details"])

    # ------------------------------------------------------------------ create
    with tab_new:
        with st.form("new_project_form", clear_on_submit=False):
            c1, c2 = st.columns(2)
            name = c1.text_input("Project name *", placeholder="e.g. Plant-A FGD Wastewater")
            client = c2.text_input("Client")
            c3, c4 = st.columns(2)
            location = c3.text_input("Location")
            flow = c4.number_input("Design flow (gpm)", min_value=1.0, value=5000.0, step=100.0)
            desc = st.text_area("Description / notes")
            submitted = st.form_submit_button("Create project", type="primary")
        if submitted:
            if not name.strip():
                st.error("Please enter a project name.")
            else:
                create_project(name.strip(), client, location, desc, flow=flow)
                st.rerun()

    # ------------------------------------------------------------------ open / manage
    with tab_open:
        projects = list_projects()
        if not projects:
            st.info("No saved projects yet. Create one in the first tab.")
        else:
            q = st.text_input("Search projects", placeholder="name, client or location")
            shown = [p for p in projects if q.lower() in
                     f"{p['name']} {p['client']} {p['location']}".lower()] if q else projects
            st.caption(f"{len(shown)} of {len(projects)} project(s), most recently modified first")

            confirm = st.session_state.get("confirm_delete")
            for p in shown:
                with st.container(border=True):
                    c = st.columns([4, 2, 1, 1, 1, 1])
                    is_cur = bool(cur and cur["slug"] == p["slug"])
                    c[0].markdown(f"**{p['name']}**{'  ✅ *open*' if is_cur else ''}")
                    c[0].caption(" · ".join(x for x in (p["client"], p["location"],
                                                         f"{float(p['flow']):,.0f} gpm") if x))
                    c[1].caption(f"Created: {p['created'] or '-'}\n\nModified: {p['modified'] or '-'}")
                    if c[2].button("Open", key=f"open_{p['slug']}", disabled=is_cur):
                        open_project(p["slug"])
                        st.rerun()
                    if c[3].button("Copy", key=f"dup_{p['slug']}"):
                        duplicate_project(p["slug"], f"{p['name']} (copy)")
                        st.rerun()
                    c[4].download_button("Save", data=read_raw(p["slug"]),
                                         file_name=f"{p['slug']}.json", mime="application/json",
                                         key=f"dl_{p['slug']}")
                    if c[5].button("Delete", key=f"del_{p['slug']}"):
                        st.session_state["confirm_delete"] = p["slug"]
                        st.rerun()
                    if confirm == p["slug"]:
                        st.warning(f"Delete **{p['name']}** permanently? This cannot be undone.")
                        b1, b2, _ = st.columns([1, 1, 4])
                        if b1.button("Yes, delete", key=f"yes_{p['slug']}", type="primary"):
                            delete_project(p["slug"])
                            st.session_state.pop("confirm_delete", None)
                            st.rerun()
                        if b2.button("Cancel", key=f"no_{p['slug']}"):
                            st.session_state.pop("confirm_delete", None)
                            st.rerun()

    # ------------------------------------------------------------------ upload
    with tab_up:
        st.write("Upload a project file (`.json`) saved from this app. Files from the previous version "
                 "of the dashboard (flat `water_data.json`) are also accepted.")
        up = st.file_uploader("Project file", type=["json"], key="project_upload")
        if up is not None:
            if st.button("Import project", type="primary"):
                try:
                    import_project(up.getvalue(), up.name)
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not import this file: {e}")

    # ------------------------------------------------------------------ project details
    with tab_det:
        cur = active()
        if not cur:
            st.info("Open or create a project to see and edit its details.")
        else:
            m, d = cur["meta"], cur["data"]
            sl = cur["slug"]
            st.subheader("Project information")
            c1, c2 = st.columns(2)
            m["name"] = c1.text_input("Project name", value=m.get("name", sl), key=f"meta_name_{sl}") or sl
            m["client"] = c2.text_input("Client", value=m.get("client", ""), key=f"meta_client_{sl}")
            c3, c4 = st.columns(2)
            m["location"] = c3.text_input("Location", value=m.get("location", ""), key=f"meta_loc_{sl}")
            c4.text_input("Created", value=m.get("created", ""), disabled=True, key=f"meta_created_{sl}")
            m["description"] = st.text_area("Description / notes", value=m.get("description", ""),
                                            key=f"meta_desc_{sl}")
            st.subheader("Design basis summary")
            c = st.columns(4)
            c[0].metric("Flow", f"{float(d.get('flow', DEFAULTS['flow'])):,.0f} gpm")
            c[1].metric("Temperature", f"{float(d.get('temperature', DEFAULTS['temperature'])):.1f} °C")
            c[2].metric("Final use", str(d.get("final_water_use", DEFAULTS["final_water_use"])))
            c[3].metric("Last saved", str(m.get("modified", "-")))
            st.caption("Flow, temperature and final use are edited on the **Influent Feed Water** page.")
            b1, b2, _ = st.columns([1, 1, 4])
            b1.download_button("💾 Download project", data=export_json(cur),
                               file_name=f"{sl}.json", mime="application/json")
            if b2.button("Close project"):
                close_project()
                st.rerun()
            if autosave():
                st.toast("Project details saved", icon="💾")


# ======================================================================
# PAGE: Influent Feed Water
# ======================================================================
def page_influent():
    p = require_project()
    d = p["data"]
    header("Influent Feed Water", "Feed water quality and hydraulic basis")

    tab_q, tab_res = st.tabs(["💦 Feed Water Quality", "📊 Results"])

    TEXT_KEYS = ("recovery_requirement", "final_water_use")
    NAME_TO_KEY = {k.lower(): k for k in DEFAULTS}
    NAME_TO_KEY.update({v[0].lower(): k for k, v in PARAM_INFO.items()})
    NAME_TO_KEY.update({"flow (gpm)": "flow", "flow": "flow", "nitrate": "nitrate", "ammonia": "ammonia",
                        "nitrite": "nitrite", "oil & grease": "oil_grease", "oil and grease": "oil_grease"})

    with tab_q:
        with st.expander("📥 Upload / download water profile (Excel)"):
            tpl = pd.DataFrame(
                [{"Parameter": "flow", "Description": "Flow", "Unit": "gpm", "Value": DEFAULTS["flow"]}] +
                [{"Parameter": k, "Description": PARAM_INFO[k][0], "Unit": PARAM_INFO[k][1],
                  "Value": DEFAULTS[k]} for k in TRAIN_PARAMS + UNTREATED_PARAMS])
            buf = io.BytesIO()
            tpl.to_excel(buf, index=False, engine="openpyxl")
            st.download_button("⬇️ Download Excel template", data=buf.getvalue(),
                               file_name="water_profile_template.xlsx")
            up = st.file_uploader("Upload water profile (.xlsx with 'Parameter' and 'Value' columns)",
                                  type=["xlsx"], key="influent_upload")
            if up is not None:
                sig = f"{p['slug']}:{up.name}:{up.size}"
                if st.session_state.get("_profile_sig") != sig:
                    try:
                        df = pd.read_excel(up, engine="openpyxl")
                        df.columns = [str(c).strip().title() for c in df.columns]
                        applied, skipped = [], []
                        for _, row in df.iterrows():
                            key = NAME_TO_KEY.get(str(row["Parameter"]).strip().lower())
                            if key is None:
                                skipped.append(str(row["Parameter"]))
                                continue
                            try:
                                d[key] = str(row["Value"]) if key in TEXT_KEYS else float(row["Value"])
                                applied.append(key)
                            except (TypeError, ValueError):
                                skipped.append(str(row["Parameter"]))
                        clear_widget_keys(applied)
                        st.session_state["_profile_sig"] = sig
                        st.session_state["_profile_msg"] = (len(applied), skipped)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Excel load error: {e}")
                msg = st.session_state.get("_profile_msg")
                if msg:
                    st.success(f"Loaded {msg[0]} parameters from Excel.")
                    if msg[1]:
                        st.warning("Not recognised / skipped: " + ", ".join(msg[1]))

        st.subheader("General")
        input_row([("Flow (gpm)", "flow", {"min_value": 1.0, "step": 100.0}), ("pH", "ph"),
                      ("Temperature (°C)", "temperature"), ("TSS (mg/L)", "tss")], 4, min_value=0.0)
        st.subheader("Water Quality")
        input_row([("Turbidity (NTU)", "turbidity"), ("SDI", "sdi"), ("TDS (mg/L)", "tds"),
                      ("Conductivity (µS/cm)", "conductivity")], 4, min_value=0.0)
        st.subheader("Hardness & Alkalinity")
        input_row([("Calcium (mg/L)", "calcium"), ("Magnesium (mg/L)", "magnesium"),
                      ("Hardness (mg/L as CaCO3)", "hardness"),
                      ("Alkalinity (mg/L as CaCO3)", "alkalinity")], 4, min_value=0.0)
        st.subheader("Metals (mg/L)")
        input_row([(k.title(), k) for k in ["iron", "aluminum", "copper", "zinc", "nickel", "lead",
                                               "cadmium", "mercury", "arsenic", "selenium"]],
                     5, min_value=0.0, format="%.6f", step=0.001)
        st.subheader("Nutrients")
        input_row([("Nitrate-N (mg/L)", "nitrate"), ("Nitrite-N (mg/L)", "nitrite"),
                      ("Ammonia-N (mg/L)", "ammonia")], 3, min_value=0.0)
        st.subheader("Salts")
        input_row([("Sulfate (mg/L)", "sulfate"), ("Chloride (mg/L)", "chloride"),
                      ("Silica (mg/L)", "silica")], 3, min_value=0.0)
        st.subheader("Organics")
        input_row([("COD (mg/L)", "cod"), ("BOD (mg/L)", "bod"), ("Oil & Grease (mg/L)", "oil_grease")],
                     3, min_value=0.0)
        st.subheader("Final use")
        c1, c2 = st.columns(2)
        text("Recovery requirement", "recovery_requirement", c1)
        text("Final water use", "final_water_use", c2)

    A = analyze(d)
    R = A["R"]

    with tab_res:
        st.subheader("Hydraulic basis")
        metrics([("Flow", R["flow_gpm"], "gpm", ",.0f"), ("Flow", R["q_m3h"], "m³/h", ",.1f"),
                    ("Flow", R["q_m3d"], "m³/day", ",.0f")], 4)
        st.subheader("Influent water summary")
        show_df(influent_df(d, R))
        st.download_button("💾 Download water profile (JSON)",
                           data=json.dumps({k: d.get(k, DEFAULTS[k]) for k in ["flow"] + TRAIN_PARAMS +
                                            UNTREATED_PARAMS + list(TEXT_KEYS)}, indent=2),
                           file_name="water_profile.json", mime="application/json")

    finish()


# ======================================================================
# PAGE: Reaction Tank
# ======================================================================
def page_reaction_tank():
    p = require_project()
    d = p["data"]
    header("Reaction Tank", "Reaction Tanks A, B and C in series (overflow): lime / FeCl3 / polymer / organosulfide in each tank")
    top = st.container()

    RT_PARAMS = ["tss", "hardness", "iron", "aluminum", "copper", "zinc", "nickel", "lead", "cadmium",
                 "arsenic", "selenium", "mercury"]

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Chemical doses")
        input_row([("Lime dose (mg/L)", "lime_dose"), ("NaOH dose (mg/L)", "naoh_dose"),
                      ("FeCl3 dose (mg/L)", "fecl3_dose"), ("Polymer dose (mg/L)", "polymer_dose")],
                     4, min_value=0.0)
        st.caption("Removals are calculated from the FeCl3 / lime / polymer response curves, combined with the "
                   "organosulfide removal (set on the Organosulfide page).")
        st.subheader("Tank design basis")
        input_row([("Tank A time (min)", "rt_react_min"), ("Tank A G (1/s)", "rt_react_g"),
                      ("Tank B time (min)", "rt_rapid_min"), ("Tank B G (1/s)", "rt_rapid_g"),
                      ("Tank C time (min)", "rt_floc_min"), ("Tank C G (1/s)", "rt_floc_g")],
                     3, min_value=0.1)

    with tab_eq:
        st.subheader("Chemical storage & feed basis")
        st.caption("Delivered / make-up strength and days of storage drive the tank and dosing-pump sizes.")
        input_row([("Lime slurry strength (%)", "lime_conc"), ("Lime storage (days)", "lime_days"),
                      ("NaOH strength (%)", "naoh_conc"), ("NaOH storage (days)", "naoh_days"),
                      ("FeCl3 strength (%)", "fecl3_conc"), ("FeCl3 storage (days)", "fecl3_days"),
                      ("Polymer make-up strength (%)", "polymer_conc"),
                      ("Polymer make-up storage (days)", "polymer_days")], 4, min_value=0.01)

    A = analyze(d)
    with top:
        unit_pfd(A, d, "rt")
    R = A["R"]

    with tab_res:
        st.subheader("Removal performance (calculated, incl. organosulfide)")
        removal_metrics(R, "rt", ["tss", "arsenic", "selenium", "mercury", "iron", "hardness", "lead",
                                     "cadmium"], 4)
        st.subheader("Reaction tank effluent")
        effluent_metrics(R, "rt", ["tss", "arsenic", "selenium", "mercury", "iron", "hardness", "lead",
                                      "cadmium"], 4)
        show_df(removal_table(R, "rt", RT_PARAMS + ["alkalinity"]))
        st.subheader("Tank sizing")
        metrics(A["des"]["rt"]["rows"], 4)
        unit_pdf(A, "rt", "Reaction Tank")

    with tab_eq:
        equipment_panel(A, G_RT, "Reaction tanks - mixers & chemical feed systems")

    finish()


# ======================================================================
# PAGE: Clarifier
# ======================================================================
def page_clarifier():
    p = require_project()
    d = p["data"]
    header("Clarifier", "Hydraulic design, solids removal and sludge underflow pumping")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Design inputs")
        input_row([("SOR (m³/m²/hr)", "clarifier_sor", {"min_value": 0.1}),
                      ("Side water depth (m)", "clarifier_swd", {"min_value": 0.5}),
                      ("Freeboard (m)", "clarifier_freeboard", {"min_value": 0.1})], 3)
        input_row([("No. of clarifiers", "clarifier_no", {"min_value": 1})], 3, kind="int")
        st.subheader("Removal performance (%)")
        input_row([("TSS", "cl_tss_removal"), ("Arsenic", "cl_arsenic_removal"),
                      ("Selenium", "cl_selenium_removal"), ("Mercury", "cl_mercury_removal"),
                      ("Lead", "cl_lead_removal"), ("Cadmium", "cl_cadmium_removal")],
                     6, min_value=0.0, max_value=100.0)

    with tab_eq:
        st.subheader("Sludge pumping design basis")
        input_row([("Sludge pump TDH (m)", "cl_sludge_tdh"), ("Sludge pump run time (hr/day)",
                                                                  "cl_sludge_run_hr", {"max_value": 24.0})],
                     3, min_value=1.0)
        input_row([("Sludge pumps - standby", "cl_sludge_standby"),
                      ("Sludge pumps - duty", "cl_sludge_duty", {"min_value": 1})], 3, kind="int")

    A = analyze(d)
    with top:
        unit_pfd(A, d, "cl")
    R = A["R"]

    with tab_res:
        st.subheader("Clarifier design results")
        metrics(A["des"]["cl"]["rows"], 5)
        st.subheader("Clarifier effluent water quality")
        show_df(removal_table(R, "cl", ["tss", "arsenic", "selenium", "mercury", "lead", "cadmium"]))
        unit_pdf(A, "cl", "Clarifier")

    with tab_eq:
        equipment_panel(A, G_CL, "Clarifiers - splitter, mechanisms & sludge pump skid")

    finish()


# ======================================================================
# PAGE: Sand Filter
# ======================================================================
def page_sand_filter():
    p = require_project()
    d = p["data"]
    header("Sand Filter", "Filter design, backwash system and feed pumping")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Design inputs")
        input_row([("Filtration rate (m³/m²/hr)", "sf_filtration_rate", {"min_value": 1.0}),
                      ("Sand bed depth (m)", "sf_bed_depth", {"min_value": 0.5}),
                      ("Backwash rate (m/hr)", "sf_backwash_rate", {"min_value": 1.0})], 3)
        input_row([("Number of filters", "sf_num_filters", {"min_value": 1})], 3, kind="int")
        st.subheader("Removal performance (%)")
        input_row([("TSS", "sf_tss_removal"), ("Arsenic", "sf_arsenic_removal"),
                      ("Selenium", "sf_selenium_removal"), ("Mercury", "sf_mercury_removal"),
                      ("Lead", "sf_lead_removal"), ("Cadmium", "sf_cadmium_removal")],
                     6, min_value=0.0, max_value=100.0)

    with tab_eq:
        st.subheader("Backwash, tanks & pumping design basis")
        input_row([("Backwash duration (min)", "sf_bw_min"), ("Air scour rate (m³/m²/hr)", "sf_air_rate"),
                      ("Blower discharge pressure (kPa)", "sf_air_dp_kpa"),
                      ("Feed pump TDH (m)", "sf_feed_tdh"), ("Backwash pump TDH (m)", "sf_bw_tdh"),
                      ("Backwash waste return time (hr)", "sf_bw_return_hr", {"min_value": 0.5}),
                      ("Waste return pump TDH (m)", "sf_bw_return_tdh")], 4, min_value=0.0)
        input_row([("Feed pumps - duty", "sf_feed_duty", {"min_value": 1}),
                      ("Feed pumps - standby", "sf_feed_standby")], 4, kind="int")

    A = analyze(d)
    with top:
        unit_pfd(A, d, "sf")
    R = A["R"]

    with tab_res:
        st.subheader("Sand filter design results")
        metrics(A["des"]["sf"]["rows"], 4)
        st.caption("Backwash is sized for one filter at a time (filters are backwashed sequentially).")
        st.subheader("Sand filter effluent quality")
        show_df(removal_table(R, "sf", ["tss", "arsenic", "selenium", "mercury", "lead", "cadmium"]))
        unit_pdf(A, "sf", "Sand Filter")

    with tab_eq:
        equipment_panel(A, G_SF, "Sand filter - pumps, blower & tanks")

    finish()


# ======================================================================
# PAGE: Organosulfide
# ======================================================================
def page_organosulfide():
    p = require_project()
    d = p["data"]
    header("Organosulfide Treatment", "Heavy-metal precipitation - organosulfide is dosed into Reaction Tanks A, B and C")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Design inputs")
        input_row([("Organosulfide dose - total (mg/L)", "organosulfide_dose")], 3, min_value=0.0)
        st.subheader("Removal contribution (%)")
        input_row([("Hg", "os_hg_removal"), ("Cd", "os_cd_removal"), ("Pb", "os_pb_removal"),
                      ("As", "os_as_removal"), ("Se", "os_se_removal")], 5, min_value=0.0, max_value=100.0)
        st.caption("Organosulfide is injected into each reaction tank (equal split). Its removal is combined "
                   "multiplicatively with the FeCl3 / lime / polymer response; there is no separate "
                   "organosulfide reaction tank in the revised flowsheet.")

    with tab_eq:
        st.subheader("Chemical storage basis")
        input_row([("Organosulfide strength (%)", "organosulfide_conc"),
                      ("Storage (days)", "organosulfide_days")], 3, min_value=0.01)

    A = analyze(d)
    with top:
        unit_pfd(A, d, "os")
    R = A["R"]

    with tab_res:
        st.subheader("Effluent of the reaction tanks (after FeCl3 + organosulfide)")
        effluent_metrics(R, "os", ["mercury", "cadmium", "lead", "arsenic", "selenium"], 5)
        st.subheader("Organosulfide contribution")
        show_df(removal_table(R, "os", ["mercury", "cadmium", "lead", "arsenic", "selenium"]))
        metrics(A["des"]["os"]["rows"], 4)
        unit_pdf(A, "os", "Organosulfide")

    with tab_eq:
        equipment_panel(A, G_RT, "Reaction tanks - tanks, mixers & chemical feed systems")

    finish()


# ======================================================================
# PAGE: Bioreactor
# ======================================================================
def page_bioreactor():
    p = require_project()
    d = p["data"]
    header("Bioreactor", "Stage 1 and Stage 2 biological selenium / nitrate removal with methanol carbon source")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Design inputs")
        input_row([("Stage 1 HRT (hr)", "bio_stage1_hrt", {"min_value": 0.5}),
                      ("Stage 2 HRT (hr)", "bio_stage2_hrt", {"min_value": 0.5})], 4)
        input_row([("Stage 1 reactors (1A-1G)", "bio_stage1_reactors", {"min_value": 1}),
                      ("Stage 2 reactors (2A-2V)", "bio_stage2_reactors", {"min_value": 1})], 4, kind="int")
        st.subheader("Biological removal (maximum, %)")
        input_row([("Nitrate", "bio_nitrate_removal"), ("Selenium (base)", "bio_selenium_removal"),
                      ("Ammonia", "bio_ammonia_removal"), ("BOD", "bio_bod_removal"),
                      ("COD", "bio_cod_removal")], 5, min_value=0.0, max_value=100.0)
        st.subheader("Carbon source")
        input_row([("Methanol dose (mg/L)", "methanol_dose", {"max_value": 1000.0, "step": 10.0}),
                      ("Design Se loading (kg/day)", "bio_design_se_loading", {"min_value": 0.1})],
                     3, min_value=0.0)
        st.caption("Nutrient dosing, bio feed tank, strainer, heat exchanger and feed pumps are on the "
                   "**Bio Feed & Nutrient** page (previous step).")

    with tab_eq:
        st.subheader("Recirculation, backwash & methanol storage design basis")
        input_row([("Recirculation ratio (x feed flow)", "bio_recirc_ratio"),
                      ("Recirculation pump TDH (m)", "bio_recirc_tdh"),
                      ("Reactor liquid depth (m)", "bio_depth", {"min_value": 0.5}),
                      ("Backwash rate (m/hr)", "bio_bw_rate"), ("Backwash duration (min)", "bio_bw_min"),
                      ("Backwash pump TDH (m)", "bio_bw_tdh"),
                      ("Methanol strength (%)", "methanol_conc", {"min_value": 1.0}),
                      ("Methanol storage (days)", "methanol_days")], 4, min_value=0.0)
        st.caption("Backwash water comes from the UF backwash / effluent pump skid (P-702); the TDH here is "
                   "used to size it.")

    A = analyze(d)
    with top:
        unit_pfd(A, d, "bio")
    R, b = A["R"], A["R"]["bio"]

    with tab_res:
        st.subheader("Selenium performance factors")
        metrics([("Total HRT", b["total_hrt"], "hr", ".1f"), ("HRT factor", b["hrt_factor"], "", ".2f"),
                    ("Se loading", b["se_load_kg_day"], "kg/day", ".1f"),
                    ("Nitrate factor", b["nitrate_factor"], "", ".2f"),
                    ("Temp factor", b["temp_factor"], "", ".2f"),
                    ("Carbon factor", b["carbon_factor"], "", ".2f"),
                    ("Loading factor", b["loading_factor"], "", ".2f"),
                    ("Required methanol", b["required_methanol"], "mg/L", ".1f")], 4)
        st.success(f"Base Se removal = {b['base_se_removal']:.1f} %   |   "
                   f"Adjusted Se removal = {b['actual_se_removal']:.1f} %   |   "
                   f"Final selenium = {R['stream']['bio']['selenium']:.4f} mg/L")
        if gf(d, "methanol_dose") < b["required_methanol"]:
            st.warning(f"Methanol dose ({gf(d, 'methanol_dose'):.0f} mg/L) is below the indicative requirement "
                       f"({b['required_methanol']:.0f} mg/L); the carbon factor limits removal to "
                       f"{b['carbon_factor'] * 100:.0f}% of the base rate.")
        st.subheader("Bioreactor design")
        metrics(A["des"]["bio"]["rows"], 5)
        st.subheader("Bioreactor effluent quality")
        show_df(removal_table(R, "bio", ["selenium", "nitrate", "nitrite", "ammonia", "bod", "cod"]))
        unit_pdf(A, "bio", "Bioreactor")

    with tab_eq:
        equipment_panel(A, G_BIO, "Bioreactor - flow control skids, vessels, recirculation, backwash waste & methanol feed")

    finish()


# ======================================================================
# PAGE: UF System
# ======================================================================
def page_uf():
    p = require_project()
    d = p["data"]
    header("Ultrafiltration (UF) System", "UF feed sump, membrane skids, backwash / CIP systems, effluent tank, UV and discharge")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Design inputs")
        input_row([("UF flux (LMH)", "uf_flux", {"min_value": 10.0}),
                      ("Recovery (%)", "uf_recovery", {"min_value": 1.0, "max_value": 100.0}),
                      ("Module area (m²/module)", "uf_module_area", {"min_value": 1.0})], 3)
        input_row([("No. of UF skids", "uf_train_count", {"min_value": 1})], 3, kind="int")
        st.subheader("Removal performance (%)")
        input_row([("TSS", "uf_tss_removal"), ("Arsenic", "uf_arsenic_removal"),
                      ("Selenium", "uf_selenium_removal"), ("Mercury", "uf_mercury_removal"),
                      ("BOD", "uf_bod_removal"), ("COD", "uf_cod_removal")],
                     6, min_value=0.0, max_value=100.0)

    with tab_eq:
        st.subheader("Pumping, cleaning & tank design basis")
        input_row([("Backwash flux ratio (x design flux)", "uf_bw_flux_ratio"),
                      ("UF feed sump time (min)", "uf_feed_tank_min"),
                      ("Filtrate tank time (min)", "uf_tank_min"), ("UF feed pump TDH (m)", "uf_feed_tdh"),
                      ("UF sump pump TDH (m)", "uf_sump_tdh"),
                      ("Backwash / CIP pump TDH (m)", "uf_bw_tdh"),
                      ("CIP volume per module (m³)", "uf_cip_vol_per_module"),
                      ("CIP flow per module (m³/h)", "uf_cip_flow_per_module"),
                      ("CEB chemical tank (m³) [allowance]", "uf_ceb_tank_m3"),
                      ("CEB dosing pump (L/h) [allowance]", "uf_ceb_pump_lph")], 3, min_value=0.0)
        input_row([("Feed pumps - standby", "uf_feed_standby")], 3, kind="int")
        st.subheader("Effluent / backwash tank, discharge pumps & UV")
        input_row([("Effluent tank time (min)", "eff_tank_min"),
                      ("Discharge pump TDH (m)", "eff_pump_tdh"),
                      ("UV energy (kWh/m³)", "uv_kwh_m3", {"step": 0.005, "format": "%.3f"})], 3, min_value=0.0)
        input_row([("Discharge pumps - duty", "eff_pump_duty", {"min_value": 1}),
                      ("Discharge pumps - standby", "eff_pump_standby")], 3, kind="int")

    A = analyze(d)
    with top:
        unit_pfd(A, d, "uf")
    R = A["R"]

    with tab_res:
        st.subheader("UF design results")
        metrics(A["des"]["uf"]["rows"], 5)
        st.subheader("UF effluent quality")
        show_df(removal_table(R, "uf", ["tss", "arsenic", "selenium", "mercury", "nitrate", "ammonia",
                                               "bod", "cod"]))
        unit_pdf(A, "uf", "UF System")

    with tab_eq:
        equipment_panel(A, G_UF, "UF - sump, pumps, skids, tanks & CIP")
        equipment_panel(A, G_EFF, "Effluent pump skid & UV treatment")

    finish()


# ======================================================================
# PAGE: Sludge Production
# ======================================================================
def page_sludge():
    p = require_project()
    d = p["data"]
    header("Sludge Production", "Dry solids, thickened sludge, filter cake and sludge pumping / storage")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        input_row([("Thickened sludge solids (%)", "sludge_solids_percent",
                       {"min_value": 1.0, "max_value": 50.0}),
                      ("Filter cake solids (%)", "cake_solids_pct", {"min_value": 1.0, "max_value": 80.0})],
                     3)
    with tab_eq:
        st.subheader("Sludge handling design basis")
        input_row([("Sludge holding time (days)", "sludge_hold_days"),
                      ("Filter press run time (hr/day)", "sludge_press_hr", {"min_value": 1.0, "max_value": 24.0}),
                      ("Press feed pump TDH (m)", "sludge_feed_tdh"),
                      ("Filtrate pump TDH (m)", "sludge_filtrate_tdh")], 4, min_value=0.0)
        input_row([("Sludge tanks (A/B)", "sludge_tanks", {"min_value": 1}),
                      ("Vacuum filter presses (A/B/C)", "sludge_presses", {"min_value": 1})], 4, kind="int")

    A = analyze(d)
    with top:
        unit_pfd(A, d, "sludge")
    sl = A["sludge"]

    with tab_res:
        metrics([("Dry solids", sl["dry"], "kg/day", ",.0f"), ("Wet sludge", sl["wet_kg"], "kg/day", ",.0f"),
                    ("Wet sludge volume", sl["wet_m3"], "m³/day", ",.1f"),
                    ("Filter cake", sl["cake_kg"] / 1000, "t/day", ",.1f")], 4)
        show_df(sludge_df(sl, d))
        show_df(sludge_summary_df(sl, d))
        st.caption("Sand-filter / UF backwash solids and UF reject are returned via the outdoor sump to the EQ "
                   "tanks and are included in the clarifier sludge, so they are not counted twice.")
        unit_pdf(A, "sludge", "Sludge Handling")

    with tab_eq:
        equipment_panel(A, G_SLG, "Sludge tanks, press feed pumps & vacuum filter presses")

    finish()


# ======================================================================
# PAGE: Pumps & Tanks List
# ======================================================================
def page_equalization():
    p = require_project()
    d = p["data"]
    header("Equalization Tanks", "Outdoor sump, equalization tanks A / B and wastewater treatment feed pump skid")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Equalization tanks A / B")
        input_row([("EQ tank HRT - total (hr)", "eq_hrt_hr"), ("EQ mixer power (W/m³)", "eq_mixer_w_m3")],
                     3, min_value=0.0)
        input_row([("No. of EQ tanks (A/B)", "eq_tanks", {"min_value": 1})], 3, kind="int")
        st.subheader("Wastewater treatment feed pump skid")
        input_row([("Feed pump TDH (m)", "eq_feed_tdh")], 3, min_value=0.0)
        input_row([("Feed pumps - duty", "eq_feed_duty", {"min_value": 1}),
                      ("Feed pumps - standby", "eq_feed_standby")], 3, kind="int")
        st.subheader("Outdoor sump (recycles, leachate, gypsum / sludge dewatering)")
        input_row([("Sump flow (% of plant flow)", "eq_sump_frac"), ("Sump retention (min)", "eq_sump_min"),
                      ("Sump pump TDH (m)", "eq_sump_tdh")], 3, min_value=0.0)

    A = analyze(d)
    with top:
        unit_pfd(A, d, "eq")
    R = A["R"]

    with tab_res:
        st.subheader("Hydraulic basis & equalization tank design")
        metrics([("Flow", R["flow_gpm"], "gpm", ",.0f"), ("Flow", R["q_m3h"], "m³/h", ",.1f"),
                    ("Flow", R["q_m3d"], "m³/day", ",.0f")] + A["des"]["eq"]["rows"][1:], 4)
        unit_pdf(A, "eq", "Equalization")

    with tab_eq:
        equipment_panel(A, G_INF, "Outdoor sump, equalization tanks, feed pumps & mixers")

    finish()


def page_clearwell():
    p = require_project()
    d = p["data"]
    header("Clearwell (HCl)", "Clarified water is neutralized with HCl in the mixed clearwell tank")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("HCl dosing & clearwell tank")
        input_row([("HCl dose in clearwell (mg/L)", "hcl_dose"),
                      ("HCl strength (%)", "hcl_conc", {"min_value": 1.0}),
                      ("HCl storage (days)", "hcl_days"),
                      ("Clearwell retention (min)", "cw_min"),
                      ("Clearwell mixer (W/m³)", "cw_mixer_w_m3")], 5, min_value=0.0)
        st.caption("HCl dose is a placeholder - set it from the lime-treated pH and alkalinity.")

    A = analyze(d)
    with top:
        unit_pfd(A, d, "cw")
    c = A["chem"]["hcl"]

    with tab_res:
        st.subheader("HCl consumption")
        metrics([("HCl consumption", c["kg_day"], "kg/day", ",.0f"), ("Product flow", c["lph"], "L/h", ",.1f"),
                    ("Storage tank", c["tank_m3"], "m³", ",.1f"),
                    ("Cost", c["cost_day"], "$/day", ",.0f")], 4)
        st.caption("Clearwell does not change modelled metal / selenium concentrations; it only adjusts pH.")
        unit_pdf(A, "cw", "Clearwell")

    with tab_eq:
        equipment_panel(A, G_CW, "Clearwell tank, mixer, HCl dosing skid & vapor scrubber")

    finish()


def page_bio_feed():
    p = require_project()
    d = p["data"]
    header("Bio Feed & Nutrient", "Bio feed tank (nutrient mixed in), nutrient injection pump skid, bio feed pumps, strainer and heat exchanger")
    top = st.container()

    tab_in, tab_res, tab_eq = st.tabs(["⚙️ Design Inputs", "📊 Results", "🛠️ Pumps & Tanks"])

    with tab_in:
        st.subheader("Nutrient (H3PO4) tank & injection pump skid")
        input_row([("Nutrient dose (mg/L)", "nutrient_dose"),
                      ("Nutrient strength (%)", "nutrient_conc", {"min_value": 1.0}),
                      ("Nutrient storage (days)", "nutrient_days")], 3, min_value=0.0)
        input_row([("Nutrient injection pumps - duty (+1 standby)", "nutrient_pump_duty", {"min_value": 1})],
                     3, kind="int")
        st.subheader("Bio feed tank, strainer & heat exchanger")
        input_row([("Bio feed tank retention (min)", "bio_feed_tank_min"),
                      ("Bio feed tank mixer (W/m³)", "bio_feed_tank_w_m3"),
                      ("Strainer opening (µm)", "bio_strainer_um"),
                      ("Heat exchanger target temp (°C)", "bio_hx_target_c")], 4, min_value=0.0)
        st.subheader("Bio feed pump skid")
        input_row([("Feed pump TDH (m)", "bio_feed_tdh")], 3, min_value=0.0)
        input_row([("Feed pumps - duty", "bio_feed_duty", {"min_value": 1}),
                      ("Feed pumps - standby", "bio_feed_standby")], 3, kind="int")

    A = analyze(d)
    with top:
        unit_pfd(A, d, "bf")
    n = A["chem"]["nutrient"]

    with tab_res:
        st.subheader("Nutrient consumption")
        metrics([("Nutrient consumption", n["kg_day"], "kg/day", ",.1f"), ("Product flow", n["lph"], "L/h", ",.1f"),
                    ("Storage tank", n["tank_m3"], "m³", ",.1f"), ("Cost", n["cost_day"], "$/day", ",.1f")], 4)
        st.subheader("Bioreactor feed temperature")
        t_in, t_tgt = gf(d, "temperature"), gf(d, "bio_hx_target_c")
        metrics([("Influent temperature", t_in, "°C", ".1f"), ("Heat exchanger target", t_tgt, "°C", ".1f"),
                    ("Temperature factor used", A["R"]["bio"]["temp_factor"], "", ".2f")], 3)
        unit_pdf(A, "bf", "Bio Feed & Nutrient")

    with tab_eq:
        equipment_panel(A, G_BF, "Bio feed tank, nutrient tank & pumps, strainer, heat exchanger, feed pumps")

    finish()


def page_equipment_list():
    p = require_project()
    d = p["data"]
    header("Pumps & Tanks List", "Plant-wide equipment schedule: pumps, tanks / vessels, mixers and blowers")

    with st.expander("⚙️ Global design basis", expanded=False):
        input_row([("Pump efficiency (centrifugal)", "pump_eff", {"min_value": 0.2, "max_value": 0.9,
                                                                      "step": 0.01}),
                      ("Pump flow margin (%)", "pump_margin_pct"),
                      ("Tank freeboard / design margin (%)", "tank_freeboard_pct")], 3, min_value=0.0)
        st.caption("Unit-specific design basis (TDH, retention times, storage days, duty/standby counts) is "
                   "entered on each process page under **Pumps & Tanks**.")

    A = analyze(d)
    sel = st.selectbox("Area", ["All areas"] + GROUPS)
    rows = A["eq"] if sel == "All areas" else filter_rows(A["eq"], sel)
    pumps, tanks, others = equipment_frames(rows, include_group=True)
    ps = power_summary(rows)

    c = st.columns(5)
    c[0].metric("Pump tags", f"{len(pumps)}")
    c[1].metric("Pumps installed", f"{sum(r['duty'] + r['standby'] for r in rows if r['kind'] == 'pump')}")
    c[2].metric("Tank / vessel tags", f"{len(tanks)}")
    c[3].metric("Installed power", f"{ps['installed_kw']:,.0f} kW")
    c[4].metric("Average running power", f"{ps['running_kw']:,.0f} kW")

    tp, tt, to, tw = st.tabs(["Pumps", "Tanks & Vessels", "Mixers & Mechanical", "Power by Area"])
    with tp:
        show_df(pumps)
        st.caption("Flow includes the pump flow margin. Absorbed kW = Q·H·SG / (367·η). "
                   "Run factor = fraction of time the duty pump operates (intermittent duties < 1).")
    with tt:
        show_df(tanks)
    with to:
        show_df(others)
    with tw:
        show_df(power_by_group_df(A["eq"]))
    section_pdf(A, ["equipment"], "Equipment List", "equipment")
    finish()


# ======================================================================
# PAGE: Process Flow Diagram
# ======================================================================
def page_pfd():
    p = require_project()
    d = p["data"]
    header("Process Flow Diagram", "Treatment train with pumps, tanks, chemical feeds, backwash and sludge circuits")

    A = analyze(d)
    render_pfd(A, d, "page")
    st.caption("Red arrows = chemical dosing, green boxes = instruments, dashed lines = recycle / backwash / "
               "reject returns (via the outdoor sump to the EQ tanks). Se = selenium concentration leaving "
               "each unit. Open the large view below the diagram for a 100 % scrollable version.")

    st.subheader("Water quality through the treatment train")
    show_df(stage_profile_df(A["R"]))

    st.subheader("Online instruments (pH / ORP / H2S / DO)")
    show_df(instrument_df())

    c = st.columns(3)
    c[0].metric("CAPEX", f"${A['cost']['total_capex']:,.0f}")
    c[1].metric("Annual OPEX", f"${A['cost']['total_opex']:,.0f}")
    c[2].metric("Dry sludge", f"{A['sludge']['dry']:,.0f} kg/day")
    section_pdf(A, ["pfd", "quality"], "Process Flow Diagram", "pfd")
    finish()


# ======================================================================
# PAGE: Chemical Consumption
# ======================================================================
def page_chemical_consumption():
    p = require_project()
    d = p["data"]
    header("Chemical Consumption", "Daily consumption, delivered product flow and storage for every chemical")

    st.caption("Doses are set on the Reaction Tank, Organosulfide, Bioreactor and Pumps & Tanks List pages; "
               "strengths and storage days on each unit's Pumps & Tanks tab.")
    A = analyze(d)
    chem = A["chem"]

    c = st.columns(4)
    c[0].metric("Flow", f"{A['R']['q_m3d']:,.0f} m³/day")
    c[1].metric("Total active chemical", f"{sum(x['kg_day'] for x in chem.values()):,.0f} kg/day")
    c[2].metric("Largest consumer", max(chem.values(), key=lambda x: x["kg_day"])["name"])
    c[3].metric("Total storage required", f"{sum(x['tank_m3'] for x in chem.values() if x['kg_day'] > 0):,.0f} m³")

    show_df(chem_df(chem))

    st.subheader("Chemical storage tanks & dosing pumps")
    rows = [r for r in A["eq"] if r.get("chem")]
    pumps, tanks, others = equipment_frames(rows, include_group=True)
    show_df(tanks)
    show_df(pumps)
    show_df(others)
    section_pdf(A, ["chemicals"], "Chemical Consumption", "chem")
    finish()


# ======================================================================
# PAGE: Chemical Cost
# ======================================================================
def page_chemical_cost():
    p = require_project()
    d = p["data"]
    header("Chemical Cost", "Unit costs and daily / monthly / annual chemical cost")

    st.subheader("Unit costs ($/kg of active chemical)")
    input_row([("Lime", "lime_cost"), ("NaOH", "naoh_cost"), ("FeCl3", "fecl3_cost"),
                  ("Polymer", "polymer_cost"), ("Organosulfide", "organosulfide_cost"),
                  ("Methanol", "methanol_cost"), ("Nutrient (H3PO4)", "nutrient_cost"),
                  ("HCl (clearwell)", "hcl_cost")], 4, min_value=0.0)

    A = analyze(d)
    chem = A["chem"]
    total_day = sum(c["cost_day"] for c in chem.values())

    df = pd.DataFrame([{"Chemical": c["name"], "Consumption (kg/day)": c["kg_day"],
                        "Unit cost ($/kg)": c["unit_cost"], "Cost ($/day)": c["cost_day"],
                        "Cost ($/year)": c["cost_day"] * 365,
                        "Share (%)": c["cost_day"] / total_day * 100 if total_day else 0.0}
                       for c in chem.values()])
    show_df(df)

    c = st.columns(4)
    c[0].metric("Daily cost", f"${total_day:,.2f}")
    c[1].metric("Monthly cost", f"${total_day * 30:,.2f}")
    c[2].metric("Annual cost", f"${total_day * 365:,.2f}")
    c[3].metric("Cost per m³ treated", f"${total_day / A['R']['q_m3d']:.3f}")
    if total_day > 0:
        st.bar_chart(df.set_index("Chemical")["Cost ($/day)"])
    section_pdf(A, ["chemicals"], "Chemical Cost", "chemcost")
    finish()


# ======================================================================
# PAGE: CAPEX & OPEX
# ======================================================================
def page_capex_opex():
    p = require_project()
    d = p["data"]
    header("CAPEX & OPEX", "Budget-level capital and operating cost build-up")

    tab_in, tab_res = st.tabs(["⚙️ Cost Inputs", "📊 Results"])

    with tab_in:
        st.subheader("Process unit cost ($) - vessels, media, membranes, mechanisms")
        input_row([("Reaction tank system", "reaction_tank_cost"), ("Clarifier", "clarifier_cost"),
                      ("Sand filter", "sand_filter_cost"), ("Organosulfide system", "organosulfide_cost_capex"),
                      ("Bioreactor", "bioreactor_cost"), ("UF system", "uf_cost"),
                      ("Sludge dewatering system", "sludge_system_cost")], 4, min_value=0.0)
        st.subheader("Pumps & tanks - unit rates (applied to the calculated equipment list)")
        input_row([("Pump cost ($ per installed kW)", "pump_cost_per_kw"),
                      ("Tank cost ($ per m³ design volume)", "tank_cost_per_m3")], 4, min_value=0.0)
        st.subheader("Project factors (% of direct cost)")
        input_row([("Installation & indirects (%)", "install_pct"), ("Engineering (%)", "eng_pct"),
                      ("Contingency (%)", "cont_pct")], 4, min_value=0.0)
        st.subheader("Operating cost")
        c1, c2 = st.columns(2)
        basis = choice("Electricity basis", "power_basis",
                          ["Calculated from equipment list", "Manual lump sum"], c1)
        num("Power tariff ($/kWh)", "power_tariff", c1, min_value=0.0, step=0.01)
        if basis.startswith("Manual"):
            num("Power cost ($/year)", "electricity_cost", c1, min_value=0.0)
        sbasis = choice("Sludge disposal basis", "sludge_basis",
                           ["Calculated ($/tonne cake)", "Manual lump sum"], c2)
        if sbasis.startswith("Calc"):
            num("Disposal cost ($/tonne cake)", "disposal_per_t", c2, min_value=0.0)
        else:
            num("Sludge disposal ($/year)", "sludge_disposal_cost", c2, min_value=0.0)
        input_row([("Labor ($/year)", "labor_cost"), ("Maintenance (% of direct cost)", "maintenance_percent"),
                      ("Discount rate (%)", "discount_rate"), ("Plant life (years)", "life_years")],
                     4, min_value=0.0)

    A = analyze(d)
    cost = A["cost"]

    with tab_res:
        c = st.columns(4)
        c[0].metric("Total CAPEX", f"${cost['total_capex']:,.0f}")
        c[1].metric("Annual OPEX", f"${cost['total_opex']:,.0f}")
        c[2].metric("OPEX per m³", f"${cost['opex_per_m3']:.3f}")
        c[3].metric("Total cost per m³", f"${cost['total_per_m3']:.3f}",
                    help="OPEX plus CAPEX annualized at the discount rate and plant life")
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("CAPEX breakdown")
            show_df(capex_df(cost))
            items = pd.DataFrame(cost["capex_items"], columns=["Item", "Cost ($)"]).set_index("Item")
            st.bar_chart(items)
        with c2:
            st.subheader("OPEX breakdown")
            show_df(opex_df(cost))
            items = pd.DataFrame(cost["opex_items"], columns=["Item", "Cost ($/year)"]).set_index("Item")
            st.bar_chart(items)
        st.subheader("Unit cost & energy")
        show_df(unit_cost_df(cost))
        section_pdf(A, ["cost"], "CAPEX & OPEX", "cost")

    finish()


# ======================================================================
# PAGE: Compliance Limits
# ======================================================================
def page_compliance():
    p = require_project()
    d = p["data"]
    header("Compliance Limits", "Permit limits used to check the final effluent")

    st.subheader("Permit limits (mg/L)")
    input_row([("TSS", "limit_tss"), ("Arsenic", "limit_arsenic", {"format": "%.6f", "step": 0.001}),
                  ("Selenium", "limit_selenium", {"format": "%.6f", "step": 0.001}),
                  ("Mercury", "limit_mercury", {"format": "%.6f", "step": 0.00001})], 4, min_value=0.0)
    input_row([("Nitrate", "limit_nitrate"), ("Ammonia", "limit_ammonia"), ("BOD", "limit_bod"),
                  ("COD", "limit_cod")], 4, min_value=0.0)

    A = analyze(d)
    comp = compliance_df(d, A["R"])
    show = comp.copy()
    show["Status"] = show["Status"].map({"PASS": "✅ PASS", "FAIL": "❌ FAIL"})
    st.subheader("Current design vs. limits")
    show_df(show)
    n_fail = int((comp["Status"] == "FAIL").sum())
    if n_fail:
        st.warning(f"{n_fail} parameter(s) exceed the limits with the current design.")
    else:
        st.success("All limits are met.")
    finish()


# ======================================================================
# PAGE: Final Effluent Water
# ======================================================================
def page_effluent_water():
    p = require_project()
    d = p["data"]
    header("Final Effluent Water", "Treated water quality, removal through the train and compliance with permit limits")

    A = analyze(d)
    R, des = A["R"], A["des"]
    comp = compliance_df(d, R)
    n_fail = int((comp["Status"] == "FAIL").sum())

    c = st.columns(4)
    c[0].metric("Treated flow", f"{des['uf']['permeate']:,.0f} m³/h", f"{des['uf']['permeate'] / 0.2271:,.0f} gpm",
                delta_color="off")
    c[1].metric("Limits met", f"{len(comp) - n_fail} / {len(comp)}")
    c[2].metric("Final selenium", f"{R['final']['selenium']:.4f} mg/L")
    se_rem = (1 - R["final"]["selenium"] / R["stream"]["inf"]["selenium"]) * 100 \
        if R["stream"]["inf"]["selenium"] > 0 else 0
    c[3].metric("Overall Se removal", f"{se_rem:.1f} %")

    if n_fail == 0:
        st.success("✅ The final effluent meets all permit limits.")
    else:
        st.error(f"❌ {n_fail} limit(s) exceeded: " + ", ".join(comp[comp["Status"] == "FAIL"]["Parameter"]))

    tab_c, tab_all, tab_chart, tab_adv = st.tabs(["🎯 Compliance", "🧾 All Parameters", "📉 Through the Train",
                                                   "💡 Improvement Hints"])

    with tab_c:
        show = comp.copy()
        show["Status"] = show["Status"].map({"PASS": "✅ PASS", "FAIL": "❌ FAIL"})
        show_df(show)

    with tab_all:
        rows = []
        for p_ in TRAIN_PARAMS:
            inf, fin = R["stream"]["inf"][p_], R["final"][p_]
            rows.append({"Parameter": PARAM_INFO[p_][0], "Unit": PARAM_INFO[p_][1], "Influent": inf,
                         "Final effluent": fin,
                         "Overall removal (%)": (inf - fin) / inf * 100 if inf > 0 else 0.0})
        st.markdown("**Treated parameters**")
        show_df(pd.DataFrame(rows))
        st.markdown("**Parameters not treated by this train (assumed unchanged)**")
        show_df(pd.DataFrame([{"Parameter": PARAM_INFO[x][0], "Unit": PARAM_INFO[x][1], "Value": gf(d, x)}
                                 for x in UNTREATED_PARAMS if x not in ("ph", "temperature")]))

    with tab_chart:
        param = st.selectbox("Parameter", TRAIN_PARAMS, index=TRAIN_PARAMS.index("selenium"),
                             format_func=lambda x: f"{PARAM_INFO[x][0]} ({PARAM_INFO[x][1]})")
        series = pd.DataFrame({"Concentration": [R["stream"][k][param] for k, _ in STAGES]},
                              index=[lbl for _, lbl in STAGES])
        st.bar_chart(series)
        lim = {p_: gf(d, lk) for p_, lk in COMPLIANCE}.get(param)
        if lim is not None:
            meet = first_stage_meeting(R, param, lim)
            st.caption(f"Permit limit: **{lim}** {PARAM_INFO[param][1]}. " +
                       (f"First met after: **{meet}**." if meet else "Not met at any stage."))
        show_df(stage_profile_df(R))

    with tab_adv:
        hints = {
            "selenium": "Raise the methanol dose (carbon factor), lengthen bioreactor HRT, add reactors, or raise the "
                        "base Se removal; also check nitrate loading, since nitrate competes for carbon.",
            "nitrate": "Raise the methanol dose or total bioreactor HRT; confirm the bioreactor temperature is above 20 °C "
                       "(heat exchanger target).",
            "mercury": "Increase the organosulfide dose or the FeCl3 dose in the reaction tanks.",
            "arsenic": "Increase the FeCl3 dose in the reaction tanks (arsenic co-precipitates with ferric hydroxide).",
            "tss": "Check clarifier SOR, sand filter rate and UF TSS removal.",
            "ammonia": "Raise the biological ammonia removal assumption or add a nitrification step.",
            "bod": "Increase bioreactor BOD removal / HRT or reduce excess methanol dosing.",
            "cod": "Increase bioreactor COD removal / HRT or reduce excess methanol dosing.",
        }
        fails = comp[comp["Status"] == "FAIL"]
        if fails.empty:
            st.success("No exceedances - nothing to fix.")
        for _, r in fails.iterrows():
            key = next(k for k in hints if PARAM_INFO[k][0] == r["Parameter"])
            st.markdown(f"**{r['Parameter']}** - effluent {r['Final Effluent']:.6g} vs. limit {r['Limit']:.6g} "
                        f"{r['Unit']}")
            st.write(hints[key])

    section_pdf(A, ["effluent"], "Final Effluent", "effluent")
    finish()


# ======================================================================
# PAGE: Detailed Output
# ======================================================================
def page_detailed_report():
    p = require_project()
    d = p["data"]
    header("Detailed Output", "Complete design output: every section of the report on screen")

    A = analyze(d)
    sections = build_report(p, A)

    c1, c2, c3 = st.columns(3)
    c1.download_button("📊 Download all tables (Excel)", data=report_to_excel(sections),
                       file_name=f"{slugify(p['meta'].get('name', 'project'))}_detailed_output.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c2.download_button("💾 Download project (JSON)", data=export_json(p),
                       file_name=f"{p['slug']}.json", mime="application/json")
    c3.page_link(PAGES["pdf"], label="Go to PDF report", icon="📄")

    for i, sec in enumerate(sections, start=1):
        with st.expander(f"{i}. {sec['title']}", expanded=(i == 1)):
            for blk in sec["blocks"]:
                kind = blk[0]
                if kind == "h2":
                    st.subheader(blk[1])
                elif kind == "text":
                    st.write(blk[1])
                elif kind == "table":
                    if blk[1] is not None and len(blk[1]):
                        st.caption(blk[2])
                        show_df(blk[1])
                elif kind == "pfd":
                    render_pfd(A, d, "detail", expand_large=False)
    finish()


# ======================================================================
# PAGE: PDF Report
# ======================================================================
def page_pdf_report():
    p = require_project()
    d = p["data"]
    header("PDF Report", "Generate the complete design report as a PDF")

    A = analyze(d)
    sections = build_report(p, A)

    c1, c2 = st.columns(2)
    prepared_by = c1.text_input("Prepared by", value=st.session_state.get("pdf_prepared_by", ""))
    company = c2.text_input("Company", value=st.session_state.get("pdf_company", "HSKH"))
    st.session_state["pdf_prepared_by"], st.session_state["pdf_company"] = prepared_by, company

    titles = {sid: t for sid, t in SECTION_TITLES}
    chosen = st.multiselect("Sections to include", [sid for sid, _ in SECTION_TITLES],
                            default=[sid for sid, _ in SECTION_TITLES], format_func=lambda x: titles[x])

    if not chosen:
        st.warning("Select at least one section.")
    else:
        pdf_button("full design report",
                      lambda: build_pdf(p, A, [s for s in sections if s["id"] in chosen],
                                        prepared_by=prepared_by, company=company),
                      f"{slugify(p['meta'].get('name', 'project'))}_Design_Report.pdf", "full",
                      extra=",".join(chosen) + prepared_by + company)
    st.caption("The report contains the design basis, unit designs, pump / tank / mixer schedules, water quality "
               "through the train, chemicals, sludge, CAPEX / OPEX, the process flow diagram and the final "
               "effluent compliance table.")
    finish()


# ======================================================================
# SAMPLE PROJECT + APP ENTRY
# ======================================================================
SAMPLE_PROJECT = {'meta': {'name': 'Sample FGD Project', 'client': 'Demo Client', 'location': 'Demo Site', 'description': 'Example project (passes all permit limits). Edit or delete freely.', 'created': '2026-11-09 09:00:00', 'modified': '2026-11-09 09:00:00'}, 'data': {'flow': 5000, 'selenium': 0.08, 'methanol_dose': 1000, 'bio_stage1_hrt': 6, 'bio_stage2_hrt': 14, 'bio_selenium_removal': 99.5, 'bio_nitrate_removal': 99.5}}


def seed_sample():
    """Create the demo project once, the first time the app runs with no valid projects."""
    marker = PROJECT_DIR / ".sample_seeded"
    if marker.exists() or list_projects():
        return
    (PROJECT_DIR / "Sample_FGD_Project.json").write_text(json.dumps(SAMPLE_PROJECT, indent=2),
                                                         encoding="utf-8")
    marker.write_text("1")


def main():
    seed_sample()
    inject_theme()
    resume_last()
    spec = [
        ("Project", "project_manager", page_project_manager, "Project Manager", "💾", "projects"),
        ("Process Design (flow sequence)", "pfd", page_pfd, "Process Flow Diagram", "📉", "pfd"),
        ("Process Design (flow sequence)", "influent", page_influent, "1 · Influent Feed Water", "💦", "influent"),
        ("Process Design (flow sequence)", "equalization", page_equalization, "2 · Equalization Tanks", "🛢️", "equalization"),
        ("Process Design (flow sequence)", "reaction_tank", page_reaction_tank, "3 · Reaction Tanks A-C", "⚗️", "reaction-tank"),
        ("Process Design (flow sequence)", "organosulfide", page_organosulfide, "4 · Organosulfide Dosing", "🧪", "organosulfide"),
        ("Process Design (flow sequence)", "clarifier", page_clarifier, "5 · Clarifiers", "🏗️", "clarifier"),
        ("Process Design (flow sequence)", "sludge", page_sludge, "6 · Sludge Handling", "🟫", "sludge"),
        ("Process Design (flow sequence)", "clearwell", page_clearwell, "7 · Clearwell (HCl)", "🧊", "clearwell"),
        ("Process Design (flow sequence)", "sand_filter", page_sand_filter, "8 · Sand Filters", "🌀", "sand-filter"),
        ("Process Design (flow sequence)", "bio_feed", page_bio_feed, "9 · Bio Feed & Nutrient", "🧬", "bio-feed"),
        ("Process Design (flow sequence)", "bioreactor", page_bioreactor, "10 · Bioreactor", "🦠", "bioreactor"),
        ("Process Design (flow sequence)", "uf", page_uf, "11 · UF, UV & Discharge", "🧫", "uf"),
        ("Process Design (flow sequence)", "equipment_list", page_equipment_list, "Pumps & Tanks List", "🛠️", "equipment"),
        ("Operations & Cost", "chemical_consumption", page_chemical_consumption, "Chemical Consumption", "🧴", "chem-consumption"),
        ("Operations & Cost", "chemical_cost", page_chemical_cost, "Chemical Cost", "💰", "chem-cost"),
        ("Operations & Cost", "capex_opex", page_capex_opex, "CAPEX & OPEX", "📈", "capex-opex"),
        ("Operations & Cost", "compliance", page_compliance, "Compliance Limits", "🎯", "compliance"),
        ("Reports", "effluent_water", page_effluent_water, "Final Effluent Water", "📊", "effluent"),
        ("Reports", "detailed_report", page_detailed_report, "Detailed Output", "📘", "detailed"),
        ("Reports", "pdf_report", page_pdf_report, "PDF Report", "📄", "pdf-report"),
    ]
    nav = {}
    for grp, key, fn, title, icon, url in spec:
        page = st.Page(fn, title=title, icon=icon, url_path=url, default=(key == "project_manager"))
        nav.setdefault(grp, []).append(page)
        PAGES[{"project_manager": "pm", "pdf_report": "pdf"}.get(key, key)] = page
    pg = st.navigation(nav)
    sidebar_status()
    pg.run()


main()