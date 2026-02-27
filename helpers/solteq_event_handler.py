"""Handle the creation of discharge documents based on patient age."""

import logging

from mbu_solteqtand_shared_components.application import SolteqTandApp
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

logger = logging.getLogger(__name__)


def handle_tilflytter_event(solteq_app: SolteqTandApp, cpr: str, solteq_tand_db_object: SolteqTandDatabase):
    """
    Afvikler den nyligt oprettede tilflytter hændelse
    """

    logger.info("Checking if event is already processed.")

    filters = {
        "e.currentStateText": [
            # "Ny tilflytter",
            # "Kendt tilflytter",
            "TEST: Ny tilflytter",
        ],
        "e.archived": 1,
        "p.cpr": cpr
    }

    events = helper_functions.find_events(db_handler=solteq_tand_db_object, filters=filters)

    print()

    print(f"len of events: {len(events)}")

    logger.info(f"Found {len(events)} existing processed tilflytter events.")

    if not events:
        solteq_app.process_tilflytter_event()

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
        solteq_app.create_new_event(clinic_name="Tandplejen Aarhus", event_text="Tilflytter - Formular ikke udfyldt")

        logger.info("Event was created successfully.")

    else:
        logger.info("Event already exists.")
