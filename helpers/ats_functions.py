"""Helper module to call some functionality in Automation Server using the API"""

import logging
import os

import requests

from automation_server_client import AutomationServer, WorkItem, Workqueue
from dotenv import load_dotenv

ATS_TOKEN = os.getenv("ATS_TOKEN")
ATS_URL = os.getenv("ATS_URL")


def get_workqueue_items(workqueue: Workqueue, return_data=False):
    """
    Retrieve items from the specified workqueue.
    If the queue is empty, return an empty list.
    """
    load_dotenv()

    if not ATS_URL or not ATS_TOKEN:
        raise OSError("ATS_URL or ATS_TOKEN is not set in the environment")

    headers = {"Authorization": f"Bearer {ATS_TOKEN}"}

    workqueue_items = {} if return_data else set()

    page = 1
    size = 200  # max allowed

    while True:
        full_url = f"{ATS_URL}/workqueues/{workqueue.id}/items?page={page}&size={size}"
        response = requests.get(full_url, headers=headers, timeout=60)
        response.raise_for_status()

        res_json = response.json().get("items", [])

        if not res_json:
            break

        for row in res_json:
            ref = row.get("reference")
            if ref:
                if return_data:
                    workqueue_items[ref] = row
                else:
                    workqueue_items.add(ref)

        page += 1

    return workqueue_items


def get_item_info(item: WorkItem):
    """Unpack item.

    The ATS work item id is returned alongside the payload so business errors can carry
    it to the process dashboard as a rerun target (see processes/process_item.py).
    """
    return item.data["item"]["data"], item.data["item"]["reference"], item.id


def init_logger():
    """Initialize the root logger with JSON formatting."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(module)s.%(funcName)s:%(lineno)d — %(message)s",
        datefmt="%H:%M:%S",
    )


def fetch_workqueue(workqueue_name: str):
    """
    Helper function to fetch the next workqueue in the overall process flow
    """

    headers = {"Authorization": f"Bearer {ATS_TOKEN}"}

    full_url = f"{ATS_URL}/workqueues/by_name/{workqueue_name}"

    response_json = requests.get(full_url, headers=headers, timeout=60).json()
    workqueue_id = response_json.get("id")

    os.environ["ATS_WORKQUEUE_OVERRIDE"] = str(workqueue_id)  # override it
    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    return workqueue


def enqueue_items(workqueue: Workqueue, items: list[dict]):
    """
    Enqueues each (reference, data) pair to the next workqueue, avoiding duplicates.

    Used for standard flows where further processing is required in later steps.
    """

    existing_refs = {str(r) for r in get_workqueue_items(workqueue)}

    for it in items:
        reference = it.get("cpr")

        if reference and reference not in existing_refs:
            workqueue.add_item(
                {"item": {"reference": reference, "data": it}}, reference
            )
