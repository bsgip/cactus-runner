import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial

import pandas as pd
import PIL.Image as PilImage
import plotly.express as px
from envoy.server.model.site import Site, SiteDER
from envoy.server.model.site_reading import SiteReadingType
from envoy_schema.server.schema.sep2.types import DataQualifierType, PhaseCode, UomType
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    ParagraphStyle,
    getSampleStyleSheet,
)
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cactus_runner import __version__
from cactus_runner.app.check import CheckResult
from cactus_runner.models import ClientInteraction, ClientInteractionType, RunnerState

logger = logging.getLogger(__name__)

PAGE_WIDTH, PAGE_HEIGHT = A4
DEFAULT_SPACER = Spacer(1, 0.25 * inch)
MARGIN = 0.5 * inch
BANNER_HEIGHT = inch

HIGHLIGHT_COLOR = HexColor(0x09BB71)  # Teal green used on cactus UI
TABLE_HEADER_COLOR = HexColor(0xF5F5F5)
TABLE_LINE_COLOR = HexColor(0xE0E0E0)
TABLE_TEXT_COLOR = HexColor(0x262626)
WARNING_COLOR = HexColor(0xFF4545)
TEXT_COLOR = HexColor(0x000000)
WHITE = HexColor(0xFFFFFF)
# OVERVIEW_BACKGROUND = HexColor(0x96EAC7)
OVERVIEW_BACKGROUND = HexColor(0xD7FCEF)

DEFAULT_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_COLOR),
        ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_TEXT_COLOR),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("GRID", (0, 0), (-1, -1), 1, TABLE_LINE_COLOR),
    ]
)


# Limit all tables to full width of page (minus margins)
DEFAULT_MAX_TABLE_WIDTH = PAGE_WIDTH - 2 * MARGIN

DOCUMENT_TITLE = "CSIP-AUS Client Test Procedure"
AUTHOR = "Cactus Test Harness"
AUTHOR_URL = "https://cactus.cecs.anu.edu.au"


@dataclass
class StyleSheet:
    """A collection of all the styles used in the PDF report"""

    title: ParagraphStyle
    heading: ParagraphStyle
    subheading: ParagraphStyle
    table: TableStyle
    table_width: float
    spacer: Spacer
    date_format: str


def get_stylesheet() -> StyleSheet:
    sample_style_sheet = getSampleStyleSheet()
    return StyleSheet(
        title=ParagraphStyle(
            name="Title",
            parent=sample_style_sheet["Normal"],
            fontName=sample_style_sheet["Title"].fontName,
            fontSize=28,
            leading=22,
            spaceAfter=3,
        ),
        heading=sample_style_sheet.get("Heading2"),
        subheading=sample_style_sheet.get("Heading3"),
        table=DEFAULT_TABLE_STYLE,
        table_width=DEFAULT_MAX_TABLE_WIDTH,
        spacer=DEFAULT_SPACER,
        date_format="%Y-%m-%d %H:%M:%S",
    )


def first_page_template(canvas, doc, test_procedure_name: str, test_procedure_instance: str):
    """Template for the first/front/title page of the report"""

    # test_procedure_name = "ALL-01"
    # test_procedure_instance = "https://cactus.cecs.anu.edu.au/asjaskdfjlkasdjf"
    document_creation: str = datetime.now(timezone.utc).strftime("%d-%m-%Y")

    canvas.saveState()

    # Banner
    canvas.setFillColor(HIGHLIGHT_COLOR)
    canvas.rect(0, PAGE_HEIGHT - BANNER_HEIGHT, PAGE_WIDTH, BANNER_HEIGHT, stroke=0, fill=1)

    # Title (Banner)
    canvas.setFillColor(TEXT_COLOR)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(MARGIN, PAGE_HEIGHT - 0.6 * inch, DOCUMENT_TITLE)

    # Logo (Banner)
    size = 40
    # canvas.drawInlineImage(
    #     "/home/mike/Downloads/cactus.png", PAGE_WIDTH - PAGE_WIDTH / 3.0, PAGE_HEIGHT - size, width=size, height=size
    # )
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 0.5 * inch, AUTHOR)
    # canvas.linkURL("https://cactus.cecs.anu.edu.au")
    canvas.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 0.7 * inch, AUTHOR_URL)

    # Footer
    # Footer Banner
    canvas.setFillColor(HIGHLIGHT_COLOR)
    canvas.rect(0, 0, PAGE_WIDTH, 0.4 * inch, stroke=0, fill=1)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 8)
    footer_offset = 0.2 * inch
    # Footer left
    canvas.drawString(MARGIN, footer_offset, test_procedure_instance)
    # Footer mid
    canvas.drawCentredString(PAGE_WIDTH / 2.0, footer_offset, f"{test_procedure_name} Test Procedure Report")
    # Footer right
    canvas.drawRightString(PAGE_WIDTH - MARGIN, footer_offset, f"Page {doc.page}")
    canvas.restoreState()

    # Document "Metadata"
    canvas.setFillColor(TEXT_COLOR)
    canvas.setFont("Helvetica", 6)
    canvas.drawRightString(
        PAGE_WIDTH - MARGIN, PAGE_HEIGHT - BANNER_HEIGHT - 0.2 * inch, f"Created on {document_creation}"
    )
    canvas.drawRightString(
        PAGE_WIDTH - MARGIN, PAGE_HEIGHT - BANNER_HEIGHT - 0.35 * inch, f"by Cactus Runner {__version__}"
    )


def later_pages_template(canvas, doc, test_procedure_name: str, test_procedure_instance: str):
    """Template for subsequent pages"""
    canvas.saveState()
    # Footer
    # Footer Banner
    canvas.setFillColor(HIGHLIGHT_COLOR)
    canvas.rect(0, 0, PAGE_WIDTH, 0.4 * inch, stroke=0, fill=1)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica", 8)
    footer_offset = 0.2 * inch
    # Footer left
    canvas.drawString(MARGIN, footer_offset, test_procedure_instance)
    # Footer mid
    canvas.drawCentredString(PAGE_WIDTH / 2.0, footer_offset, f"{test_procedure_name} Test Procedure Report")
    # Footer right
    canvas.drawRightString(PAGE_WIDTH - MARGIN, footer_offset, f"Page {doc.page}")
    canvas.restoreState()


def generate_title(test_procedure_name: str, style: ParagraphStyle) -> list[Flowable]:
    return [Paragraph(f"{test_procedure_name} Test Procedure Report", style), Spacer(1, 10)]


def generate_overview_section(
    test_procedure_name: str,
    test_procedure_description: str,
    test_procedure_instance: str,
    init_timestamp: datetime,
    start_timestamp: datetime,
    client_lfdi: str,
    duration: timedelta,
    stylesheet: StyleSheet,
) -> list[Flowable]:
    elements = []
    elements.append(Paragraph(test_procedure_name, style=stylesheet.title))
    elements.append(Paragraph(test_procedure_description, style=stylesheet.subheading))
    elements.append(stylesheet.spacer)
    doe_data = [
        [
            "Instance",
            test_procedure_instance,
            "",
            "Initialisation time (UTC)",
            init_timestamp.strftime(stylesheet.date_format),
        ],
        ["Client LFDI", client_lfdi, "", "Start time (UTC)", start_timestamp.strftime(stylesheet.date_format)],
        ["", "", "", "Duration", str(duration).split(".")[0]],  # remove microseconds from output
    ]
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.15, 0.4, 0.05, 0.2, 0.2]]
    table = Table(doe_data, colWidths=column_widths)
    tstyle = TableStyle(
        [
            ("BACKGROUND", (0, 0), (1, 2), OVERVIEW_BACKGROUND),
            ("BACKGROUND", (3, 0), (4, 2), OVERVIEW_BACKGROUND),
            ("TEXTCOLOR", (0, 0), (-1, -1), TABLE_TEXT_COLOR),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (0, 2), "Helvetica-Bold"),
            ("FONTNAME", (3, 0), (3, 2), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (4, 0), 6),
            ("BOTTOMPADDING", (0, 2), (4, 2), 6),
        ]
    )
    table.setStyle(tstyle)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_criteria_section(check_results: dict[str, CheckResult], stylesheet: StyleSheet) -> list[Flowable]:
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
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.33, 0.15, 0.52]]
    table = Table(criteria_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_test_progress_chart() -> Image:
    # gannt/timeline style
    df = pd.DataFrame(
        [
            dict(Stage="Init", Start="2009-01-01", Finish="2009-01-01", Request="/dcap", Method="GET"),
            dict(Stage="Unmatched", Start="2009-03-05", Finish="2009-04-15", Request="/edev", Method="GET"),
            dict(Stage="Unmatched", Start="2009-02-20", Finish="2009-05-30", Request="/edev", Method="GET"),
            dict(Stage="Step 1.", Start="2009-02-20", Finish="2009-05-30", Request="/tm", Method="GET"),
            dict(Stage="Step 2.", Start="2009-02-21", Finish="2009-05-30", Request="/edev/1", Method="GET"),
            dict(Stage="Step 3.", Start="2009-02-22", Finish="2009-05-30", Request="/edev/1/der", Method="GET"),
            dict(Stage="Step 4.", Start="2009-02-23", Finish="2009-05-30", Request="/tm", Method="POST"),
        ]
    )

    fig = px.scatter(
        df,
        x="Start",
        y="Stage",
        color="Request",
        symbol="Method",
        category_orders={"Stage": ["Init", "Unmatched", "Step 1.", "Step 2.", "Step 3.", "Step 4."]},
    )
    fig.update_traces(marker=dict(size=20), selector=dict(mode="markers"))

    img_bytes = fig.to_image(format="png", scale=4)  # Scale up figure so it's high enough resolution
    pil_image = PilImage.open(io.BytesIO(img_bytes))
    buffer = io.BytesIO(img_bytes)
    scale_factor = pil_image.width / DEFAULT_MAX_TABLE_WIDTH  # rescale image to width of page content
    return Image(buffer, width=pil_image.width / scale_factor, height=pil_image.height / scale_factor)


def generate_test_progress_section(stylesheet: StyleSheet) -> list[Flowable]:
    elements = []
    elements.append(Paragraph("Test Progress", stylesheet.heading))
    elements.append(generate_test_progress_chart())
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
) -> list[Flowable]:
    elements = []
    elements.append(Paragraph("Communications", stylesheet.heading))
    if request_timestamps:
        elements.append(generate_requests_timeline(request_timestamps=request_timestamps))
    else:
        elements.append(Paragraph("No requests were received by utility server during the test procedure."))
    elements.append(stylesheet.spacer)
    return elements


def generate_site_der_table(site: Site, stylesheet: StyleSheet) -> list[Flowable]:
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


def generate_site_section(site: Site, stylesheet: StyleSheet) -> list[Flowable]:
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


def generate_devices_section(sites: list[Site], stylesheet: StyleSheet) -> list[Flowable]:
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


def generate_reading_count_table(reading_counts: dict[SiteReadingType, int], stylesheet: StyleSheet) -> list[Flowable]:
    elements = []

    table_data = [
        [reading_type.site_reading_type_id, reading_description(reading_type), count]
        for reading_type, count in reading_counts.items()
    ]
    table_data.insert(0, ["MUP", "Description", "Number received"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.13, 0.63, 0.24]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_readings_section(
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    stylesheet: StyleSheet,
) -> list[Flowable]:
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
) -> ClientInteraction:
    for client_interaction in client_interactions:
        if client_interaction.interaction_type == interaction_type:
            return client_interaction
    raise ValueError(f"No client interactions found with type={interaction_type}")


def generate_page_elements(
    runner_state: RunnerState,
    test_procedure_instance: str,
    check_results: dict[str, CheckResult],
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    sites: list[Site],
    stylesheet: StyleSheet,
) -> list[Flowable]:
    active_test_procedure = runner_state.active_test_procedure
    if active_test_procedure is None:
        raise ValueError("'active_test_procedure' attribute of 'runner_state' cannot be None")

    page_elements = []

    test_procedure_name = active_test_procedure.name
    test_procedure_description = active_test_procedure.definition.description

    # Document Title
    # page_elements.extend(generate_title(test_procedure_name, style=stylesheet.title))
    # The title is handles by the first page banner
    # We need a space to skip past the banner
    page_elements.append(Spacer(1, MARGIN))

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
                test_procedure_description=test_procedure_description,
                test_procedure_instance=test_procedure_instance,
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

    # Test Progress Section
    page_elements.extend(generate_test_progress_section(stylesheet=stylesheet))

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


def generate_page_elements_no_active_procedure(stylesheet: StyleSheet) -> list[Flowable]:
    page_elements = []
    page_elements.append(Paragraph("Test Procedure Report", stylesheet.title))
    page_elements.append(DEFAULT_SPACER)
    page_elements.append(
        Paragraph(
            "NO ACTIVE TEST PROCEDURE", ParagraphStyle("red-title", parent=stylesheet.title, textColor=WARNING_COLOR)
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

    test_procedure_instance = "cactus.cecs.anu.edu.au/0ab24cce-cd1b-4bfc"

    if runner_state.active_test_procedure is not None:
        page_elements = generate_page_elements(
            runner_state=runner_state,
            test_procedure_instance=test_procedure_instance,
            check_results=check_results,
            readings=readings,
            reading_counts=reading_counts,
            sites=sites,
            stylesheet=stylesheet,
        )
    else:
        page_elements = generate_page_elements_no_active_procedure(stylesheet=stylesheet)

    test_procedure_name = runner_state.active_test_procedure.name
    first_page = partial(
        first_page_template, test_procedure_name=test_procedure_name, test_procedure_instance=test_procedure_instance
    )
    later_pages = partial(
        later_pages_template, test_procedure_name=test_procedure_name, test_procedure_instance=test_procedure_instance
    )

    with io.BytesIO() as buffer:
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            title=DOCUMENT_TITLE,
            author=AUTHOR,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN,
        )
        doc.build(page_elements, onFirstPage=first_page, onLaterPages=later_pages)
        pdf_data = buffer.getvalue()

    return pdf_data
