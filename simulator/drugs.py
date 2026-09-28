"""A fixed formulary of ~30 common drugs.

Each entry carries a canonical form and one `messy_desc` — how a real PMS might
print the same drug. The messy variant is what later gives the duplicate fault
its "small differences", and what `normalizer` will have to fold back together.
"""

from __future__ import annotations

from simulator.models import Drug

# (name, strength, form, messy_desc)
_FORMULARY: tuple[tuple[str, str, str, str], ...] = (
    ("atorvastatin", "20 mg", "tablet", "ATORVASTATIN CALCIUM 20 MG TAB"),
    ("lisinopril", "10 mg", "tablet", "LISINOPRIL 10MG TABS"),
    ("levothyroxine", "50 mcg", "tablet", "LEVOTHYROXINE SODIUM 50MCG TAB"),
    ("metformin", "500 mg", "tablet", "METFORMIN HCL 500 MG TAB"),
    ("amlodipine", "5 mg", "tablet", "AMLODIPINE BESYLATE 5MG TAB"),
    ("metoprolol", "25 mg", "tablet", "METOPROLOL TARTRATE 25 MG TAB"),
    ("omeprazole", "20 mg", "capsule", "OMEPRAZOLE 20MG CAP DR"),
    ("simvastatin", "20 mg", "tablet", "SIMVASTATIN 20 MG TABS"),
    ("losartan", "50 mg", "tablet", "LOSARTAN POTASSIUM 50MG TAB"),
    ("albuterol", "90 mcg", "inhaler", "ALBUTEROL SULFATE HFA 90MCG INH"),
    ("gabapentin", "300 mg", "capsule", "GABAPENTIN 300MG CAPS"),
    ("hydrochlorothiazide", "25 mg", "tablet", "HCTZ 25 MG TAB"),
    ("sertraline", "50 mg", "tablet", "SERTRALINE HCL 50MG TAB"),
    ("montelukast", "10 mg", "tablet", "MONTELUKAST SODIUM 10 MG TAB"),
    ("rosuvastatin", "10 mg", "tablet", "ROSUVASTATIN CALCIUM 10MG TAB"),
    ("escitalopram", "10 mg", "tablet", "ESCITALOPRAM OXALATE 10 MG TAB"),
    ("bupropion", "150 mg", "tablet", "BUPROPION HCL XL 150MG TAB"),
    ("furosemide", "40 mg", "tablet", "FUROSEMIDE 40 MG TABS"),
    ("pantoprazole", "40 mg", "tablet", "PANTOPRAZOLE SODIUM 40MG TAB DR"),
    ("trazodone", "50 mg", "tablet", "TRAZODONE HCL 50 MG TAB"),
    ("duloxetine", "30 mg", "capsule", "DULOXETINE HCL 30MG CAP DR"),
    ("prednisone", "10 mg", "tablet", "PREDNISONE 10 MG TABS"),
    ("tamsulosin", "0.4 mg", "capsule", "TAMSULOSIN HCL 0.4MG CAP"),
    ("warfarin", "5 mg", "tablet", "WARFARIN SODIUM 5 MG TAB"),
    ("clopidogrel", "75 mg", "tablet", "CLOPIDOGREL BISULFATE 75MG TAB"),
    ("glipizide", "5 mg", "tablet", "GLIPIZIDE 5 MG TABS"),
    ("citalopram", "20 mg", "tablet", "CITALOPRAM HBR 20MG TAB"),
    ("fluoxetine", "20 mg", "capsule", "FLUOXETINE HCL 20 MG CAP"),
    ("carvedilol", "12.5 mg", "tablet", "CARVEDILOL 12.5MG TAB"),
    ("insulin glargine", "100 unit/mL", "pen", "INSULIN GLARGINE 100UNIT/ML PEN"),
)

DRUGS: tuple[Drug, ...] = tuple(
    Drug(
        drug_id=f"D{index:02d}",
        name=name,
        strength=strength,
        form=form,
        messy_desc=messy,
    )
    for index, (name, strength, form, messy) in enumerate(_FORMULARY, start=1)
)
