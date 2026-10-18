"""Load demo facilities and offers so the platform has something to match.

Run with: ./.venv/bin/python -m scripts.seed_demo
Device facts here are typed from real published specifications, with each field
labelled as stated by the donor rather than read off a plate.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.data import marketplace_repo as repo
from app.data import repository
from app.data.seed import FACILITIES
from app.domain.device import DeviceFacts
from app.domain.marketplace import GeoPoint, Offer
from app.domain.provenance import Provenance, fact

STATED = Provenance.DONOR_STATED
OFFER_WINDOW_DAYS = 180


def _device(**kwargs) -> DeviceFacts:
    return DeviceFacts(**{k: fact(v, STATED) for k, v in kwargs.items()})


DEMO_OFFERS = (
    Offer(
        donor_name="Baptist Health biomedical department",
        donor_email="biomed@example.org",
        device_type="ventilator",
        origin=GeoPoint(country="United States", city="Nashville", latitude=36.1627, longitude=-86.7816),
        quantity_offered=3,
        device=_device(
            manufacturer="Puritan Bennett", model="840", year_of_manufacture=2009,
            voltage_min_v=100, voltage_max_v=120, frequencies_hz=[50, 60], power_watts=400,
            temp_min_c=10.0, temp_max_c=40.0, humidity_max_pct=95.0,
            needs_oxygen=True, needs_compressed_air=True, needs_distilled_water=False,
            consumables=["patient circuits", "bacterial filters", "oxygen sensors"],
            manual_languages=["English"], operating_complexity="high",
        ),
    ),
    Offer(
        donor_name="St Mary's Hospital surplus",
        donor_email="surplus@example.org",
        device_type="anaesthesia machine",
        origin=GeoPoint(country="United States", city="Chicago", latitude=41.8781, longitude=-87.6298),
        quantity_offered=2,
        device=_device(
            manufacturer="Drager", model="Fabius Tiro", year_of_manufacture=2019,
            voltage_min_v=220, voltage_max_v=240, frequencies_hz=[50, 60], power_watts=150,
            temp_min_c=10.0, temp_max_c=40.0, humidity_max_pct=80.0,
            needs_oxygen=True, needs_compressed_air=True, needs_distilled_water=False,
            consumables=["soda lime canisters", "breathing circuits"],
            manual_languages=["English", "French"], operating_complexity="high",
        ),
    ),
    Offer(
        donor_name="Nairobi Equipment Trust",
        donor_email="trust@example.org",
        device_type="patient monitor",
        origin=GeoPoint(country="Kenya", city="Nairobi", latitude=-1.2921, longitude=36.8219),
        quantity_offered=6,
        device=_device(
            manufacturer="Philips", model="SureSigns VM4", year_of_manufacture=2021,
            voltage_min_v=100, voltage_max_v=240, frequencies_hz=[50, 60], power_watts=60,
            temp_min_c=0.0, temp_max_c=45.0, humidity_max_pct=90.0,
            needs_oxygen=False, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["SpO2 sensors", "blood pressure cuffs"],
            manual_languages=["English", "French", "Swahili"], operating_complexity="low",
        ),
    ),
    Offer(
        donor_name="Gulf Coast Medical Center",
        donor_email="surplus@example.org",
        device_type="ventilator",
        origin=GeoPoint(country="Kenya", city="Mombasa", latitude=-4.0435, longitude=39.6682),
        quantity_offered=2,
        device=_device(
            manufacturer="Hamilton Medical", model="C1", year_of_manufacture=2020,
            voltage_min_v=100, voltage_max_v=240, frequencies_hz=[50, 60], power_watts=120,
            temp_min_c=0.0, temp_max_c=40.0, humidity_max_pct=90.0,
            needs_oxygen=True, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["patient circuits", "bacterial filters"],
            manual_languages=["English"], operating_complexity="high",
        ),
    ),
)

# Imaging for the demo. The two X-ray units are the substitution case: a
# stationary system needs three-phase supply that none of the seeded clinics
# have, so it is blocked everywhere and the mobile unit is what can actually go.
IMAGING_OFFERS = (
    Offer(
        donor_name="Vanderbilt Radiology surplus",
        donor_email="radiology@example.org",
        device_type="x-ray system",
        origin=GeoPoint(country="United States", city="Nashville", latitude=36.1627, longitude=-86.7816),
        quantity_offered=1,
        device=_device(
            manufacturer="Siemens", model="Multix Fusion", year_of_manufacture=2018,
            voltage_min_v=380, voltage_max_v=480, frequencies_hz=[50, 60],
            power_watts=32000, needs_three_phase=True,
            temp_min_c=10.0, temp_max_c=35.0, humidity_max_pct=75.0,
            needs_oxygen=False, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["detector calibration kit"],
            manual_languages=["English", "German"], operating_complexity="high",
        ),
    ),
    Offer(
        donor_name="Vanderbilt Radiology surplus",
        donor_email="radiology@example.org",
        device_type="mobile x-ray unit",
        origin=GeoPoint(country="United States", city="Nashville", latitude=36.1627, longitude=-86.7816),
        quantity_offered=2,
        device=_device(
            manufacturer="GE Healthcare", model="AMX 240", year_of_manufacture=2020,
            voltage_min_v=100, voltage_max_v=240, frequencies_hz=[50, 60],
            power_watts=2000, needs_three_phase=False,
            temp_min_c=10.0, temp_max_c=40.0, humidity_max_pct=80.0,
            needs_oxygen=False, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["detector plates"],
            manual_languages=["English", "French"], operating_complexity="high",
        ),
    ),
    Offer(
        donor_name="Memphis Imaging Partners",
        donor_email="imaging@example.org",
        device_type="ultrasound scanner",
        origin=GeoPoint(country="United States", city="Memphis", latitude=35.1495, longitude=-90.0490),
        quantity_offered=1,
        device=_device(
            manufacturer="Philips", model="Affiniti 50", year_of_manufacture=2019,
            voltage_min_v=100, voltage_max_v=240, frequencies_hz=[50, 60],
            power_watts=500, needs_three_phase=False,
            temp_min_c=10.0, temp_max_c=35.0, humidity_max_pct=80.0,
            needs_oxygen=False, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["ultrasound gel", "probe covers"],
            manual_languages=["English"], operating_complexity="high",
        ),
    ),
    Offer(
        donor_name="Nairobi Equipment Trust",
        donor_email="trust@example.org",
        device_type="portable ultrasound",
        origin=GeoPoint(country="Kenya", city="Nairobi", latitude=-1.2921, longitude=36.8219),
        quantity_offered=3,
        device=_device(
            manufacturer="Butterfly Network", model="iQ+", year_of_manufacture=2022,
            voltage_min_v=100, voltage_max_v=240, frequencies_hz=[50, 60],
            power_watts=15, needs_three_phase=False,
            temp_min_c=0.0, temp_max_c=40.0, humidity_max_pct=90.0,
            needs_oxygen=False, needs_compressed_air=False, needs_distilled_water=False,
            consumables=["ultrasound gel"],
            manual_languages=["English", "French", "Swahili"], operating_complexity="low",
        ),
    ),
)

# Support equipment. Without these in the inventory a requirement stays advice.
SUPPORT_OFFERS = (
    Offer(
        donor_name="Kampala Power Trust",
        donor_email="trust@example.org",
        device_type="voltage stabiliser",
        support_kind="voltage stabiliser",
        origin=GeoPoint(country="Uganda", city="Kampala", latitude=0.3476, longitude=32.5825),
        quantity_offered=4,
        device=_device(manufacturer="Sollatek", model="AVS30", year_of_manufacture=2022),
    ),
    Offer(
        donor_name="Kampala Power Trust",
        donor_email="trust@example.org",
        device_type="UPS 3kVA",
        support_kind="backup power",
        origin=GeoPoint(country="Uganda", city="Kampala", latitude=0.3476, longitude=32.5825),
        quantity_offered=2,
        device=_device(manufacturer="APC", model="Smart-UPS 3000", year_of_manufacture=2021),
    ),
    Offer(
        donor_name="Mombasa Medical Gases",
        donor_email="gases@example.org",
        device_type="medical air compressor",
        support_kind="medical air compressor",
        origin=GeoPoint(country="Kenya", city="Mombasa", latitude=-4.0435, longitude=39.6682),
        quantity_offered=1,
        device=_device(manufacturer="Bambi", model="VT200", year_of_manufacture=2020),
    ),
)


def main() -> None:
    repository.setup()
    for profile in FACILITIES:
        repo.save_facility(profile)
    print(f"facilities: {len(FACILITIES)}")

    expires = date.today() + timedelta(days=OFFER_WINDOW_DAYS)
    for offer in DEMO_OFFERS + IMAGING_OFFERS + SUPPORT_OFFERS:
        offer_id = repo.save_offer(
            Offer(**{**offer.__dict__, "available_until": expires})
            if offer.available_until is None else offer
        )
        print(f"  offer {offer_id}: {offer.device.label} x{offer.quantity_offered} "
              f"from {offer.origin.city}")


if __name__ == "__main__":
    main()
