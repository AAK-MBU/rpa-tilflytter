"""Helper functions"""

import sys

import os
import logging

from dotenv import load_dotenv

from datetime import date

from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient
from mbu_process_dashboard_shared_components import (
    process_run,
    process_step_run
)

from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

load_dotenv()

logger = logging.getLogger(__name__)

API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN")
CLIENT = ProcessDashboardClient(api_admin_token=API_ADMIN_TOKEN, base_url="https://dev-mbu-dashboard-api.adm.aarhuskommune.dk/api/v1")


def get_age_category(cpr: str, on_date: date | None = None) -> str:
    """
    Helper to find a citizen's age category from their CPR
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

    age_years = today.year - birthdate.year

    if (today.month, today.day) < (birthdate.month, birthdate.day):
        age_years -= 1

    if age_years < 18:
        return "under_18"

    # compute 21y9m cutoff
    cutoff_year = today.year - 21
    cutoff_month = today.month - 9

    if cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1

    cutoff_date = date(cutoff_year, cutoff_month, today.day)

    if birthdate <= cutoff_date:
        return "is_21y9m_or_older"

    return "is_under_21y9m"


# pylint: disable=protected-access
def find_events(db_handler: SolteqTandDatabase, filters=None):
    """
    Helper to find events for a specific event_name
    """

    base_query = """
        SELECT
            e.[eventId],
            e.[type],
            e.[currentStateText],
            e.[currentStateDate],
            e.[timestamp],
            e.[clinicId],
            c.name,
            e.[entityId],
            e.[eventTriggerDate],
            p.cpr,
            CONCAT(p.firstName, ' ', p.lastName) as fullName,
            e.archived
        FROM
            [tmtdata_prod].[dbo].[EVENT] e
        JOIN
            [tmtdata_prod].[dbo].[PATIENT] p ON p.patientId = e.entityId
        JOIN
            [tmtdata_prod].[dbo].[CLINIC] c ON c.clinicId = e.clinicId
    """

    final_query, params = db_handler._construct_sql_statement(
        base_query,
        filters=filters,
        order_by="e.currentStateDate",
        order_direction="DESC"
    )

    logger.info(f"\n\nprinting sql:\n\n{final_query}\n\n")

    return db_handler._execute_query(final_query, tuple(params))


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
