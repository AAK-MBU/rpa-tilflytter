"""Solteq Tand helpers for the tilflytter flow.

Each check_and_* function is idempotent: it inspects Solteq's current state and only
performs its action (create a document, event, note or booking; send the letter; set a
status) when it has not already been done, then verifies the result. This lets the whole
process be re-run safely after a pause or a failure without duplicating work.
"""

import logging
import os
import time

import datetime
from dateutil.relativedelta import relativedelta

import uiautomation as auto

from mbu_rpa_core.exceptions import BusinessError
from mbu_solteqtand_shared_components.application import SolteqTandApp
from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

logger = logging.getLogger(__name__)

# A journal note shows up in Solteq's UI before it is queryable here: get_list_of_journal_notes
# joins through Forloeb/ForloebSymbolisering/DiagnoseStatus, so the row can lag the save by
# several seconds. Poll for the note instead of assuming one short sleep is enough - otherwise
# a note that was created just fine fails the whole process.
JOURNAL_NOTE_CONFIRM_TIMEOUT_SECONDS = 30
JOURNAL_NOTE_CONFIRM_POLL_SECONDS = 3


def refocus_patient_window(solteq_app: SolteqTandApp):
    """
    Re-point solteq_app.app_window at the patient window (FormPatient).

    SolteqTandApp inherits from every handler in mbu_solteqtand_shared_components, so it
    is a single object with a single self.app_window that all of them read and write.
    The appointment handler reassigns it to the booking pane it just edited
    (`self.app_window = booking_control` in appointment.py) and never restores it, so the
    next handler that navigates the patient card searches the booking pane's subtree
    instead of the patient window. open_tab() then finds no tab, returns None, and dies
    on "'NoneType' object has no attribute 'GetPattern'" - even though the patient window
    is open and the booking update succeeded. close_patient_window() in the shared library
    already works around the same leak by re-acquiring the window; this does it for the
    steps in between.

    Call this after any appointment/booking action, before touching the patient card again.
    """

    logger.info("Re-acquiring the patient window (FormPatient).")

    patient_window = solteq_app.wait_for_control(
        auto.WindowControl,
        {"AutomationId": "FormPatient"},
        search_depth=2,
        timeout=15,
    )

    patient_window.SetFocus()
    solteq_app.app_window = patient_window

    return patient_window


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

    rows = solteq_tand_db_object._execute_query(final_query, tuple(params))

    if rows:
        return False

    return True


def check_and_create_welcome_document(item_data: dict, solteq_app: SolteqTandApp, solteq_tand_db_object: SolteqTandDatabase, age_category):
    """
    Create a welcome document based on the patient's age.
    If the document already exists, it will not be created again.

    Age categories:
    - "0_to_5": Tilflytter 0-5 år - Velkommen (parents only)
    - "6_to_14": Tilflytter 6-14 år - Velkommen (parents only)
    - "15_to_17": Tilflytter 15-17 år - Velkommen (parents and patient)
    - "18_to_21y8m": Tilflytter 18-21 år 8 mdr - Velkommen (young adult)
    - "21y9m_and_older": Tilflytter 21 år 9 mdr - Velkommen (adult)
    """

    if age_category == "0_to_5":
        template_name = "Tilflytter 0-5 år - Velkommen"

    elif age_category == "6_to_14":
        template_name = "Tilflytter 6-14 år - Velkommen"

    elif age_category == "15_to_17":
        template_name = "Tilflytter 15-17 år - Velkommen"

    elif age_category == "18_to_21y8m":
        template_name = "Tilflytter 18-21 år 8 mdr - Velkommen"

    elif age_category == "21y9m_and_older":
        template_name = "Tilflytter 21 år 9 mdr - Velkommen"

    else:
        raise ValueError(f"Unknown age category: {age_category}")

    welcome_document_filename = "Velkomstbrev"

    logger.info("Checking for existing welcome documents.")

    list_of_documents = get_welcome_documents(solteq_tand_db_object, item_data["cpr"], welcome_document_filename)

    logger.info(f"Found {len(list_of_documents)} existing welcome documents.")

    if not list_of_documents:
        folder_path = f"C:\\tmp\\tmt\\{item_data['cpr']}"
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        logger.info("No existing welcome documents found, creating a new one.")
        document_template_metadata = {
            "templateName": template_name,
            "destinationPath": folder_path,
            "dischargeDocumentFilename": welcome_document_filename,
        }

        solteq_app.create_document_from_template(
            metadata=document_template_metadata
        )

        time.sleep(3)  # Wait for the document to be registered in the database

        if not welcome_document_exists(solteq_tand_db_object, item_data["cpr"], welcome_document_filename):
            raise RuntimeError("Welcome document creation failed.")

        logger.info("Welcome document was created successfully.")

    else:
        logger.info("Welcome document already exists, skipping creation.")

    return welcome_document_filename


def check_and_send_welcome_document(item_data: dict, solteq_app: SolteqTandApp, solteq_tand_db_object: SolteqTandDatabase, welcome_document_filename: str):
    """
    Send the welcome letter to Digital Post, unless it has already been sent.

    A document is considered already sent when it exists (within the last month) and its
    SentToNemSMS flag is set, so re-runs after a pause do not send it a second time.
    """

    logger.info("Checking if the welcome document is already sent to DigitalPost.")

    list_of_documents = get_welcome_documents(solteq_tand_db_object, item_data["cpr"], welcome_document_filename)

    if (
        list_of_documents
        and not list_of_documents[0]["SentToNemSMS"]
    ):
        logger.info("Welcome document not sent to DigitalPost, proceeding to send.")

        discharge_document_metadata = {
            "documentTitle": welcome_document_filename + ".pdf",
            "digitalPostSubject": "Velkommen til Tandplejen Aarhus",
        }

        solteq_app.send_discharge_document_digitalpost(
            metadata=discharge_document_metadata
        )

        time.sleep(3)  # Wait for the send status to be registered in the database

        list_of_documents = get_welcome_documents(solteq_tand_db_object, item_data["cpr"], welcome_document_filename)

        if not (list_of_documents and list_of_documents[0]["SentToNemSMS"]):
            raise RuntimeError("Sending welcome document to DigitalPost failed.")

        logger.info("Welcome document sent to DigitalPost successfully.")

    else:
        logger.info("Welcome document already sent to DigitalPost or not found, skipping sending.")


def get_welcome_documents(solteq_tand_db_object: SolteqTandDatabase, cpr: str, welcome_document_filename: str = "Velkomstbrev"):
    """
    Return the citizen's welcome-letter documents journalised within the last month.

    The one-month window scopes the lookup to the current tilflytter run, so a document
    from an earlier run for the same citizen is not mistaken for this run's letter.
    """

    one_month_ago = datetime.datetime.now() - relativedelta(months=1)

    return solteq_tand_db_object.get_documents(cpr, welcome_document_filename, created_after=one_month_ago)


def welcome_document_exists(solteq_tand_db_object: SolteqTandDatabase, cpr: str, welcome_document_filename: str = "Velkomstbrev") -> bool:
    """
    Return True if a welcome document has been journalised for the citizen within
    the last month. Used to confirm a manual send (by Tandplejen) before marking
    the booking as sent, since manual sends are not registered via Digital Post.
    """

    return bool(get_welcome_documents(solteq_tand_db_object, cpr, welcome_document_filename))


def check_and_handle_event(solteq_app: SolteqTandApp, cpr: str, solteq_tand_db_object: SolteqTandDatabase, event_name):
    """
    Process the citizen's newly created tilflytter event in Solteq Tand.

    Idempotent: an already-processed (archived) event is left alone. If the event is not
    present at all - neither processed nor pending - a BusinessError is raised, since the
    citizen is expected to have it before this runs.
    """

    logger.info("Checking if event is already processed.")

    # Processing an event flips e.archived from 0 to 1, so an archived event
    # with this state text means it has already been processed.
    filters = {
        "e.currentStateText": [
            f"{event_name}",
        ],
        "p.cpr": cpr,
        "e.archived": 1,
    }

    events = solteq_tand_db_object.get_list_of_events(
        filters=filters,
        order_by="e.currentStateDate",
        order_direction="DESC",
    )

    print()

    print(f"len of events: {len(events)}")

    logger.info(f"Found {len(events)} existing processed tilflytter events.")

    if not events:
        # Ensure the citizen actually has the unprocessed event before handling it
        unprocessed_filters = {
            "e.currentStateText": [
                f"{event_name}",
            ],
            "p.cpr": cpr,
            "e.archived": 0,
        }

        if not solteq_tand_db_object.get_list_of_events(filters=unprocessed_filters):
            raise BusinessError(f"Event '{event_name}' not found on citizen.")

        if event_name == "Ny tilflytter":
            target_values = {event_name, "Ny tilflytter", "Nej"}

        elif event_name == "Kendt tilflytter":
            target_values = {event_name, "Kendt tilflytter", "Nej"}

        solteq_app.process_target_event(target_values=target_values)

        time.sleep(3)  # Wait for the event state to be registered in the database

        if not solteq_tand_db_object.get_list_of_events(filters=filters):
            raise RuntimeError("Event processing failed.")

        logger.info("Event was processed successfully.")

    else:
        logger.info("Event already processed, skipping processing.")


def check_and_create_new_event(solteq_app: SolteqTandApp, solteq_tand_db_object: SolteqTandDatabase, event_name: str, cpr: str):
    """
    Check if an event exists in Solteq Tand, and create it if not
    """

    logger.info("Checking if event is already processed.")

    filters = {
        "e.currentStateText": [
            f"{event_name}",
        ],
        "p.cpr": cpr
    }

    events = solteq_tand_db_object.get_list_of_events(
        filters=filters,
        order_by="e.currentStateDate",
        order_direction="DESC",
    )

    if not events:
        solteq_app.create_new_event(clinic_name="Tandplejen Aarhus - Kontaktcenter", event_text=event_name)

        time.sleep(3)  # Wait for the event to be registered in the database

        if not solteq_tand_db_object.get_list_of_events(filters=filters):
            raise RuntimeError("Event creation failed.")

        logger.info("Event was created successfully.")

    else:
        logger.info("Event already exists.")


def is_event_processed(solteq_tand_db_object: SolteqTandDatabase, cpr: str, event_name: str) -> bool:
    """
    Return True if the given event has been processed (archived) for the citizen,
    False if it exists but is not yet processed.

    Raises BusinessError if the event does not exist at all: the caller creates the
    event before checking, so a missing event is an anomaly to flag rather than a
    legitimate "not yet processed" state to silently wait on. Processing/approving
    an event in Solteq Tand flips e.archived from 0 to 1.
    """

    filters = {
        "e.currentStateText": [
            f"{event_name}",
        ],
        "p.cpr": cpr,
    }

    events = solteq_tand_db_object.get_list_of_events(filters=filters)

    if not events:
        raise BusinessError(f"Event '{event_name}' not found on citizen.")

    return any(event["archived"] for event in events)


def check_and_create_booking_reminder(solteq_app: SolteqTandApp, solteq_tand_db_object: SolteqTandDatabase, cpr: str, booking_status: str, booking_text: str = "Velkomstbrev"):
    """
    Check if the tilflytter booking reminder exists in Solteq Tand, and create it if not.
    booking_status depends on the citizen's age category (see get_age_category).
    """

    logger.info("Checking if booking reminder already exists.")

    # Only look at future bookings, so old reminders from a previous
    # tilflytter run do not count as existing
    filters = {
        "p.cpr": cpr,
        "bt.Description": "Z - Tilflytter",
        "b.BookingText": booking_text,
        "b.StartTime": (">=", datetime.datetime.now()),
    }

    bookings = solteq_tand_db_object.get_list_of_bookings(filters=filters)

    if not bookings:
        future_date = datetime.datetime.now() + relativedelta(months=3)

        booking_reminder_data = {
            "comboBoxBookingType": "Z - Tilflytter",
            "comboBoxDentist": "Z - Tilflytter",
            "comboBoxChair": "Z - Tilflytter",
            "dateTimePickerStartTime": "07:45",
            "textBoxDuration": "5",
            "comboBoxStatus": booking_status,
            "textBoxBookingText": booking_text,
            "futureDate": future_date.strftime("%d-%m-%Y"),
            "futureDateTime": future_date.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        }

        solteq_app.create_booking_reminder(booking_reminder_data=booking_reminder_data, booking_clinic="Tandplejen Aarhus - Kontaktcenter")

        time.sleep(3)  # Wait for the booking to be registered in the database

        if not solteq_tand_db_object.get_list_of_bookings(filters=filters):
            raise RuntimeError("Booking reminder creation failed.")

        logger.info("Booking reminder was created successfully.")

    else:
        logger.info("Booking reminder already exists, skipping creation.")


def check_and_set_booking_status(solteq_app: SolteqTandApp, solteq_tand_db_object: SolteqTandDatabase, cpr: str, status_id: int, status_text: str, booking_text: str = "Velkomstbrev"):
    """
    Set the tilflytter booking's aftalestatus, unless it already has it.

    b.Status is a numeric status id in the database, while the UI status dropdown
    is selected by its text, so both forms are needed:
    - status_id: the numeric status used for the DB check/verify (e.g. 640).
    - status_text: the dropdown label used to set the status in the UI
      (e.g. "Tilflytter - Velkomstbrev udsendt").

    Idempotent: safe to call on re-runs where the document was already dispatched
    but a previous status update did not complete.
    """

    logger.info("Checking current tilflytter booking status.")

    # b.Status is not in the SELECT of get_list_of_bookings, but it can still be
    # filtered on - a match means the booking already has the target status.
    already_set = solteq_tand_db_object.get_list_of_bookings(
        filters={
            "p.cpr": cpr,
            "b.BookingText": booking_text,
            "b.Status": status_id,
        }
    )

    if already_set:
        logger.info(f"Booking status is already '{status_text}', skipping update.")
        return

    logger.info(f"Locating tilflytter booking to set status to '{status_text}'.")

    appointments = solteq_app.get_list_of_appointments()
    controls = appointments.get("controls", [])
    value_columns = [key for key in appointments if key != "controls"]

    booking_control = None
    for index, control in enumerate(controls):
        row_values = [appointments[column][index] for column in value_columns]

        # Match on the distinctive booking text, falling back to the booking type
        if booking_text in row_values or "Z - Tilflytter" in row_values:
            booking_control = control
            break

    if booking_control is None:
        raise RuntimeError("Could not locate the tilflytter booking to update its status.")

    # Approve the change despite the "no availability for chair/behandler" warning
    # that the Z - Tilflytter admin booking always triggers ("Godkend trods advarsel").
    solteq_app.change_appointment_status_handle_warning(appointment_control=booking_control, set_status=status_text, warning_button="ButtonOk")

    # The booking handler leaves app_window pointing at the booking pane - put it back on
    # the patient window so the next step (journal note, events, ...) can navigate the card.
    refocus_patient_window(solteq_app)

    time.sleep(3)  # Wait for the status change to be registered in the database

    verify = solteq_tand_db_object.get_list_of_bookings(
        filters={
            "p.cpr": cpr,
            "b.BookingText": booking_text,
            "b.Status": status_id,
        }
    )

    if not verify:
        raise RuntimeError(f"Setting booking status to '{status_text}' failed.")

    logger.info(f"Booking status set to '{status_text}' successfully.")


def journal_note_db_value(note_message: str) -> str:
    """
    The value Solteq stores in dn.Beskrivelse for a journal note.

    A note is written in the UI as "<type> <message>" - e.g. "Administrativt notat
    'Velkomstbrev er sendt. Se Dokumenter'" - but Solteq splits the two apart: the type is
    the note's category, and dn.Beskrivelse always holds just the message, without the
    quotes it is displayed in. Looking a note up by the combined string therefore never
    matches, however long you wait for it.
    """

    return note_message.replace("'", "")


def check_and_create_journal_note(solteq_app: SolteqTandApp, solteq_tand_db_object: SolteqTandDatabase, cpr: str, note_type: str, note_message: str):
    """
    Check if a journal note exists in Solteq Tand, and create it if not.

    note_type is the note's category in Solteq ("Administrativt notat"); note_message is the
    note text, quoted the way Solteq displays it ("'Velkomstbrev er sendt. Se Dokumenter'").
    The UI is given the two joined together, the database is queried on the message alone -
    see journal_note_db_value.
    """

    logger.info("Checking if journal note already exists.")

    note_db_value = journal_note_db_value(note_message)

    filters = {
        "p.cpr": cpr,
        "dn.Beskrivelse": note_db_value,
    }

    journal_notes = solteq_tand_db_object.get_list_of_journal_notes(filters=filters)

    if not journal_notes:
        solteq_app.create_journal_note(note_message=f"{note_type} {note_message}", checkmark_in_complete=True)

        # Wait for the journal note to be registered in the database, re-checking every few
        # seconds rather than giving up after the first look.
        deadline = time.monotonic() + JOURNAL_NOTE_CONFIRM_TIMEOUT_SECONDS

        while True:
            time.sleep(JOURNAL_NOTE_CONFIRM_POLL_SECONDS)

            if solteq_tand_db_object.get_list_of_journal_notes(filters=filters):
                break

            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Journal note creation failed - '{note_db_value}' did not appear in "
                    f"dn.Beskrivelse within {JOURNAL_NOTE_CONFIRM_TIMEOUT_SECONDS} seconds."
                )

            logger.info("Journal note not registered in the database yet - re-checking.")

        logger.info("Journal note was created successfully.")

    else:
        logger.info("Journal note already exists, skipping creation.")
