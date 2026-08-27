"""Process a single tilflytter work item.

A work item represents one citizen flagged as a new mover ("tilflytter"). The queue
that invoked the run (via a sys.argv flag) selects the behaviour: record a
missed-deadline or age-limit outcome, or run the main flow - determine the age
category, create the booking reminder and events, and send (or arrange manual
sending of) the welcome letter.

The flow is idempotent: every run re-checks Solteq's state before acting, so an item
can be paused (pending user action) and later resumed without repeating work or
sending the welcome letter twice.
"""

import logging
import os
import sys

from mbu_rpa_core.exceptions import BusinessError
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions, solteq_helper
from processes.application_handler import get_app

logger = logging.getLogger(__name__)


def process_item(item_data: dict, item_reference: str, item_id: int):
    """Function to handle item processing"""

    assert item_data, "Item data is required"
    assert item_reference, "Item reference is required"
    assert item_id, "Item id is required"

    db_conn_string = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")

    citizen_cpr = item_data.get("cpr")
    citizen_name = item_data.get("name")

    process_name = "Tilflytter til Aarhus Kommune"

    tilflytter_event_name = item_data.get("event_name")

    # Which dashboard step a BusinessError raised below belongs to, and the status to
    # report it with. Updated as the flow moves from step to step; the BusinessError
    # handler reads them so every raise site - including the ones inside solteq_helper -
    # lands on the right step. "pending" marks the deliberate wait-for-human pauses,
    # "failed" everything else.
    current_step_name = "Tilflytter registreret"
    current_step_status = "failed"

    try:
        solteq_tand_db_object = SolteqTandDatabase(conn_str=db_conn_string)

        solteq_app = get_app()

        logger.info("Opening patient")
        solteq_app.open_patient(ssn=citizen_cpr)

        # The invoking queue (a sys.argv flag) selects the branch. The two outcome queues below
        # are populated by pre_process_checks; --tilflytter_registreret is the main flow.
        if "--formular_ikke_indsendt_inden_for_tidsfristen" in sys.argv:
            current_step_name = "Formular indsendt"

            solteq_helper.check_and_create_new_event(
                solteq_app=solteq_app,
                solteq_tand_db_object=solteq_tand_db_object,
                event_name="Tilflytter - Formular ikke indsendt inden for tidsfristen",
                cpr=citizen_cpr,
            )

            helper_functions.handle_process_dashboard(
                status="optional",
                item_reference=item_reference,
                process_step_name="Formular indsendt",
            )

        elif "--tilflytter_overskredet_aldersgraense" in sys.argv:
            current_step_name = "Tilflytter under 21 år og 9 måneder"

            solteq_helper.check_and_create_new_event(
                solteq_app=solteq_app,
                solteq_tand_db_object=solteq_tand_db_object,
                event_name="Tilflytter - Tilflytter 21 år og 9 måneder - Formular ikke udfyldt",
                cpr=citizen_cpr,
            )

            helper_functions.handle_process_dashboard(
                status="optional",
                item_reference=item_reference,
                process_step_name="Tilflytter under 21 år og 9 måneder",
            )

        elif "--tilflytter_registreret" in sys.argv:
            # Determine the age category and booking status. These decide which welcome
            # letter template applies and whether the letter needs approval before sending.
            # The category is fixed on the first run and read back from the run meta on every
            # resume, so a citizen keeps the same category even if they cross an age boundary
            # while the item is paused. Reaching 21 years 9 months is the one exception - that
            # transition is caught by pre_process_checks, not here.
            logger.info("Step - Checking citizen age category")
            existing_meta = helper_functions.get_process_run_meta(
                process_name=process_name, cpr=citizen_cpr
            )

            if existing_meta and existing_meta.get("age_category"):
                age_category = existing_meta["age_category"]
                booking_status = existing_meta["booking_status"]
                logger.info("Reusing age category from existing process run")
            else:
                age_category, booking_status = helper_functions.get_age_category(
                    cpr=citizen_cpr
                )

            # Citizens under 18 need Tandplejen's approval before the letter can be sent.
            awaiting_approval = booking_status == "Tilflytter - Afventer godkendelse"

            # Register a process-dashboard run for the citizen so the flow can be tracked
            # and resumed. welcome_document_sent_timestamp stays empty until the letter is
            # actually sent - the 3-month form deadline is measured from that timestamp.
            logger.info("Step 1 - Creating process run")
            meta = {
                "cpr": citizen_cpr,
                "name": citizen_name,
                "age_category": age_category,
                "booking_status": booking_status,
                "welcome_document_sent_timestamp": None,
            }
            helper_functions.handle_dashboard_run_creation(
                process_name=process_name, meta=meta
            )

            # helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Tilflytter registreret", failure=None, process_name=process_name)
            helper_functions.handle_process_dashboard(
                status="failed",
                item_reference=item_reference,
                process_step_name="Tilflytter registreret",
                failure=None,
                process_name=process_name,
            )

            booking_text = "Velkomstbrev"

            # Create the booking reminder 3 months out. Its status carries the age-based
            # approval state, so under-18 reminders are created as "awaiting approval".
            logger.info("Step 1b - Handling booking reminder 3 months ahead")
            solteq_helper.check_and_create_booking_reminder(
                solteq_app=solteq_app,
                solteq_tand_db_object=solteq_tand_db_object,
                cpr=citizen_cpr,
                booking_status=booking_status,
                booking_text=booking_text,
            )

            # STEP 2 - process the tilflytter event in Solteq Tand
            logger.info("Step 2 - Handling tilflytter event in Solteq")
            solteq_helper.check_and_handle_event(
                solteq_app=solteq_app,
                cpr=citizen_cpr,
                solteq_tand_db_object=solteq_tand_db_object,
                event_name=tilflytter_event_name,
            )

            # Under 18: create the approval event and pause here until Tandplejen approves it.
            # Approving it in Solteq archives the event; a separate service then re-queues the
            # item, and on the next run this check passes so the flow continues to send.
            if awaiting_approval:
                logger.info(
                    "Citizen is under 18 - handling 'Godkend afsendelse af velkomstbrev' event"
                )
                approve_document_event = "Tilflytter - Godkend afsendelse af velkomstbrev"
                current_step_name = "Velkomstbrev godkendt"

                solteq_helper.check_and_create_new_event(
                    solteq_app=solteq_app,
                    solteq_tand_db_object=solteq_tand_db_object,
                    event_name=approve_document_event,
                    cpr=citizen_cpr,
                )

                if not solteq_helper.is_event_processed(
                    solteq_tand_db_object=solteq_tand_db_object,
                    cpr=citizen_cpr,
                    event_name=approve_document_event,
                ):
                    logger.info(
                        "Welcome document sending not yet approved - pausing item and awaiting confirmation"
                    )

                    # The BusinessError handler reports the step (as pending, with the
                    # work item id attached so the dashboard can rerun it).
                    current_step_status = "pending"

                    raise BusinessError(
                        "Afventer godkendelse af afsendelse af velkomstbrev."
                    )

                logger.info(
                    "Welcome document sending has been approved - continuing to send"
                )

            helper_functions.handle_process_dashboard(
                status="success",
                item_reference=item_reference,
                process_step_name="Velkomstbrev godkendt",
                failure=None,
                process_name=process_name,
            )

            # STEP 3 - is the citizen (or a parent) registered for Digital Post? This decides
            # whether the welcome letter is sent digitally or handed to Tandplejen to send by hand.
            logger.info("Step 3 - Checking citizen digital post status")
            current_step_name = "Velkomstbrev sendt"
            current_step_status = "failed"
            citizen_tilmeldt_digital_post = solteq_helper.check_digital_post_status(
                cpr=citizen_cpr, solteq_tand_db_object=solteq_tand_db_object
            )

            if citizen_tilmeldt_digital_post:
                logger.info("Citizen and/or parents are registered for Digital Post")

                logger.info("Handling the creation of the welcome document")
                document_file_name = solteq_helper.check_and_create_welcome_document(
                    item_data=item_data,
                    solteq_app=solteq_app,
                    solteq_tand_db_object=solteq_tand_db_object,
                    age_category=age_category,
                )

                logger.info("Handling the sending of the welcome document")
                solteq_helper.check_and_send_welcome_document(
                    item_data=item_data,
                    solteq_app=solteq_app,
                    solteq_tand_db_object=solteq_tand_db_object,
                    welcome_document_filename=document_file_name,
                )

                helper_functions.handle_process_dashboard(
                    status="success",
                    item_reference=item_reference,
                    process_step_name="Velkomstbrev sendt",
                    failure=None,
                    process_name=process_name,
                )

                logger.info(
                    "Setting tilflytter booking status to 'Tilflytter - Velkomstbrev udsendt'"
                )
                solteq_helper.check_and_set_booking_status(
                    solteq_app=solteq_app,
                    solteq_tand_db_object=solteq_tand_db_object,
                    cpr=citizen_cpr,
                    status_id=640,
                    status_text="Tilflytter - Velkomstbrev udsendt",
                    booking_text=booking_text,
                )

                logger.info(
                    "Updating process run metadata with actual welcome document sent timestamp"
                )
                helper_functions.update_process_run_metadata(
                    cpr=citizen_cpr,
                    meta_update={
                        "booking_status": "Tilflytter - Velkomstbrev udsendt",
                        "welcome_document_sent_timestamp": helper_functions.current_timestamp(),
                    },
                    process_name=process_name,
                )

                logger.info("Creating administrative note for welcome letter")
                solteq_helper.check_and_create_journal_note(
                    solteq_app=solteq_app,
                    solteq_tand_db_object=solteq_tand_db_object,
                    cpr=citizen_cpr,
                    note_message="Velkomstbrev er sendt. Se Dokumenter",
                )

            else:
                logger.info(
                    "Citizen and parents are not registered for Digital Post - manual send flow"
                )

                # Tandplejen sends the welcome letter by hand and journalises it as
                # "Velkomstbrev". The robot records the task as an event and pauses until both
                # that event is handled and the document exists (a manual send is not registered
                # via Digital Post, so the journalised document is the only proof it went out),
                # then marks the booking sent.
                manual_send_event = (
                    "Tilflytter - Ikke tilmeldt digital post - udsend brev manuelt"
                )
                solteq_helper.check_and_create_new_event(
                    solteq_app=solteq_app,
                    solteq_tand_db_object=solteq_tand_db_object,
                    event_name=manual_send_event,
                    cpr=citizen_cpr,
                )

                event_handled = solteq_helper.is_event_processed(
                    solteq_tand_db_object=solteq_tand_db_object,
                    cpr=citizen_cpr,
                    event_name=manual_send_event,
                )
                document_exists = solteq_helper.welcome_document_exists(
                    solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr
                )

                if not (event_handled and document_exists):
                    logger.info(
                        "Manual welcome letter send not yet completed - pausing item and awaiting manual send"
                    )

                    # The BusinessError handler reports the step (as pending, with the
                    # work item id attached so the dashboard can rerun it).
                    current_step_status = "pending"

                    raise BusinessError("Afventer manuel udsendelse af velkomstbrev.")

                logger.info(
                    "Manual welcome letter send completed - marking booking as sent"
                )
                solteq_helper.check_and_set_booking_status(
                    solteq_app=solteq_app,
                    solteq_tand_db_object=solteq_tand_db_object,
                    cpr=citizen_cpr,
                    status_id=640,
                    status_text="Tilflytter - Velkomstbrev udsendt",
                    booking_text=booking_text,
                )

                helper_functions.update_process_run_metadata(
                    cpr=citizen_cpr,
                    meta_update={
                        "booking_status": "Tilflytter - Velkomstbrev udsendt",
                        "welcome_document_sent_timestamp": helper_functions.current_timestamp(),
                    },
                    process_name=process_name,
                )

                helper_functions.handle_process_dashboard(
                    status="success",
                    item_reference=item_reference,
                    process_step_name="Velkomstbrev sendt",
                    failure=None,
                    process_name=process_name,
                )

            # STEP 5 - flag citizens who have reached 21 years 9 months. This applies on both
            # the digital and manual paths, so the age is recorded however the letter was sent.
            logger.info("Step 5 - Handling tilflytter age step in process dashboard")
            current_step_name = "Tilflytter under 21 år og 9 måneder"

            if age_category == "21y9m_and_older":
                helper_functions.handle_process_dashboard(
                    status="optional",
                    item_reference=item_reference,
                    process_step_name="Tilflytter under 21 år og 9 måneder",
                    failure=None,
                    process_name=process_name,
                )

            else:
                helper_functions.handle_process_dashboard(
                    status="success",
                    item_reference=item_reference,
                    process_step_name="Tilflytter under 21 år og 9 måneder",
                    failure=None,
                    process_name=process_name,
                )

            logger.info("Closing patient window")
            solteq_app.close_patient_window()

    except BusinessError as be:
        logger.info(f"BusinessError: {be}")

        # Report the step the item stopped on and attach the ATS work item id as the
        # rerun target, so the item can be rerun from the process dashboard. Reporting
        # must never mask the original BusinessError - main.py needs it to put the work
        # item into "pending user action".
        try:
            helper_functions.handle_process_dashboard(
                status=current_step_status,
                item_reference=item_reference,
                process_step_name=current_step_name,
                failure=be,
                process_name=process_name,
                rerun_config={"workitem_id": item_id},
            )
        except Exception:
            logger.exception("Failed to report BusinessError to process dashboard")

        # Always leave the patient window closed when pausing/rejecting an item
        try:
            solteq_app.close_patient_window()
        except Exception:
            logger.exception("Failed to close patient window after BusinessError")

        raise

    except Exception as e:
        logger.info(f"error! {e}")

        # Always leave the patient window closed when pausing/rejecting an item
        try:
            solteq_app.close_patient_window()
        except Exception:
            logger.exception("Failed to close patient window after Exception")

        raise e
