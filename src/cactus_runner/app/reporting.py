import io
import logging
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    ParagraphStyle,
    StyleSheet1,
    getSampleStyleSheet,
)
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cactus_runner.app.check import CheckResult
from cactus_runner.models import ClientInteraction, ClientInteractionType, RunnerState

logger = logging.getLogger(__name__)

DEFAULT_SPACER = Spacer(1, 20)


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


def test_procedure_criteria(
    check_results: dict[str, CheckResult], style: ParagraphStyle, table_style: TableStyle
) -> list:
    elements = []
    elements.append(Paragraph("Criteria", style))
    criteria_data = [
        [check_name, "PASS" if check_result.passed else "FAIL", check_result.description]
        for check_name, check_result in check_results.items()
    ]
    criteria_data.insert(0, ["Criteria Name", "Pass/Fail", "Description"])
    table = Table(criteria_data, colWidths=[140, 80, 200])
    table.setStyle(table_style)
    elements.append(table)
    elements.append(DEFAULT_SPACER)
    return elements


def requests_timeline(request_timestamps: list[datetime]) -> Image:

    WIDTH = 500
    HEIGHT = 250
    df = pd.DataFrame({"timestamp": request_timestamps})
    fig = px.histogram(df, x="timestamp", labels={"timestamp": "Time (UTC)"})
    fig.update_layout(bargap=0.2)
    fig.update_layout(title_text="Requests over time", title_x=0.5)
    fig.update_layout(
        autosize=False,
        width=WIDTH,
        height=HEIGHT,
        margin=dict(l=30, r=30, b=50, t=50, pad=4),
    )

    img_bytes = fig.to_image(format="png")
    buffer = io.BytesIO(img_bytes)
    return Image(buffer)


def test_procedure_communications(
    request_timestamps: list[datetime],
    style: ParagraphStyle,
) -> list:
    elements = []
    elements.append(Paragraph("Communications", style))
    elements.append(requests_timeline(request_timestamps=request_timestamps))
    elements.append(DEFAULT_SPACER)
    return elements


def readings_timeline(readings_df: pd.DataFrame, title: str) -> Image:
    WIDTH = 500
    HEIGHT = 250

    fig = px.line(readings_df, x="created_time", y="scaled_value")

    fig.update_layout(title_text=title, title_x=0.5)
    fig.update_layout(
        autosize=False,
        width=WIDTH,
        height=HEIGHT,
        margin=dict(l=30, r=30, b=50, t=50, pad=4),
    )

    img_bytes = fig.to_image(format="png")
    buffer = io.BytesIO(img_bytes)
    return Image(buffer)


def reading_count_table(reading_counts: dict[SiteReadingType, int], table_style) -> list:
    elements = []
    table_data = [[f"{reading_type.uom}", count] for reading_type, count in reading_counts.items()]
    table_data.insert(0, ["Reading Type", "Counts"])
    table = Table(table_data, colWidths=[140, 80])
    table.setStyle(table_style)
    elements.append(table)
    elements.append(DEFAULT_SPACER)
    return elements


def test_procedure_readings(
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    style: ParagraphStyle,
    table_style: TableStyle,
) -> list:
    elements = []
    elements.append(Paragraph("Readings", style))

    # Add table to show how many of each reading type was sent to the utility server (all reading types)
    if reading_counts:
        elements.extend(reading_count_table(reading_counts=reading_counts, table_style=table_style))

    # Add charts for each of the different reading types
    # if readings:
    #     for reading_type, readings_df in readings.items():
    #         title = f"{reading_type}"
    #         elements.append(readings_timeline(readings_df=readings_df, title=title))

    elements.append(DEFAULT_SPACER)
    return elements


def first_client_interaction_of_type(
    client_interactions: list[ClientInteraction], interaction_type: ClientInteractionType
):
    for client_interaction in client_interactions:
        if client_interaction.interaction_type == interaction_type:
            return client_interaction
    raise ValueError(f"No client interactions found with type={interaction_type}")


def generate_page_elements(
    runner_state: RunnerState,
    check_results: dict[str, CheckResult],
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    styles: StyleSheet1,
) -> list:
    active_test_procedure = runner_state.active_test_procedure
    if active_test_procedure is None:
        raise ValueError("'active_test_procedure' attribute of 'runner_state' cannot be None")

    page_elements = []

    test_procedure_name = active_test_procedure.name

    # Document Title
    page_elements.extend(title(test_procedure_name, style=styles["Title"]))

    # Overview Section
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
        logger.error(f"Unable to add 'test procedure overview' to PDF report. Reason={repr(e)}")

    # Criteria Section
    page_elements.extend(
        test_procedure_criteria(check_results=check_results, style=styles["Heading1"], table_style=table_style())
    )

    # Communications Section
    request_timestamps = [request_entry.timestamp for request_entry in runner_state.request_history]
    page_elements.extend(test_procedure_communications(request_timestamps=request_timestamps, style=styles["Heading1"]))

    # Readings Section
    page_elements.extend(
        test_procedure_readings(
            readings=readings, reading_counts=reading_counts, style=styles["Heading1"], table_style=table_style()
        )
    )

    return page_elements


def generate_page_elements_no_active_procedure(styles: StyleSheet1):
    page_elements = []
    page_elements.append(Paragraph("Test Procedure Report", styles["Title"]))
    page_elements.append(DEFAULT_SPACER)
    page_elements.append(
        Paragraph("NO ACTIVE TEST PROCEDURE", ParagraphStyle("red-title", parent=styles["Title"], textColor=colors.red))
    )
    return page_elements


def pdf_report_as_bytes(
    runner_state: RunnerState,
    check_results: dict[str, CheckResult],
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
) -> bytes:
    styles = getSampleStyleSheet()

    if runner_state.active_test_procedure is not None:
        page_elements = generate_page_elements(
            runner_state=runner_state,
            check_results=check_results,
            readings=readings,
            reading_counts=reading_counts,
            styles=styles,
        )
    else:
        page_elements = generate_page_elements_no_active_procedure(styles=styles)

    with io.BytesIO() as buffer:
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        doc.build(page_elements)
        pdf_data = buffer.getvalue()

    return pdf_data
