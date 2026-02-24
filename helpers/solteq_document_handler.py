"""Handle the creation of discharge documents based on patient age."""

import os

import logging

import datetime
from dateutil.relativedelta import relativedelta

from mbu_solteqtand_shared_components.application import SolteqTandApp
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

logger = logging.getLogger(__name__)


def create_welcome_document(solteq_tand_app: SolteqTandApp, item_data: dict, solteq_tand_db_object: SolteqTandDatabase, age_category):
    """
    Create a welcome document based on the patient's age.
    If the document already exists, it will not be created again.
    """

    age_category = "is_21y9m_or_older"

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

        solteq_tand_app.create_document_from_template(
            metadata=document_template_metadata
        )

        logger.info("Welcome document was created successfully.")

    else:
        logger.info("Welcome document already exists, skipping creation.")
