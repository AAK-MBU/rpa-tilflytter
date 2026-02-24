"""Module for handling application startup, and close"""

import logging
import subprocess as sp
from subprocess import CalledProcessError

from mbu_dev_shared_components.database.connection import RPAConnection
from mbu_solteqtand_shared_components.application import SolteqTandApp

from helpers import config

APP = None
logger = logging.getLogger(__name__)


def get_app():
    # ruff: noqa: PLW0602
    global APP
    return APP


def startup():
    """Function for starting applications"""
    logger.info("Starting applications...")

    with RPAConnection(db_env="PROD", commit=False) as rpa_conn:
        creds = rpa_conn.get_credential("solteq_tand_svcrpambu001")
        username = creds["username"]
        password = creds["decrypted_password"]

    solteq_app = SolteqTandApp(
        app_path=config.APP_PATH,
        username=username,
        password=password
    )

    solteq_app.start_application()
    solteq_app.login()

    # ruff: noqa: PLW0603
    global APP
    APP = solteq_app


def soft_close():
    """Function for closing applications softly"""
    logger.info("Closing applications softly...")
    solteq_app = get_app()
    solteq_app.close_solteq_tand()
    logger.info("Closed application softly")


def hard_close():
    """Function for closing applications hard"""
    logger.info("Closing applications hard...")
    list_processes = ["wmic", "process", "get", "description"]
    if "TMTand.exe" in sp.check_output(list_processes).strip().decode():
        try:
            kill_msg = sp.check_output(["taskkill", "/f", "/im", "TMTand.exe"])

            logger.info(kill_msg)

        except CalledProcessError as e:
            logger.error(f"TMTand.exe found in subprocesses, but error while killing it: {e}")


def close():
    """Function for closing applications softly or hardly if necessary"""
    try:
        soft_close()

    except Exception:
        hard_close()


def reset():
    """Function for resetting application"""
    close()
    startup()