"""Module to handle item processing"""
# from mbu_rpa_core.exceptions import ProcessError, BusinessError

import sys

import os
import logging

from mbu_rpa_core.exceptions import BusinessError

from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions, solteq_document_handler, solteq_event_handler

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

    try:
        solteq_tand_db_object = SolteqTandDatabase(conn_str=db_conn_string)

        solteq_app = get_app()

        solteq_app.open_patient(ssn=citizen_cpr)

        if "--formular_ikke_indsendt_inden_for_tidsfristen" in sys.argv:
            event_text = "Formular indsendt inden for tidsfristen"

            solteq_event_handler.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_text=event_text, cpr=citizen_cpr)

            helper_functions.handle_process_dashboard(status="cancelled", item_reference=item_reference, process_step_name="Formular indsendt inden for tidsfristen")

        elif "--tilflytter_overskredet_aldersgraense" in sys.argv:
            event_text = "Tilflytter 21 år og 9 måneder - Formular ikke udfyldt"

            solteq_event_handler.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_text=event_text, cpr=citizen_cpr)

            helper_functions.handle_process_dashboard(status="cancelled", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder")

        else:
            # STEP 1 - fang borgere med tilflytter hændelse i solteq tand
            helper_functions.handle_dashboard_run_creation(process_name=process_name, meta=meta)

            helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Tilflytter registreret", failure=None, process_name=process_name)

            # STEP 2 - udregn borgerens alder og send digital post
            age_category = helper_functions.get_age_category(cpr=citizen_cpr)

            logger.info("Handling the creation of the welcome document")
            document_file_name = solteq_document_handler.check_and_create_welcome_document(solteq_app=solteq_app, item_data=item_data, solteq_tand_db_object=solteq_tand_db_object, age_category=age_category)

            logger.info("Handling the sending of the welcome document")
            solteq_document_handler.check_and_send_welcome_document(solteq_app=solteq_app, item_data=item_data, solteq_tand_db_object=solteq_tand_db_object, welcome_document_filename=document_file_name)

            helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Digital post udsendt", failure=None, process_name=process_name)

            # STEP 3 - afvikl hændelse i Solteq Tand
            solteq_event_handler.handle_tilflytter_event(solteq_app=solteq_app, cpr=citizen_cpr, solteq_tand_db_object=solteq_tand_db_object)

            # STEP 4 - hvis borger er 21 år og 9 måneder eller ældre --> annullér deres process run
            if age_category == "is_21y9m_or_older":
                event_text = "Tilflytter 21 år og 9 måneder ved tilflytning"

                solteq_event_handler.check_and_create_new_event(solteq_app=solteq_app, solteq_tand_db_object=solteq_tand_db_object, event_text=event_text, cpr=citizen_cpr)

                helper_functions.handle_process_dashboard(status="cancelled", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder", failure=None, process_name=process_name)

            else:
                helper_functions.handle_process_dashboard(status="success", item_reference=item_reference, process_step_name="Tilflytter under 21 år og 9 måneder", failure=None, process_name=process_name)

        solteq_app.close_patient_window()

    except BusinessError as be:
        logger.info(f"BusinessError: {be}")

        raise

    except Exception as e:
        print(f"error! {e}")

        raise e
