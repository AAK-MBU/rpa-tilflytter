"""Handle the creation of discharge documents based on patient age."""

import os

import logging

import datetime
from dateutil.relativedelta import relativedelta

from mbu_solteqtand_shared_components.application import SolteqTandApp
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

logger = logging.getLogger(__name__)


def handle_tilflytter_event(solteq_tand_app: SolteqTandApp, cpr: str, solteq_tand_db_object: SolteqTandDatabase):
    """
    Create a discharge document based on the patient's age.
    If the document already exists, it will not be created again.
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
        solteq_tand_app.process_tilflytter_event()

        logger.info("Event was processed successfully.")

    else:
        logger.info("Event already processed, skipping processing.")
