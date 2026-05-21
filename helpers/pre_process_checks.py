"""Module to catch patient's that have exceeded age limit after beginning the tilflytter process"""

import os

from datetime import datetime

from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient
from mbu_process_dashboard_shared_components import (
    process,
    process_run,
)
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import ats_functions, helper_functions


def main():
    """Main"""

    formular_not_sent = []
    tilflytter_above_age_limit = []

    process_name = "Tilflytter til Aarhus Kommune"

    API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN")
    DASHBOARD_BASE_URL = os.getenv("DASHBOARD_BASE_URL", "https://dev-mbu-dashboard-api.adm.aarhuskommune.dk/api/v1")
    DB_CONN_STRING = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")

    client = ProcessDashboardClient(api_admin_token=API_ADMIN_TOKEN, base_url=DASHBOARD_BASE_URL)
    db_handler = SolteqTandDatabase(conn_str=DB_CONN_STRING)

    process_id, _ = process.find_process_id_and_steps(client=client, process_name=process_name)

    all_process_runs = process_run.get_all_process_runs(client=client, process_id=process_id, run_status="running")

    # today = datetime.now().date()
    today = datetime(2026, 6, 20).date()

    for run in all_process_runs:
        cpr = run["meta"]["cpr"]

        item_data = {
            "cpr": cpr
        }

        print("checking booking reminder date")
        # Check if booking reminder date has been reached
        booking_reminder_date = get_tilflytter_booking_reminder_date(cpr=cpr, db_handler=db_handler)

        if booking_reminder_date:
            # Convert to date object if it's a datetime
            if isinstance(booking_reminder_date, datetime):
                booking_reminder_date = booking_reminder_date.date()

                print(f"booking reminder date: {booking_reminder_date}")

            if today >= booking_reminder_date:
                formular_not_sent.append(item_data)
            else:
                age_category = helper_functions.get_age_category(cpr=cpr)

                if age_category == "21y9m_and_older":
                    tilflytter_above_age_limit.append(item_data)

        else:
            age_category = helper_functions.get_age_category(cpr=cpr)

            if age_category == "21y9m_and_older":
                tilflytter_above_age_limit.append(item_data)

    if formular_not_sent:
        workqueue_name = "tan.tilflytter.formular_ikke_indsendt_inden_for_tidsfristen"

        workqueue = ats_functions.fetch_workqueue(workqueue_name=workqueue_name)

        ats_functions.enqueue_items(workqueue=workqueue, items=formular_not_sent)

    if tilflytter_above_age_limit:
        workqueue_name = "tan.tilflytter.tilflytter_overskredet_aldersgraense"

        workqueue = ats_functions.fetch_workqueue(workqueue_name=workqueue_name)

        ats_functions.enqueue_items(workqueue=workqueue, items=tilflytter_above_age_limit)


def get_tilflytter_booking_reminder_date(cpr: str, db_handler: SolteqTandDatabase):
    """
    Query the database for the tilflytter booking reminder scheduled date.
    Returns the booking appointment date if found, None otherwise.
    """
    query = """
        SELECT TOP 1
            b.BookingID,
            b.CreatedDateTime,
            b.MeetingTime,
            b.Status
        FROM
            [tmtdata_prod].[dbo].[BOOKING] b
        JOIN
            [tmtdata_prod].[dbo].[PATIENT] p ON p.patientId = b.patientId
        WHERE
            p.cpr = ?
            AND b.BookingText LIKE '%Tilflytter - Er digital formular udfyldt?%'
        ORDER BY
            b.CreatedDateTime DESC
    """

    # pylint: disable=protected-access
    rows = db_handler._execute_query(query, params=(cpr,))

    if rows:
        # Use MeetingTime (scheduled appointment date) rather than CreatedDateTime
        booking_date = rows[0].get("MeetingTime")
        if booking_date:
            return booking_date

    return None