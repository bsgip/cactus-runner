import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
from envoy.server.model.site import Site, SiteDER
from envoy.server.model.site_reading import SiteReadingType
from envoy_schema.server.schema.sep2.types import DataQualifierType, PhaseCode, UomType
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    ParagraphStyle,
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

DEFAULT_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
    ]
)


@dataclass
class StyleSheet:
    """A collection of all the styles used in the PDF report"""

    title: ParagraphStyle
    heading: ParagraphStyle
    subheading: ParagraphStyle
    table: TableStyle
    spacer: Spacer
    date_format: str


def get_stylesheet() -> StyleSheet:
    sample_style_sheet = getSampleStyleSheet()
    return StyleSheet(
        title=sample_style_sheet.get("Title"),
        heading=sample_style_sheet.get("Heading2"),
        subheading=sample_style_sheet.get("Heading3"),
        table=DEFAULT_TABLE_STYLE,
        spacer=DEFAULT_SPACER,
        date_format="%Y-%m-%d %H:%M:%S",
    )


def generate_title(test_procedure_name: str, style: ParagraphStyle) -> list:
    return [Paragraph(f"{test_procedure_name} Test Procedure Report", style), Spacer(1, 10)]


def generate_overview_section(
    test_procedure_name: str,
    init_timestamp: datetime,
    start_timestamp: datetime,
    client_lfdi: str,
    duration: timedelta,
    stylesheet: StyleSheet,
) -> list:
    elements = []
    elements.append(Paragraph("Overview", stylesheet.heading))
    doe_data = [
        ["Test Procedure", test_procedure_name],
        ["Client LFDI", client_lfdi],
        ["Test Initialisation Timestamp (UTC)", init_timestamp.strftime(stylesheet.date_format)],
        ["Test Start Timestamp (UTC)", start_timestamp.strftime(stylesheet.date_format)],
        ["Duration", str(duration).split(".")[0]],  # remove microseconds from output
    ]
    table = Table(doe_data, colWidths=[200, 250])
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_criteria_section(check_results: dict[str, CheckResult], stylesheet: StyleSheet) -> list:
    elements = []
    elements.append(Paragraph("Criteria", stylesheet.heading))
    criteria_data = [
        [
            check_name,
            "PASS" if check_result.passed else "FAIL",
            Paragraph("" if check_result.description is None else check_result.description),
        ]
        for check_name, check_result in check_results.items()
    ]
    criteria_data.insert(0, ["Criteria Name", "Pass/Fail", "Comment"])
    table = Table(criteria_data, colWidths=[130, 60, 250])
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_requests_timeline(request_timestamps: list[datetime]) -> Image:

    WIDTH = 500
    HEIGHT = 250
    df = pd.DataFrame({"timestamp": request_timestamps})
    fig = px.histogram(df, x="timestamp", labels={"timestamp": "Time (UTC)"})
    fig.update_layout(bargap=0.2)
    fig.update_layout(yaxis_title="Number of requests")
    fig.update_layout(
        autosize=False,
        width=WIDTH,
        height=HEIGHT,
        margin=dict(l=30, r=30, b=50, t=50, pad=4),
    )

    img_bytes = fig.to_image(format="png")
    buffer = io.BytesIO(img_bytes)
    return Image(buffer)


def generate_communications_section(
    request_timestamps: list[datetime],
    stylesheet: StyleSheet,
) -> list:
    elements = []
    elements.append(Paragraph("Communications", stylesheet.heading))
    if request_timestamps:
        elements.append(generate_requests_timeline(request_timestamps=request_timestamps))
    else:
        elements.append(Paragraph("No requests were received by utility server during the test procedure."))
    elements.append(stylesheet.spacer)
    return elements


def generate_site_der_table(site: Site, stylesheet: StyleSheet) -> list:
    elements = []
    table_data = [
        [
            Paragraph(site_der.site_der_rating.model_dump()),
            Paragraph(site_der.site_der_setting.model_dump()),
            Paragraph(site_der.site_der_availability.model_dump()),
            Paragraph(site_der.site_der_status.model_dump()),
        ]
        for site_der in site.site_ders
    ]
    table_data.insert(0, [Paragraph("Rating"), Paragraph("Setting"), Paragraph("Availability"), Paragraph("Status")])
    table = Table(table_data)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_site_section(site: Site, stylesheet: StyleSheet) -> list:
    elements = []

    if site.nmi:
        section_title = f"Site {site.site_id} (nmi: {site.nmi})"
        site_description = f"Created at {site.created_time.strftime(stylesheet.date_format)}"
    else:
        section_title = f"Site {site.site_id}"
        site_description = "Generated as part of test procedure precondition."
    elements.append(Paragraph(section_title, stylesheet.subheading))
    elements.append(Paragraph(site_description))
    elements.append(stylesheet.spacer)
    if site.site_ders:
        elements.extend(generate_site_der_table(site=site, stylesheet=stylesheet))
    else:
        elements.append(Paragraph("No Site DER registered for this site."))
    return elements


def generate_devices_section(sites: list[Site], stylesheet: StyleSheet) -> list:
    elements = []
    elements.append(Paragraph("Devices", stylesheet.heading))
    if sites:
        for site in sites:
            elements.extend(generate_site_section(site=site, stylesheet=stylesheet))
    else:
        elements.append(Paragraph("No devices registered either out-of-band or in-band during this test procedure."))
    elements.append(stylesheet.spacer)
    return elements


def generate_readings_timeline(readings_df: pd.DataFrame, quantity: str) -> Image:
    WIDTH = 500
    HEIGHT = 250

    fig = px.line(readings_df, x="time_period_start", y="scaled_value", markers=True)

    fig.update_layout(
        autosize=False,
        width=WIDTH,
        height=HEIGHT,
        margin=dict(l=30, r=30, b=50, t=50, pad=4),
    )
    fig.update_layout(
        xaxis=dict(title=dict(text="Time (UTC)")),
        yaxis=dict(title=dict(text=quantity)),
    )

    img_bytes = fig.to_image(format="png")
    buffer = io.BytesIO(img_bytes)
    return Image(buffer)


def reading_quantity(srt: SiteReadingType) -> str:
    quantity = UomType(srt.uom).name
    quantity = quantity.replace("_", " ").title()
    return quantity


def reading_description(srt: SiteReadingType) -> str:
    mup = srt.site_reading_type_id
    quantity = reading_quantity(srt)
    qualifier = DataQualifierType(srt.data_qualifier).name
    qualifier = qualifier.replace("_", " ").title()
    if srt.phase == 0:
        description = f"MUP {mup}: {quantity} ({qualifier})"
    else:
        phase = PhaseCode(srt.phase).name
        phase = phase.replace("_", " ").title()
        description = f"MUP {mup}: {quantity} ({qualifier}, {phase})"

    return description


def generate_reading_count_table(reading_counts: dict[SiteReadingType, int], stylesheet: StyleSheet) -> list:
    elements = []

    table_data = [
        [reading_type.site_reading_type_id, reading_description(reading_type), count]
        for reading_type, count in reading_counts.items()
    ]
    table_data.insert(0, ["MUP", "Description", "Number received"])
    table = Table(table_data, colWidths=[50, 250, 100])
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_readings_section(
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    stylesheet: StyleSheet,
) -> list:
    elements = []
    elements.append(Paragraph("Readings", stylesheet.heading))

    # Add table to show how many of each reading type was sent to the utility server (all reading types)
    if reading_counts:
        elements.extend(generate_reading_count_table(reading_counts=reading_counts, stylesheet=stylesheet))

        # Add charts for each of the different reading types
        if readings:
            for reading_type, readings_df in readings.items():
                elements.append(Paragraph(reading_description(reading_type), style=stylesheet.subheading))
                elements.append(
                    generate_readings_timeline(readings_df=readings_df, quantity=reading_quantity(reading_type))
                )
    else:
        elements.append(Paragraph("No readings sent to the utility server during this test procedure."))

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
    sites: list[Site],
    stylesheet: StyleSheet,
) -> list:
    active_test_procedure = runner_state.active_test_procedure
    if active_test_procedure is None:
        raise ValueError("'active_test_procedure' attribute of 'runner_state' cannot be None")

    page_elements = []

    test_procedure_name = active_test_procedure.name

    # Document Title
    page_elements.extend(generate_title(test_procedure_name, style=stylesheet.title))

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
            generate_overview_section(
                test_procedure_name=test_procedure_name,
                init_timestamp=init_timestamp,
                start_timestamp=start_timestamp,
                client_lfdi=active_test_procedure.client_lfdi,
                duration=duration,
                stylesheet=stylesheet,
            )
        )
    except ValueError as e:
        # ValueError is raised by 'first_client_interaction_of_type' if it can find the required
        # client interations. This is a guard-rail. If we have an active test procedure then
        # the appropriate client interactions SHOULD be defined in the runner state.
        logger.error(f"Unable to add 'test procedure overview' to PDF report. Reason={repr(e)}")

    # Criteria Section
    page_elements.extend(generate_criteria_section(check_results=check_results, stylesheet=stylesheet))

    # Communications Section
    request_timestamps = [request_entry.timestamp for request_entry in runner_state.request_history]
    page_elements.extend(generate_communications_section(request_timestamps=request_timestamps, stylesheet=stylesheet))

    # Devices Section
    page_elements.extend(generate_devices_section(sites=sites, stylesheet=stylesheet))

    # Readings Section
    page_elements.extend(
        generate_readings_section(readings=readings, reading_counts=reading_counts, stylesheet=stylesheet)
    )

    return page_elements


def generate_page_elements_no_active_procedure(stylesheet: StyleSheet):
    page_elements = []
    page_elements.append(Paragraph("Test Procedure Report", stylesheet.title))
    page_elements.append(DEFAULT_SPACER)
    page_elements.append(
        Paragraph(
            "NO ACTIVE TEST PROCEDURE", ParagraphStyle("red-title", parent=stylesheet.title, textColor=colors.red)
        )
    )
    return page_elements


def pdf_report_as_bytes(
    runner_state: RunnerState,
    check_results: dict[str, CheckResult],
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    sites: list[Site],
) -> bytes:
    stylesheet = get_stylesheet()

    if runner_state.active_test_procedure is not None:
        page_elements = generate_page_elements(
            runner_state=runner_state,
            check_results=check_results,
            readings=readings,
            reading_counts=reading_counts,
            sites=sites,
            stylesheet=stylesheet,
        )
    else:
        page_elements = generate_page_elements_no_active_procedure(stylesheet=stylesheet)

    with io.BytesIO() as buffer:
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        doc.build(page_elements)
        pdf_data = buffer.getvalue()

    return pdf_data
