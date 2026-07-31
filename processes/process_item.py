"""Module to handle item processing"""
# from mbu_rpa_core.exceptions import ProcessError, BusinessError

import logging
import os
import sys

from mbu_rpa_core.exceptions import BusinessError
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions, solteq_helper
from processes.application_handler import get_app

logger = logging.getLogger(__name__)


def process_item(item_data: dict, item_reference: str):
    """Function to handle item processing"""

    assert item_data, "Item data is required"
    assert item_reference, "Item reference is required"

    db_conn_string = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")

    citizen_cpr = item_data.get("cpr")
    citizen_name = item_data.get("name")

    process_name = "Tilflytter til Aarhus Kommune"

    tilflytter_event_name = item_data.get("event_name")

    try:
        solteq_tand_db_object = SolteqTandDatabase(conn_str=db_conn_string)

        solteq_app = get_app()

        logger.info("Opening patient")
        solteq_app.open_patient(ssn=citizen_cpr)

        if "--formular_ikke_indsendt_inden_for_tidsfristen" in sys.argv:
            solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_name="Formular ikke indsendt inden for tidsfristen", cpr=citizen_cpr)

            helper_functions.handle_process_dashboard(status="optional", item_reference=item_reference, process_step_name="Formular indsendt inden for tidsfristen")

        elif "--tilflytter_overskredet_aldersgraense" in sys.argv:
            solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_name="Tilflytter 21 år og 9 måneder - Formular ikke udfyldt", cpr=citizen_cpr)

            helper_functions.handle_process_dashboard(status="optional", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder")

        elif "--tilflytter_registreret" in sys.argv:
            # STEP - determine age category / booking-status. Compute it once on the first
            # run and reuse it on resumes (from the existing run meta), so the citizen keeps
            # the same pattern across runs even if they cross an age boundary while paused.
            # A 21y9m crossing is handled separately by pre_process_checks.
            logger.info("Step - Checking citizen age category")
            existing_meta = helper_functions.get_process_run_meta(process_name=process_name, cpr=citizen_cpr)

            if existing_meta and existing_meta.get("age_category"):
                age_category = existing_meta["age_category"]
                booking_status = existing_meta["booking_status"]
                logger.info("Reusing age category from existing process run")
            else:
                age_category, booking_status = helper_functions.get_age_category(cpr=citizen_cpr)

            # Under 18 the welcome letter may not be sent until it has been approved
            awaiting_approval = booking_status == "Tilflytter - Afventer godkendelse"

            # STEP 1 - opret et process run for borgeren
            logger.info("Step 1 - Creating process run")
            meta = {
                "cpr": citizen_cpr,
                "name": citizen_name,
                "age_category": age_category,
                "booking_status": booking_status,
                "welcome_document_sent_timestamp": None,
            }
            helper_functions.handle_dashboard_run_creation(process_name=process_name, meta=meta)

            # TEST: reset every step of this run to "pending"
            helper_functions.set_all_steps_pending(process_name=process_name, cpr=citizen_cpr)

            # helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Tilflytter registreret", failure=None, process_name=process_name)
            helper_functions.handle_process_dashboard(status="failed", item_reference=item_reference, process_step_name="Tilflytter registreret", failure=None, process_name=process_name)

            booking_text = "Velkomstbrev"

            # STEP 1b - create booking reminder 3 months ahead
            logger.info("Step 1b - Handling booking reminder 3 months ahead")
            solteq_helper.check_and_create_booking_reminder(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr, booking_status=booking_status, booking_text=booking_text)

            # STEP 2 - afvikl tilflytter hændelse i Solteq Tand
            logger.info("Step 2 - Handling tilflytter event in Solteq")
            solteq_helper.check_and_handle_event(solteq_app=solteq_app, cpr=citizen_cpr, solteq_tand_db_object=solteq_tand_db_object, event_name=tilflytter_event_name)

            # Under 18: ensure the approval event exists, then pause until it has been approved.
            # A confirming service re-queues the item once approval is given; on that re-run the
            # approval event is processed (archived), so we fall through and send the document.
            if awaiting_approval:
                logger.info("Citizen is under 18 - handling 'Godkend afsendelse af velkomstbrev' event")
                approve_document_event = "Godkend afsendelse af velkomstbrev"
                solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_name=approve_document_event, cpr=citizen_cpr)

                if not solteq_helper.is_event_processed(solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr, event_name=approve_document_event):
                    logger.info("Welcome document sending not yet approved - pausing item and awaiting confirmation")

                    helper_functions.handle_process_dashboard(status="pending", item_reference=item_reference, process_step_name="Borger afventer godkendelse", failure=None, process_name=process_name)

                    raise BusinessError("Afventer godkendelse af afsendelse af velkomstbrev.")

                logger.info("Welcome document sending has been approved - continuing to send")

            helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Borger afventer godkendelse", failure=None, process_name=process_name)

            # STEP 3 - tjek om borger er tilmeldt digital post --> cancel process run hvis ikke
            logger.info("Step 3 - Checking citizen digital post status")
            citizen_tilmeldt_digital_post = solteq_helper.check_digital_post_status(cpr=citizen_cpr, solteq_tand_db_object=solteq_tand_db_object)

            if citizen_tilmeldt_digital_post:
                logger.info("Citizen and/or parents are registered for Digital Post")

                logger.info("Handling the creation of the welcome document")
                document_file_name = solteq_helper.check_and_create_welcome_document(item_data=item_data, solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, age_category=age_category)

                logger.info("Handling the sending of the welcome document")
                solteq_helper.check_and_send_welcome_document(item_data=item_data, solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, welcome_document_filename=document_file_name)

                helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Digital post udsendt", failure=None, process_name=process_name)

                logger.info("Setting tilflytter booking status to 'Tilflytter - Velkomstbrev udsendt'")
                solteq_helper.check_and_set_booking_status(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr, status_id=640, status_text="Tilflytter - Velkomstbrev udsendt", booking_text=booking_text)

                logger.info("Updating process run metadata with actual welcome document sent timestamp")
                helper_functions.update_process_run_metadata(cpr=citizen_cpr, meta_update={"booking_status": "Tilflytter - Velkomstbrev udsendt", "welcome_document_sent_timestamp": helper_functions.current_timestamp()}, process_name=process_name)

                logger.info("Creating administrative note for welcome letter")
                solteq_helper.check_and_create_journal_note(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr, note_message="Velkomstbrev er sendt. Se Dokumenter")

            else:
                logger.info("Citizen and parents are not registered for Digital Post - manual send flow")

                # Tandplejen sends the welcome letter manually (journalised as "Velkomstbrev")
                # and handles this event. The robot creates the event, then pauses until the
                # event is handled AND the document exists, at which point it marks the booking sent.
                manual_send_event = "Tilflytter - Ikke tilmeldt digital post - udsend brev manuelt"
                solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_name=manual_send_event, cpr=citizen_cpr)

                event_handled = solteq_helper.is_event_processed(solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr, event_name=manual_send_event)
                document_exists = solteq_helper.welcome_document_exists(solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr)

                if not (event_handled and document_exists):
                    logger.info("Manual welcome letter send not yet completed - pausing item and awaiting manual send")

                    raise BusinessError("Afventer manuel udsendelse af velkomstbrev.")

                logger.info("Manual welcome letter send completed - marking booking as sent")
                solteq_helper.check_and_set_booking_status(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, cpr=citizen_cpr, status_id=640, status_text="Tilflytter - Velkomstbrev udsendt", booking_text=booking_text)

                helper_functions.update_process_run_metadata(cpr=citizen_cpr, meta_update={"booking_status": "Tilflytter - Velkomstbrev udsendt", "welcome_document_sent_timestamp": helper_functions.current_timestamp()}, process_name=process_name)

                helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Digital post udsendt", failure=None, process_name=process_name)

            # STEP 5 - handle the 21 år og 9 måneder age step. Runs after both the
            # digital-post and manual-send paths, so it applies regardless of Digital Post status.
            logger.info("Step 5 - Handling tilflytter age step in process dashboard")
            if age_category == "21y9m_and_older":
                logger.info("Citizen in 21y9m_and_older age category --> creating event in Solteq")
                solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_name="Tilflytter 21 år og 9 måneder ved tilflytning", cpr=citizen_cpr)

                helper_functions.handle_process_dashboard(status="optional", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder", failure=None, process_name=process_name)

            else:
                helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder", failure=None, process_name=process_name)

            logger.info("Closing patient window")
            solteq_app.close_patient_window()

    except BusinessError as be:
        logger.info(f"BusinessError: {be}")

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
