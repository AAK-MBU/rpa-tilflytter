"""Pre-queue checks run over every in-progress tilflytter run.

For each running process run this flags two situations to their own follow-up queues:
- the citizen passed the 3-month form deadline without submitting the form, and
- the citizen reached 21 years 9 months while the process was under way.

The 3-month deadline is measured from when the welcome letter was actually sent
(welcome_document_sent_timestamp in the run meta), not from when the run started, so a
citizen whose letter has not gone out yet (e.g. an under-18 awaiting approval) is never
flagged as late.
"""

import os

import logging

from datetime import datetime

from dateutil.relativedelta import relativedelta
from mbu_process_dashboard_shared_components.process_dashboard_client import (
    ProcessDashboardClient,
)
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

    client = ProcessDashboardClient(api_admin_token=API_ADMIN_TOKEN)

    process_id, process_steps = process.find_process_id_and_steps(
        client=client, process_name=process_name
    )

    # Resolve the "form submitted in time" step so we can check actual submission, rather
    # than assuming a still-running run means the citizen never submitted.
    formular_step_name = "Formular indsendt inden for tidsfristen"
    formular_step_id = next(
        (
            step.get("id")
            for step in process_steps
            if step.get("name") == formular_step_name
        ),
        None,
    )

    if formular_step_id is None:
        logging.warning(
            "Could not resolve step id for '%s' - formular deadline check may over-flag",
            formular_step_name,
        )

    all_process_runs = process_run.get_all_process_runs(
        client=client, process_id=process_id, run_status="running"
    )

    # today = datetime.now().date()
    today = datetime(2026, 6, 20).date()

    for run in all_process_runs:
        cpr = run["meta"]["cpr"]

        item_data = {"cpr": cpr}

        # The 3-month deadline runs from when the welcome letter was actually sent
        # (recorded in the run meta), not from when the process run started.
        sent_ts = run["meta"].get("welcome_document_sent_timestamp")

        if sent_ts:
            deadline = (
                datetime.fromisoformat(sent_ts) + relativedelta(months=3)
            ).date()

            print(f"welcome document sent: {sent_ts}, deadline: {deadline}")

            # A still-running run does NOT mean the citizen failed to submit - only flag
            # them if the submission step has not been marked success.
            formular_submitted = any(
                step.get("step_id") == formular_step_id
                and step.get("status") == "success"
                for step in run.get("steps", [])
            )

            if today >= deadline and not formular_submitted:
                # 3 months since the letter was sent and no submission in time
                formular_not_sent.append(item_data)
            else:
                age_category, _ = helper_functions.get_age_category(cpr=cpr)

                if age_category == "21y9m_and_older":
                    tilflytter_above_age_limit.append(item_data)

        else:
            # Welcome letter not sent yet (e.g. under-18 awaiting approval) - no clock started
            age_category, _ = helper_functions.get_age_category(cpr=cpr)

            if age_category == "21y9m_and_older":
                tilflytter_above_age_limit.append(item_data)

    if formular_not_sent:
        workqueue_name = "tan.tilflytter.formular_ikke_indsendt_inden_for_tidsfristen"

        workqueue = ats_functions.fetch_workqueue(workqueue_name=workqueue_name)

        ats_functions.enqueue_items(workqueue=workqueue, items=formular_not_sent)

    if tilflytter_above_age_limit:
        workqueue_name = "tan.tilflytter.tilflytter_overskredet_aldersgraense"

        workqueue = ats_functions.fetch_workqueue(workqueue_name=workqueue_name)

        ats_functions.enqueue_items(
            workqueue=workqueue, items=tilflytter_above_age_limit
        )
