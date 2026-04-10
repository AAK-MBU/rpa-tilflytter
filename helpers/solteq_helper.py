"""Handle the creation of discharge documents based on patient age."""

import logging
import os

import datetime

from dateutil.relativedelta import relativedelta
from mbu_solteqtand_shared_components.application import SolteqTandApp
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

logger = logging.getLogger(__name__)


# pylint: disable=protected-access
def check_digital_post_status(cpr: str, solteq_tand_db_object: SolteqTandDatabase):
    """
    Check if the patient is registered for digital post
    """

    filters = {
        "cpr": cpr,
        "isDKALMailSubscriber": 0,
    }

    base_query = """
        SELECT
            *
        FROM
            [tmtdata_prod].[dbo].[ACTIVE_PATIENTS]
        WHERE
            1=1
            AND primaryDKALRecipient IS NULL
            AND secondaryDKALRecipient IS NULL
    """

    final_query, params = solteq_tand_db_object._construct_sql_statement(
        base_query,
        filters=filters,
    )

    logger.info(f"\n\nprinting sql:\n\n{final_query}\n\n")

    rows = solteq_tand_db_object._execute_query(final_query, tuple(params))

    if rows:
        return False

    return True


def check_and_create_welcome_document(solteq_app: SolteqTandApp, item_data: dict, solteq_tand_db_object: SolteqTandDatabase, age_category):
    """
    Create a welcome document based on the patient's age.
    If the document already exists, it will not be created again.
    """

    if age_category == "under_18":
        template_name = "Tilflytter 21 år 9 mdr - Velkommen"
        # template_name = "Velkomstbrev til forældre og patient under 18"

        welcome_document_filename = "Velkomstbrev"

    elif age_category == "is_21y9m_or_older":
        template_name = "Tilflytter 21 år 9 mdr - Velkommen"

        welcome_document_filename = "Velkomstbrev"

    else:
        template_name = "Tilflytter 21 år 9 mdr - Velkommen"
        # template_name = "Velkomstbrev til ung fra 18-21 år og 8 måneder"

        welcome_document_filename = "Velkomstbrev"

    one_month_ago = datetime.datetime.now() - relativedelta(months=1)

    logger.info("Checking for existing welcome documents.")

    list_of_documents = solteq_tand_db_object.get_list_of_documents(
        filters={
            "p.cpr": item_data["cpr"],
            "ds.OriginalFilename": f"%{welcome_document_filename}%",
            "ds.rn": "1",
            "ds.DocumentStoreStatusId": "1",
            "ds.DocumentCreatedDate": (">=", one_month_ago),
        }
    )

    logger.info(f"Found {len(list_of_documents)} existing welcome documents.")

    if not list_of_documents:
        folder_path = f"C:\\tmp\\tmt\\{item_data['cpr']}"
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        print(f"\nprinting the folder_path: {folder_path}\n")

        logger.info("No existing welcome documents found, creating a new one.")
        document_template_metadata = {
            "templateName": template_name,
            "destinationPath": folder_path,
            "dischargeDocumentFilename": welcome_document_filename,
        }

        solteq_app.create_document_from_template(
            metadata=document_template_metadata
        )

        logger.info("Welcome document was created successfully.")

    else:
        logger.info("Welcome document already exists, skipping creation.")

    return welcome_document_filename


def check_and_send_welcome_document(solteq_app: SolteqTandApp, item_data: dict, solteq_tand_db_object: SolteqTandDatabase, welcome_document_filename: str):
    """
    Check if the welcome document is already sent to DigitalPost; if not, send it.
    This function checks for the existence of welcome document within the last month
    and sends it to DigitalPost if it has not been sent yet.
    """

    one_month_ago = datetime.datetime.now() - relativedelta(months=1)

    # Check if the discharge document is already sent to DigitalPost; if not, send it.
    logger.info("Checking if the welcome document is already sent to DigitalPost.")

    list_of_documents = solteq_tand_db_object.get_list_of_documents(
        filters={
            "p.cpr": item_data["cpr"],
            "ds.OriginalFilename": f"%{welcome_document_filename}%",
            "ds.rn": "1",
            "ds.DocumentStoreStatusId": "1",
            "ds.DocumentCreatedDate": (">=", one_month_ago),
        }
    )

    if (
        list_of_documents
        and not list_of_documents[0]["SentToNemSMS"]
    ):
        logger.info("Discharge document not sent to DigitalPost, proceeding to send.")

        discharge_document_metadata = {
            "documentTitle": welcome_document_filename + ".pdf",
            "digitalPostSubject": "Velkommen til Tandplejen Aarhus",
        }

        solteq_app.send_discharge_document_digitalpost(
            metadata=discharge_document_metadata
        )

        logger.info("Welcome document sent to DigitalPost successfully.")

    else:
        logger.info("Welcome document already sent to DigitalPost or not found, skipping sending.")


def check_and_handle_event(solteq_app: SolteqTandApp, cpr: str, solteq_tand_db_object: SolteqTandDatabase, event_name):
    """
    Afvikler den nyligt oprettede tilflytter hændelse
    """

    logger.info("Checking if event is already processed.")

    filters = {
        "e.currentStateText": [
            f"{event_name}",
        ],
        "p.cpr": cpr,
        "e.archived": 0,
    }

    events = helper_functions.find_events(db_handler=solteq_tand_db_object, filters=filters)

    print()

    print(f"len of events: {len(events)}")

    logger.info(f"Found {len(events)} existing processed tilflytter events.")

    if not events:
        target_values = {event_name, "Henvisning", "Nej"}

        solteq_app.process_target_event(target_values=target_values)

        logger.info("Event was processed successfully.")

    else:
        logger.info("Event already processed, skipping processing.")


def check_and_create_new_event(solteq_app: SolteqTandApp, solteq_tand_db_object: SolteqTandDatabase, event_text: str, cpr: str):
    """
    Check if and event exists in Solteq Tand, and create it if not
    """

    logger.info("Checking if event is already processed.")

    filters = {
        "e.currentStateText": [
            f"{event_text}",
        ],
        "p.cpr": cpr
    }

    events = helper_functions.find_events(db_handler=solteq_tand_db_object, filters=filters)

    if not events:
        solteq_app.create_new_event(clinic_name="Tandplejen Aarhus", event_text=event_text)

        logger.info("Event was created successfully.")

    else:
        logger.info("Event already exists.")
