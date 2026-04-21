"""Module to handle item processing"""
# from mbu_rpa_core.exceptions import ProcessError, BusinessError

import sys

import os
import logging

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

    meta = {
        "cpr": citizen_cpr,
        "name": citizen_name,
    }

    process_name = "Tilflytter til Aarhus Kommune"

    event_name = item_data.get("event_name")

    try:
        solteq_tand_db_object = SolteqTandDatabase(conn_str=db_conn_string)

        solteq_app = get_app()

        logger.info("Opening patient")
        solteq_app.open_patient(ssn=citizen_cpr)

        if "--formular_ikke_indsendt_inden_for_tidsfristen" in sys.argv:
            solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_text="Formular ikke indsendt inden for tidsfristen", cpr=citizen_cpr)

            helper_functions.handle_process_dashboard(status="cancelled", item_reference=item_reference, process_step_name="Formular indsendt inden for tidsfristen")

        elif "--tilflytter_overskredet_aldersgraense" in sys.argv:
            solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_text="Tilflytter 21 år og 9 måneder - Formular ikke udfyldt", cpr=citizen_cpr)

            helper_functions.handle_process_dashboard(status="cancelled", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder")

        else:
            # STEP 1 - opret et process run for borgeren
            logger.info("Step 1 - Creating process run")
            helper_functions.handle_dashboard_run_creation(process_name=process_name, meta=meta)

            helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Tilflytter registreret", failure=None, process_name=process_name)

            # STEP 2 - afvikl tilflytter hændelse i Solteq Tand
            logger.info("Step 2 - Handling tilflytter event in Solteq")
            solteq_helper.check_and_handle_event(solteq_app=solteq_app, cpr=citizen_cpr, solteq_tand_db_object=solteq_tand_db_object, event_name=event_name)

            # STEP 3 - tjek om borger er tilmeldt digital post --> cancel process run hvis ikke
            logger.info("Step 3 - Checking citizen digital post status")
            citizen_tilmeldt_digital_post = solteq_helper.check_digital_post_status(cpr=citizen_cpr, solteq_tand_db_object=solteq_tand_db_object)

            if citizen_tilmeldt_digital_post:
                logger.info("Citizen and/or parents are registered for Digital Post")
                # STEP 4 - udregn borgerens alder og send digital post
                logger.info("Step 4 - Checking citizen age category")
                age_category = helper_functions.get_age_category(cpr=citizen_cpr)

                logger.info("Handling the creation of the welcome document")
                document_file_name = solteq_helper.check_and_create_welcome_document(item_data=item_data, solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, age_category=age_category)

                logger.info("Handling the sending of the welcome document")
                solteq_helper.check_and_send_welcome_document(item_data=item_data, solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, welcome_document_filename=document_file_name)

                helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Digital post udsendt", failure=None, process_name=process_name)

                # STEP 5 - hvis borger er 21 år og 9 måneder eller ældre --> annullér deres process run
                logger.info("Step 5 - Handling tilflytter age step in process dashboard")
                if age_category == "is_21y9m_or_older":
                    logger.info("Citizen in 21y9m_or_older age category --> creating event in Solteq")
                    solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_text="Tilflytter 21 år og 9 måneder ved tilflytning", cpr=citizen_cpr)

                    helper_functions.handle_process_dashboard(status="cancelled", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder", failure=None, process_name=process_name)

                else:
                    helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder", failure=None, process_name=process_name)

            else:
                logger.info("Citizen and parents are not registered for Digital Post")
                helper_functions.handle_process_dashboard(status="cancelled", item_reference=item_reference, process_step_name="Digital post udsendt", failure="Tilflytter - Ikke tilmeldt Digital Post", process_name=process_name)

                solteq_helper.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_text="Tilflytter - Ikke tilmeldt Digital Post", cpr=citizen_cpr)

        logger.info("Closing patient window")
        solteq_app.close_patient_window()

    except BusinessError as be:
        logger.info(f"BusinessError: {be}")

        raise

    except Exception as e:
        logger.info(f"error! {e}")

        raise e
