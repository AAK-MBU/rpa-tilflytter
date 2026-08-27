"""Pre-queue checks run over every in-progress tilflytter run.

For each running process run this flags two situations to their own follow-up queues:
- the citizen passed the 4-week form deadline without submitting the form, and
- the citizen reached 21 years 9 months while the process was under way.

"While the process was under way" is the whole point of the age check: a citizen who was
already past 21 years 9 months when their run was created is left alone, because they never
had an age limit to miss. Only someone who was below the limit at creation and has since
crossed it is queued - see has_newly_exceeded_age_limit.

The 4-week deadline is measured from when the welcome letter was actually sent
(welcome_document_sent_timestamp in the run meta), not from when the run started, so a
citizen whose letter has not gone out yet (e.g. an under-18 awaiting approval) is never
flagged as late.
"""

import os

import logging

from datetime import date, datetime

from dateutil.relativedelta import relativedelta
from mbu_process_dashboard_shared_components.process_dashboard_client import (
    ProcessDashboardClient,
)
from mbu_process_dashboard_shared_components import (
    process,
    process_run,
)

from helpers import ats_functions, helper_functions

logger = logging.getLogger(__name__)

AGE_LIMIT_CATEGORY = "21y9m_and_older"

# The citizen has 4 weeks from the day the welcome letter went out to submit the form.
FORM_DEADLINE_WEEKS = 4


def run_created_date(run: dict) -> date | None:
    """
    The date the process run was created, taken from its step runs.

    The dashboard's /runs/ payload (ProcessRunPublic) carries no created_at, and started_at
    is never set on creation - but each step run is created together with the run and does
    expose created_at, so the earliest step run dates the run.

    Returns None if the run has no step runs to date it by.
    """

    created_timestamps = [
        step["created_at"] for step in run.get("steps", []) if step.get("created_at")
    ]

    if not created_timestamps:
        return None

    return min(datetime.fromisoformat(ts) for ts in created_timestamps).date()


def has_newly_exceeded_age_limit(run: dict, cpr: str, on_date: date) -> bool:
    """
    Whether the citizen has crossed 21 years 9 months *during* their process run.

    A citizen who was already past the limit when the run was created is not flagged: they
    never had an age limit to miss, the main flow already marks their "Tilflytter under 21
    år og 9 måneder" step optional at registration, and queueing them here would create a
    "Formular ikke udfyldt" event they should never get.
    """

    age_category_now, _ = helper_functions.get_age_category(cpr=cpr, on_date=on_date)

    if age_category_now != AGE_LIMIT_CATEGORY:
        return False

    # The run meta holds the age category as of run creation: it is computed once when the
    # run is created and deliberately never recomputed (see process_item), which makes it
    # the authoritative "what were they when they started" value.
    age_category_at_creation = run.get("meta", {}).get("age_category")

    if not age_category_at_creation:
        # Runs created before age_category was stored in the meta - date the run by its step
        # runs and work out the category the same way process_item would have.
        run_created = run_created_date(run)

        if run_created is None:
            logger.warning(
                "Process run %s has neither age_category in its meta nor a step run to date "
                "it by - skipping the age limit check rather than risk flagging a citizen "
                "who was above the limit all along.",
                run.get("id"),
            )

            return False

        age_category_at_creation, _ = helper_functions.get_age_category(
            cpr=cpr, on_date=run_created
        )

    return age_category_at_creation != AGE_LIMIT_CATEGORY


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
    formular_step_name = "Formular indsendt"
    formular_step_id = next(
        (
            step.get("id")
            for step in process_steps
            if step.get("name") == formular_step_name
        ),
        None,
    )

    if formular_step_id is None:
        logger.warning(
            "Could not resolve step id for '%s' - formular deadline check may over-flag",
            formular_step_name,
        )

    all_process_runs = process_run.get_all_process_runs(
        client=client, process_id=process_id, run_status="running"
    )

    today = date.today()

    for run in all_process_runs:
        cpr = run["meta"]["cpr"]

        item_data = {"cpr": cpr}

        # The deadline runs from when the welcome letter was actually sent
        # (recorded in the run meta), not from when the process run started.
        sent_ts = run["meta"].get("welcome_document_sent_timestamp")

        if sent_ts:
            deadline = (
                datetime.fromisoformat(sent_ts)
                + relativedelta(weeks=FORM_DEADLINE_WEEKS)
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
                # Past the deadline since the letter was sent, and no submission in time
                formular_not_sent.append(item_data)

            elif has_newly_exceeded_age_limit(run=run, cpr=cpr, on_date=today):
                tilflytter_above_age_limit.append(item_data)

        else:
            # Welcome letter not sent yet (e.g. under-18 awaiting approval) - no clock started
            if has_newly_exceeded_age_limit(run=run, cpr=cpr, on_date=today):
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
