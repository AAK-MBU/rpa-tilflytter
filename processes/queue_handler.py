"""Module to hande queue population"""

import sys

import os

import asyncio
import json
import logging

from automation_server_client import Workqueue

from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import config, helper_functions

SOLTEQ_TAND_DB_CONN_STRING = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")


logger = logging.getLogger(__name__)


def retrieve_items_for_queue() -> list[dict]:
    """Function to populate queue"""

    references = []
    data = []

    db_handler = SolteqTandDatabase(conn_str=SOLTEQ_TAND_DB_CONN_STRING)

    filters = {
        "e.currentStateText": [
            # "Ny tilflytter",
            # "Kendt tilflytter",
            "TEST: Ny tilflytter",
        ],
        "e.archived": 0
    }

    events = helper_functions.find_events(db_handler=db_handler, filters=filters)

    print()

    print(f"len of events: {len(events)}")

    print()

    for ev in events:
        print(ev)

        ev_title = ev.get("currentStateText")

        citizen_cpr = ev.get("cpr")

        event_created_date = ev.get("currentStateDate")

        citizen_dict = {
            "cpr": citizen_cpr,
            "name": ev.get("fullName"),
            "event_name": ev_title,
            "event_created_date": event_created_date.isoformat(),
            "event_last_modified": ev.get("timestamp").isoformat()
        }

        references.append(citizen_cpr)

        data.append(citizen_dict)

        break

    items = [
        {"reference": ref, "data": d} for ref, d in zip(references, data, strict=True)
    ]

    return items


def create_sort_key(item: dict) -> str:
    """
    Create a sort key based on the entire JSON structure.
    Converts the item to a sorted JSON string for consistent ordering.
    """
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


async def concurrent_add(workqueue: Workqueue, items: list[dict]) -> None:
    """
    Populate the workqueue with items to be processed.
    Uses concurrency and retries with exponential backoff.

    Args:
        workqueue (Workqueue): The workqueue to populate.
        items (list[dict]): List of items to add to the queue.

    Returns:
        None

    Raises:
        Exception: If adding an item fails after all retries.
    """
    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    async def add_one(it: dict):
        reference = str(it.get("reference") or "")
        data = {"item": it}

        async with sem:
            for attempt in range(1, config.MAX_RETRIES + 1):
                try:
                    await asyncio.to_thread(workqueue.add_item, data, reference)
                    logger.info("Added item to queue with reference: %s", reference)
                    return True

                except Exception as e:
                    if attempt >= config.MAX_RETRIES:
                        logger.error(
                            "Failed to add item %s after %d attempts: %s",
                            reference,
                            attempt,
                            e,
                        )
                        return False

                    backoff = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))

                    logger.warning(
                        "Error adding %s (attempt %d/%d). Retrying in %.2fs... %s",
                        reference,
                        attempt,
                        config.MAX_RETRIES,
                        backoff,
                        e,
                    )
                    await asyncio.sleep(backoff)

    if not items:
        logger.info("No new items to add.")
        return

    sorted_items = sorted(items, key=create_sort_key)
    logger.info(
        "Processing %d items sorted by complete JSON structure", len(sorted_items)
    )

    results = await asyncio.gather(*(add_one(i) for i in sorted_items))
    successes = sum(1 for r in results if r)
    failures = len(results) - successes

    logger.info(
        "Summary: %d succeeded, %d failed out of %d", successes, failures, len(results)
    )
