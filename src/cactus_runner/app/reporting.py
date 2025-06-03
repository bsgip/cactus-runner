import io
import logging
from datetime import datetime, timedelta
from pathlib import Path

from _pytest import runner
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    ParagraphStyle,
    StyleSheet1,
    getSampleStyleSheet,
)
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cactus_runner.models import ClientInteraction, ClientInteractionType, RunnerState

logger = logging.getLogger(__name__)

DEFAULT_SPACER = Spacer(1, 10)


def table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ]
    )


def stylesheet() -> StyleSheet1:
    return getSampleStyleSheet()


def title(test_procedure_name: str, style: ParagraphStyle) -> list:
    return [Paragraph(f"{test_procedure_name} Test Procedure Report", style), Spacer(1, 10)]


def test_procedure_overview(
    test_procedure_name: str,
    init_timestamp: datetime,
    start_timestamp: datetime,
    client_lfdi: str,
    duration: timedelta,
    style: ParagraphStyle,
    table_style: TableStyle,
) -> list:
    elements = []
    elements.append(Paragraph("Overview", style))
    doe_data = [
        ["Test Procedure", test_procedure_name],
        ["Client LFDI", client_lfdi],
        ["Test Initialisation Timestamp (UTC)", init_timestamp.isoformat()],
        ["Test Start Timestamp (UTC)", start_timestamp.isoformat()],
        ["Duration", duration],
    ]
    table = Table(doe_data, colWidths=[200, 250])
    table.setStyle(table_style)
    elements.append(table)
    elements.append(DEFAULT_SPACER)
    return elements


def test_procedure_communications(
    style: ParagraphStyle,
) -> list:
    elements = []
    elements.append(Paragraph("Communications", style))
    elements.append(DEFAULT_SPACER)
    return elements


def first_client_interaction_of_type(
    client_interactions: list[ClientInteraction], interaction_type: ClientInteractionType
):
    for client_interaction in client_interactions:
        if client_interaction.interaction_type == interaction_type:
            return client_interaction
    raise ValueError(f"No client interactions found with type={interaction_type}")


def generate_page_elements(runner_state: RunnerState, styles: StyleSheet1) -> list:
    active_test_procedure = runner_state.active_test_procedure
    if active_test_procedure is None:
        raise ValueError("'active_test_procedure' attribute of 'runner_state' cannot be None")

    page_elements = []

    test_procedure_name = active_test_procedure.name

    page_elements.extend(title(test_procedure_name, style=styles["Title"]))

    try:
        init_timestamp = first_client_interaction_of_type(
            client_interactions=runner_state.client_interactions,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT,
        ).timestamp
        start_timestamp = first_client_interaction_of_type(
            client_interactions=runner_state.client_interactions,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_START,
        ).timestamp
        duration = runner_state.last_client_interaction.timestamp - init_timestamp

        page_elements.extend(
            test_procedure_overview(
                test_procedure_name=test_procedure_name,
                init_timestamp=init_timestamp,
                start_timestamp=start_timestamp,
                client_lfdi=active_test_procedure.client_lfdi,
                duration=duration,
                style=styles["Heading1"],
                table_style=table_style(),
            )
        )
    except ValueError as e:
        # ValueError is raised by 'first_client_interaction_of_type' if it can find the required
        # client interations. This is a guard-rail. If we have an active test procedure then
        # the appropriate client interactions SHOULD be defined in the runner state.
        logger.error(f"Unable to add 'test procedure summary' to PDF report. Reason={repr(e)}")

    page_elements.extend(test_procedure_communications(style=styles["Heading1"]))

    return page_elements


def generate_page_elements_no_active_procedure(styles: StyleSheet1):
    page_elements = []
    page_elements.append(Paragraph("Test Procedure Report", styles["Title"]))
    page_elements.append(DEFAULT_SPACER)
    page_elements.append(
        Paragraph("NO ACTIVE TEST PROCEDURE", ParagraphStyle("red-title", parent=styles["Title"], textColor=colors.red))
    )
    return page_elements


def pdf_report_as_bytes(page_elements: list) -> bytes:
    with io.BytesIO() as buffer:
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        doc.build(page_elements)
        pdf_data = buffer.getvalue()

    return pdf_data


def generate_pdf_report(file_path: Path, runner_state: RunnerState):
    styles = getSampleStyleSheet()

    if runner_state.active_test_procedure is not None:
        page_elements = generate_page_elements(runner_state=runner_state, styles=styles)
    else:
        page_elements = generate_page_elements_no_active_procedure(styles=styles)
    pdf_data = pdf_report_as_bytes(page_elements=page_elements)

    with open(file_path, "wb") as f:
        f.write(pdf_data)
