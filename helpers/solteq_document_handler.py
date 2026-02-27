"""Handle the creation of discharge documents based on patient age."""

import sys

import os

import logging

import datetime
from dateutil.relativedelta import relativedelta

from mbu_solteqtand_shared_components.application import SolteqTandApp
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

logger = logging.getLogger(__name__)


def check_and_create_welcome_document(solteq_app: SolteqTandApp, item_data: dict, solteq_tand_db_object: SolteqTandDatabase, age_category):
    """
    Create a welcome document based on the patient's age.
    If the document already exists, it will not be created again.
    """

    if age_category == "under_18":
        template_name = "Velkomstbrev til forældre og patient under 18"

        discharge_document_filename = "Velkomstbrev"

    elif age_category == "is_21y9m_or_older":
        template_name = "Tilflytter 21 år 9 mdr - Velkommen"

        discharge_document_filename = "Velkomstbrev"

    else:
        template_name = "Velkomstbrev til ung fra 18-21 år og 8 måneder"

        discharge_document_filename = "Velkomstbrev"

    one_month_ago = datetime.datetime.now() - relativedelta(months=1)

    logger.info("Checking for existing welcome documents.")

    list_of_documents = solteq_tand_db_object.get_list_of_documents(
        filters={
            "p.cpr": item_data["cpr"],
            "ds.OriginalFilename": f"%{discharge_document_filename}%",
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
            "dischargeDocumentFilename": discharge_document_filename,
        }

        solteq_app.create_document_from_template(
            metadata=document_template_metadata
        )

        logger.info("Welcome document was created successfully.")

    else:
        logger.info("Welcome document already exists, skipping creation.")

    return discharge_document_filename


def check_and_send_welcome_document(solteq_app: SolteqTandApp, item_data: dict, solteq_tand_db_object: SolteqTandDatabase, welcome_document_filename: str):
    """
    Check if the welcome document is already sent to DigitalPost; if not, send it.
    This function checks for the existence of welcome document within the last month
    and sends it to DigitalPost if it has not been sent yet.
    """

    one_month_ago = datetime.datetime.now() - relativedelta(months=1)

    # Check if the discharge document is already sent to DigitalPost; if not, send it.
    logger.info("Checking if the welcome document is already sent to DigitalPost.")

    list_of_documents_send_document = solteq_tand_db_object.get_list_of_documents(
        filters={
            "p.cpr": item_data["cpr"],
            "ds.OriginalFilename": f"%{welcome_document_filename}%",
            "ds.rn": "1",
            "ds.DocumentStoreStatusId": "1",
            "ds.DocumentCreatedDate": (">=", one_month_ago),
        }
    )

    if (
        list_of_documents_send_document
        and not list_of_documents_send_document[0]["SentToNemSMS"]
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
