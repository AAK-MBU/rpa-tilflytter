"""Helper functions"""

import os
import logging

from datetime import date, datetime

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient
from mbu_process_dashboard_shared_components import (
    process,
    process_run,
    process_step_run,
)

load_dotenv()

logger = logging.getLogger(__name__)

API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN")

DASHBOARD_BASE_URL = os.getenv("DASHBOARD_BASE_URL", "https://dev-mbu-dashboard-api.adm.aarhuskommune.dk/api/v1")
CLIENT = ProcessDashboardClient(api_admin_token=API_ADMIN_TOKEN, base_url=DASHBOARD_BASE_URL)

os.environ["ATS_TOKEN"] = os.getenv("ATS_TOKEN_DEV")
os.environ["ATS_URL"] = os.getenv("ATS_URL_DEV")


def get_age_category(cpr: str, on_date: date | None = None) -> tuple[str, str]:
    """
    Helper to find a citizen's age category and booking status from their CPR.

    Categories:
    - "0_to_5": 0-5 years (to parents only)
    - "6_to_14": 6-14 years (to parents only)
    - "15_to_17": 15-17 years (to parents and patient)
    - "18_to_21y8m": 18 to before 21 years 9 months (to young adult)
    - "21y9m_and_older": 21 years 9 months and older

    Returns:
        tuple: (age_category, booking_status) where booking_status is the
        booking aftalestatus: "Tilflytter - Afventer godkendelse" for citizens
        under 18 (welcome letter must be approved before sending) and
        "Tilflytter - Afsendelse godkendt" for citizens 18 and older.
    """

    s = cpr.replace("-", "").strip()

    if len(s) != 10 or not s.isdigit():
        raise ValueError("Invalid CPR format")

    dd = int(s[0:2])
    mm = int(s[2:4])
    yy = int(s[4:6])
    serial = int(s[6:10])

    # Century rules
    if 0 <= serial <= 3999:
        year = 1900 + yy

    elif 4000 <= serial <= 4999:
        year = 2000 + yy if yy <= 36 else 1900 + yy

    elif 5000 <= serial <= 8999:
        year = 2000 + yy if yy <= 57 else 1800 + yy

    else:  # 9000–9999
        year = 2000 + yy if yy <= 36 else 1900 + yy

    birthdate = date(year, mm, dd)

    today = on_date or date.today()

    # Calculate age (years only)
    age_years = today.year - birthdate.year

    if (today.month, today.day) < (birthdate.month, birthdate.day):
        age_years -= 1

    # Category 1a: 0-5 years
    if age_years < 6:
        age_category = "0_to_5"

    # Category 1b: 6-14 years
    elif age_years < 15:
        age_category = "6_to_14"

    # Category 2: 15-17 years
    elif age_years < 18:
        age_category = "15_to_17"

    else:
        # Category 3 & 4: Calculate 21 years 9 months cutoff
        cutoff_date = today - relativedelta(years=21, months=9)

        if birthdate <= cutoff_date:
            age_category = "21y9m_and_older"

        else:
            age_category = "18_to_21y8m"

    # Under 18 the welcome letter must be approved before it is sent
    if age_years < 18:
        booking_status = "Tilflytter - Afventer godkendelse"

    else:
        booking_status = "Tilflytter - Afsendelse godkendt"

    return age_category, booking_status


def current_timestamp() -> str:
    """Return the current timestamp as an ISO 8601 string."""
    return datetime.now().isoformat()


def update_process_run_metadata(cpr: str, meta_update: dict, process_name: str = "Tilflytter til Aarhus Kommune") -> dict:
    """
    Merge fields into an existing process run's metadata.

    The metadata endpoint merges the supplied fields into the run's existing
    meta, so only the keys to change need to be passed. Used to record the
    actual welcome-document send time, which for under-18 citizens happens on a
    later run than when the run (and its initial meta) was created.
    """

    process_id = process.get_dashboard_process_id(client=CLIENT, process_name=process_name)

    if not process_id:
        raise RuntimeError("Process ID not found for process name.")

    process_run_id = process_run.get_dashboard_run_id(client=CLIENT, process_id=int(process_id), cpr=cpr)

    if not process_run_id:
        raise RuntimeError("Process run ID not found.")

    response = CLIENT.patch(endpoint=f"/runs/{process_run_id}/metadata", json={"meta": meta_update}, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(f"Failed to update process run metadata. Status code: {response.status_code}, Response: {response.text}")

    logger.info("Process run metadata updated: %s", meta_update)

    return response.json()


def handle_dashboard_run_creation(process_name: str, meta: dict):
    """
    Method for handling the creation of new process dashboard runs - if run already exists for the citizen, no new process run is created
    """

    print(f"meta: {meta}")

    citizen_cpr = meta.get("cpr")

    existing_run_id = process_run.get_process_run_by_cpr(client=CLIENT, process_name=process_name, cpr=citizen_cpr)

    if existing_run_id:
        logger.info("Process run already exists for citizen")

    else:
        process_run.create_dashboard_run(client=CLIENT, process_name=process_name, meta=meta)


def get_process_run_meta(process_name: str, cpr: str):
    """
    Return the meta dict of the citizen's most recent process run for the process,
    or None if no run exists. Used to reuse first-run values (e.g. age_category)
    across resumes instead of recomputing them each run.
    """

    process_id, _ = process.find_process_id_and_steps(client=CLIENT, process_name=process_name)

    response = CLIENT.get(
        f"runs/?process_id={process_id}&meta_filter=cpr%3A{cpr}&order_by=created_at&sort_direction=desc&page=1&size=1",
        timeout=10,
    )

    items = response.json().get("items", [])

    return items[0].get("meta") if items else None


def handle_process_dashboard(status: str, item_reference: str, process_step_name: str, failure: Exception | None = None, process_name: str = "Tilflytter til Aarhus Kommune"):
    """
    Method for handling updating the process dashboard
    """

    status_update_data = {
        "status": status
    }

    citizen_cpr = item_reference

    logger.info("before get_step_run_id_for_process_step_cpr() ...")

    step_run_id = process_step_run.get_step_run_id_for_process_step_cpr(client=CLIENT, process_name=process_name, step_name=process_step_name, cpr=citizen_cpr)

    if failure:
        step_run_update_data = process_step_run.build_step_run_update(status=status, failure=failure)

        status_update_data["failure"] = failure

    else:
        step_run_update_data = process_step_run.build_step_run_update(status=status)

    logger.info("before update_dashboard_step_run_by_id() ...")

    updated_step_run_data, status_code = process_step_run.update_dashboard_step_run_by_id(client=CLIENT, step_run_id=step_run_id, update_data=step_run_update_data)

    return updated_step_run_data, status_code


def set_all_steps_pending(process_name: str, cpr: str):
    """
    TEST HELPER: set every step of the citizen's process run to "pending".
    Useful for re-running the flow against the same citizen without stale step states.
    """

    _, steps = process.find_process_id_and_steps(client=CLIENT, process_name=process_name)

    for step in steps:
        step_name = step.get("name")

        if not step_name:
            continue

        handle_process_dashboard(status="pending", item_reference=cpr, process_step_name=step_name, process_name=process_name)
