"""Module to catch patient's that have exceeded age limit after beginning the tilflytter process"""

import os

from datetime import datetime
from dateutil.relativedelta import relativedelta

from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient
from mbu_process_dashboard_shared_components import (
    process,
    process_run,
)

from helpers import ats_functions, helper_functions


def main():
    """Main"""

    formular_not_sent = []
    tilflytter_above_age_limit = []

    process_name = "Tilflytter til Aarhus Kommune"

    API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN")

    client = ProcessDashboardClient(api_admin_token=API_ADMIN_TOKEN, base_url="https://dev-mbu-dashboard-api.adm.aarhuskommune.dk/api/v1")

    process_id = process.find_process_id_and_steps(client=client, process_name=process_name)

    all_process_runs = process_run.get_all_process_runs(client=client, process_id=process_id, run_status="running")

    date_3_months_ago = datetime.now() - relativedelta(months=3)

    for run in all_process_runs:
        workqueue_name = None

        cpr = run["meta"]["cpr"]

        item_data = {
            "cpr": cpr
        }

        started_at_str = run.get("started_at")

        if not started_at_str:
            continue

        started_at = datetime.fromisoformat(started_at_str)
        if started_at < date_3_months_ago:
            formular_not_sent.append(item_data)

        else:
            age_category = helper_functions.get_age_category(cpr=cpr)

            if age_category == "is_21y9m_or_older":
                tilflytter_above_age_limit.append(item_data)

    if formular_not_sent:
        workqueue_name = "tan.tilflytter.formular_ikke_indsendt_inden_for_tidsfristen"

        workqueue = ats_functions.fetch_workqueue(workqueue_name=workqueue_name)

        ats_functions.enqueue_items(workqueue=workqueue, items=formular_not_sent)

    if tilflytter_above_age_limit:
        workqueue_name = "tan.tilflytter.tilflytter_overskredet_aldersgraense"

        workqueue = ats_functions.fetch_workqueue(workqueue_name=workqueue_name)

        ats_functions.enqueue_items(workqueue=workqueue, items=tilflytter_above_age_limit)
