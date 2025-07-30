import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from http import HTTPStatus

import pandas as pd
import PIL.Image as PilImage
import plotly.express as px  # type: ignore
import plotly.graph_objects as go  # type: ignore
from cactus_test_definitions import __version__ as cactus_test_definitions_version
from envoy.server.model import (
    DynamicOperatingEnvelope,
    Site,
    SiteDERAvailability,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
)
from envoy.server.model.site_reading import SiteReadingType
from envoy_schema.server.schema.sep2.types import DataQualifierType, PhaseCode, UomType
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    ParagraphStyle,
    getSampleStyleSheet,
)
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BalancedColumns,
    BaseDocTemplate,
    Flowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cactus_runner import __version__ as cactus_runner_version
from cactus_runner.app import event
from cactus_runner.app.check import CheckResult
from cactus_runner.models import (
    ClientInteraction,
    ClientInteractionType,
    RequestEntry,
    RunnerState,
)

logger = logging.getLogger(__name__)

PAGE_WIDTH, PAGE_HEIGHT = A4
DEFAULT_SPACER = Spacer(1, 0.25 * inch)
MARGIN = 0.5 * inch
BANNER_HEIGHT = inch

HIGHLIGHT_COLOR = HexColor(0x09BB71)  # Teal green used on cactus UI
MUTED_COLOR = HexColor(0xD7FCEF)  # Light mint green
WHITE = HexColor(0xFFFFFF)

TABLE_TEXT_COLOR = HexColor(0x262626)
TABLE_HEADER_TEXT_COLOR = HexColor(0x424242)
TABLE_ROW_COLOR = WHITE
TABLE_ALT_ROW_COLOR = MUTED_COLOR
TABLE_LINE_COLOR = HexColor(0x707070)

OVERVIEW_BACKGROUND = MUTED_COLOR

WARNING_COLOR = HexColor(0xFF4545)
TEXT_COLOR = HexColor(0x000000)
PASS_COLOR = HIGHLIGHT_COLOR
FAIL_COLOR = HexColor(0xF1420E)

DEFAULT_TABLE_STYLE = TableStyle(
    [
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [TABLE_ROW_COLOR, TABLE_ALT_ROW_COLOR]),
        ("TEXTCOLOR", (0, 0), (-1, -1), TABLE_TEXT_COLOR),
        ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT_COLOR),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, TABLE_LINE_COLOR),
        ("LINEBELOW", (0, -1), (-1, -1), 1, TABLE_LINE_COLOR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
)

# Limit document content to full width of page (minus margins)
MAX_CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

# The maximum length of a string that can appear in a single table cell
MAX_CELL_LENGTH_CHARS = 500

DOCUMENT_TITLE = "CSIP-AUS Client Test Procedure"
AUTHOR = "Cactus Test Harness"
AUTHOR_URL = "https://cactus.cecs.anu.edu.au"


def rl_to_plotly_color(reportlab_color: Color) -> str:
    """Converts a reportlab color to plotly color (as hexstring)"""
    return f"#{reportlab_color.hexval()[2:]}"


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
    max_cell_length_chars: int
    truncation_marker: str


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
        heading=sample_style_sheet.get("Heading2"),  # type: ignore
        subheading=sample_style_sheet.get("Heading3"),  # type: ignore
        table=DEFAULT_TABLE_STYLE,
        table_width=MAX_CONTENT_WIDTH,
        spacer=DEFAULT_SPACER,
        date_format="%Y-%m-%d %H:%M:%S",
        max_cell_length_chars=MAX_CELL_LENGTH_CHARS,
        truncation_marker=" … ",
    )


def first_page_template(canvas: Canvas, doc: BaseDocTemplate, test_procedure_name: str, test_run_id: str) -> None:
    """Template for the first/front/title page of the report"""

    document_creation: str = datetime.now(timezone.utc).strftime("%d-%m-%Y")

    canvas.saveState()

    # Banner
    canvas.setFillColor(HIGHLIGHT_COLOR)
    canvas.rect(0, PAGE_HEIGHT - BANNER_HEIGHT, PAGE_WIDTH, BANNER_HEIGHT, stroke=0, fill=1)

    # Title (Banner)
    canvas.setFillColor(TEXT_COLOR)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(MARGIN, PAGE_HEIGHT - 0.6 * inch, DOCUMENT_TITLE)

    # Report author details
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
    canvas.drawString(MARGIN, footer_offset, f"Run ID: {test_run_id}")
    # Footer mid
    canvas.drawCentredString(PAGE_WIDTH / 2.0, footer_offset, f"{test_procedure_name} Test Procedure Report")
    # Footer right
    canvas.drawRightString(PAGE_WIDTH - MARGIN, footer_offset, f"Page {doc.page}")
    canvas.restoreState()

    # Document "Metadata"
    canvas.setFillColor(TEXT_COLOR)
    canvas.setFont("Helvetica", 6)
    canvas.drawRightString(
        PAGE_WIDTH - MARGIN, PAGE_HEIGHT - BANNER_HEIGHT - 0.2 * inch, f"Report created on {document_creation}"
    )
    canvas.drawRightString(
        PAGE_WIDTH - MARGIN,
        PAGE_HEIGHT - BANNER_HEIGHT - 0.35 * inch,
        f"Cactus Test Definitions v{cactus_test_definitions_version}",
    )
    canvas.drawRightString(
        PAGE_WIDTH - MARGIN, PAGE_HEIGHT - BANNER_HEIGHT - 0.5 * inch, f"Cactus Runner v{cactus_runner_version}"
    )


def later_pages_template(canvas: Canvas, doc: BaseDocTemplate, test_procedure_name: str, test_run_id: str) -> None:
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
    canvas.drawString(MARGIN, footer_offset, f"Run ID: {test_run_id}")
    # Footer mid
    canvas.drawCentredString(PAGE_WIDTH / 2.0, footer_offset, f"{test_procedure_name} Test Procedure Report")
    # Footer right
    canvas.drawRightString(PAGE_WIDTH - MARGIN, footer_offset, f"Page {doc.page}")
    canvas.restoreState()


def fig_to_image(fig: go.Figure, content_width: float) -> Image:
    UPSCALE_FACTOR = 4
    img_bytes = fig.to_image(format="png", scale=UPSCALE_FACTOR)  # Scale up figure so it's high enough resolution
    pil_image = PilImage.open(io.BytesIO(img_bytes))
    buffer = io.BytesIO(img_bytes)
    scale_factor = pil_image.width / content_width  # rescale image to width of page content
    return Image(buffer, width=pil_image.width / scale_factor, height=pil_image.height / scale_factor)


def generate_overview_section(
    test_procedure_name: str,
    test_procedure_description: str,
    test_run_id: str,
    init_timestamp: datetime,
    start_timestamp: datetime,
    client_cert_type: str,
    client_lfdi: str,
    duration: timedelta,
    stylesheet: StyleSheet,
) -> list[Flowable]:
    elements: list[Flowable] = []
    elements.append(Paragraph(test_procedure_name, style=stylesheet.title))
    elements.append(Paragraph(test_procedure_description, style=stylesheet.subheading))
    elements.append(stylesheet.spacer)

    doe_data = [
        [
            "Run ID",
            test_run_id,
            "",
            "Initialisation time (UTC)",
            init_timestamp.strftime(stylesheet.date_format),
        ],
        [
            f"{client_cert_type} LFDI",
            client_lfdi,
            "",
            "Start time (UTC)",
            start_timestamp.strftime(stylesheet.date_format),
        ],
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


def generate_criteria_summary_chart(num_passed: int, num_failed: int) -> Image:
    labels = ["Pass", "Fail"]
    values = [num_passed, num_failed]
    total = num_passed + num_failed

    # Create pie chart
    pie = go.Pie(
        labels=labels,
        values=values,
        hole=0.6,  # Adds a hole to centre of pie chart (for annotation)
        textinfo="none",  # Hide the % labels on each segment
    )

    # If not all passed or all failed
    if num_passed > 1 and num_failed > 1:
        # Adds separators between pie segments
        pie.marker.line.width = 5
        pie.marker.line.color = "white"

    # Create a figure from the pie chart
    fig = go.Figure(data=[pie])

    # Remove all margins and padding to make chart as small as possible
    fig.update_layout(showlegend=False, margin=dict(l=0, r=0, b=0, t=0, pad=0))

    # Add summary annotation to middle of pie doughnut
    annotation = "<b>All</b><br>passed" if num_passed == total else f"<b>{num_passed}</b> / <b>{total}</b><br>passed"
    fig.add_annotation(
        x=0.5,
        y=0.5,
        text=annotation,
        font=dict(size=40),
        showarrow=False,
    )

    # Set the colors of the segments
    fig.update_traces(marker=dict(colors=[rl_to_plotly_color(PASS_COLOR), rl_to_plotly_color(FAIL_COLOR)]))

    # Generate the image from the fig
    content_width = MAX_CONTENT_WIDTH / 2.5  # rescale image to width of KeepTogether column (roughly)
    return fig_to_image(fig=fig, content_width=content_width)


def generate_criteria_summary_table(check_results: dict[str, CheckResult], stylesheet: StyleSheet) -> Table:
    table_style = TableStyle(
        [
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [TABLE_ROW_COLOR, TABLE_ALT_ROW_COLOR]),
            ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT_COLOR),
            ("LINEBELOW", (0, 0), (-1, 0), 1, TABLE_LINE_COLOR),
            ("LINEBELOW", (0, -1), (-1, -1), 1, TABLE_LINE_COLOR),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ]
    )

    # Generate table data
    criteria_data = [
        [
            index + 1,
            check_name,
            "Pass" if check_results[check_name].passed else "Fail",
            # Paragraph("" if check_result.description is None else check_result.description),
        ]
        for index, check_name in enumerate(check_results)
    ]

    # Add table header
    criteria_data.insert(0, ["", "", "Pass/Fail"])

    # Set the colors of the pass/fail column
    for index, check_name in enumerate(check_results):
        row = index + 1  # +1 to account for header row
        if check_results[check_name].passed:
            table_style.add("TEXTCOLOR", (2, row), (2, row), PASS_COLOR)
        else:
            table_style.add("TEXTCOLOR", (2, row), (2, row), FAIL_COLOR)

    # Create the table
    column_widths = [int(fraction * stylesheet.table_width * 0.46) for fraction in [0.1, 0.7, 0.2]]
    table = Table(criteria_data, colWidths=column_widths, hAlign="RIGHT")
    table.setStyle(table_style)

    return table


def generate_criteria_failure_table(check_results: dict[str, CheckResult], stylesheet: StyleSheet) -> Table:
    criteria_explanation_data = [
        [
            index + 1,
            name,
            Paragraph(
                "" if check_results[name].description is None else check_results[name].description  # type: ignore
            ),
        ]
        for index, name in enumerate(check_results)
        if not check_results[name].passed
    ]

    criteria_explanation_data.insert(0, ["", "", "Explanation of Failure"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.05, 0.35, 0.6]]
    table = Table(criteria_explanation_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    return table


def generate_criteria_section(check_results: dict[str, CheckResult], stylesheet: StyleSheet) -> list[Flowable]:
    check_values = [check_result.passed for check_result in check_results.values()]
    num_passed = sum(check_values)
    num_failed = len(check_values) - num_passed

    elements: list[Flowable] = []
    elements.append(Paragraph("Criteria", stylesheet.heading))
    chart = generate_criteria_summary_chart(num_passed=num_passed, num_failed=num_failed)
    table = generate_criteria_summary_table(check_results=check_results, stylesheet=stylesheet)
    elements.append(BalancedColumns([chart, table]))

    # Criteria Failure Table (only shown if there are failures present)
    if num_failed > 0:
        elements.append(stylesheet.spacer)
        elements.append(stylesheet.spacer)
        elements.append(generate_criteria_failure_table(check_results=check_results, stylesheet=stylesheet))
    elements.append(stylesheet.spacer)
    return elements


def generate_test_progress_chart(runner_state: RunnerState, time_relative_to_test_start: bool = True) -> Image:
    base_timestamp = runner_state.interaction_timestamp(interaction_type=ClientInteractionType.TEST_PROCEDURE_START)
    alternative_x_axis_label = "Time relative to start of test (s)"

    x_axis_label = "Time (UTC)"

    requests = []
    for request_entry in runner_state.request_history:

        if time_relative_to_test_start and base_timestamp is not None:
            # Timedeltas (timestamp - base_timestamp) are represented strangely by plotly
            # For example it displays 0, 5B, 10B to mean 0, 5 and 10 seconds.
            # Here convert the timedeltas to total seconds to avoid this problem.
            total_seconds = (request_entry.timestamp - base_timestamp).total_seconds()
            x_axis_label = alternative_x_axis_label

            v = dict(
                Stage=request_entry.step_name,
                Time=total_seconds,
                Request=request_entry.path,
                Method=str(request_entry.method),
            )
        else:
            timestamp = request_entry.timestamp
            v = dict(
                Stage=request_entry.step_name,
                Time=timestamp,
                Request=request_entry.path,
                Method=str(request_entry.method),
            )

        requests.append(v)
    df = pd.DataFrame(requests)

    all_stage_names = [
        event.INIT_STAGE_STEP_NAME,
        event.UNMATCHED_STEP_NAME,
        *runner_state.active_test_procedure.definition.steps.keys(),  # type: ignore
    ]

    fig = px.scatter(
        df,
        x="Time",
        y="Stage",
        range_y=[-1, len(all_stage_names)],  # Force showing all stages on y-axis
        color="Request",
        symbol="Method",
        category_orders={"Stage": all_stage_names},  # Make y-axis a category axis
        labels={"Stage": "Stage/Step", "Time": x_axis_label},
    )

    # Show large markers for each request.
    # The marker color is determined by the request e.g. /dcap
    # The marker shape is determiend by the method e.g. GET, POST etc.
    # fig.update_traces(marker=dict(size=20), selector=dict(mode="markers"))
    fig.update_traces(marker=dict(size=10), selector=dict(mode="markers"))

    # Hide the background and grid lines
    fig.update_layout(paper_bgcolor="#fff", plot_bgcolor="#fff")
    fig.update_yaxes(showgrid=False)
    fig.update_xaxes(showgrid=False)

    # Style the legend
    fig.update_layout(
        legend_title_text=None,
        legend=dict(entrywidth=120, itemsizing="constant", orientation="h", xanchor="center", x=0.5, y=-0.15),
    )

    # Add horizontal bands delineating each stage
    init_and_match_color = "#e0e0e0"
    step_color = rl_to_plotly_color(MUTED_COLOR)
    stage_band_colors = [*[step_color] * (len(all_stage_names) - 2), *[init_and_match_color] * 2]
    for index, color in enumerate(stage_band_colors):
        fig.add_hrect(
            y0=index - 0.4,
            y1=index + 0.4,
            line_width=0,
            fillcolor=color,
            opacity=0.5,
            layer="below",
        )

    return fig_to_image(fig=fig, content_width=MAX_CONTENT_WIDTH)


def generate_test_progress_section(runner_state: RunnerState, stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []
    elements.append(Paragraph("Test Progress", stylesheet.heading))
    elements[-1].keepWithNext = True
    if runner_state.request_history:
        elements.append(generate_test_progress_chart(runner_state=runner_state))
    else:
        elements.append(Paragraph("No requests were received by utility server during the test procedure."))
    elements.append(stylesheet.spacer)
    return elements


def generate_requests_histogram(request_timestamps: list[datetime] | list[float], x_axis_label: str) -> Image:
    df = pd.DataFrame({"timestamp": request_timestamps})
    fig = px.histogram(
        df,
        x="timestamp",
        labels={"timestamp": x_axis_label},
        color_discrete_sequence=[rl_to_plotly_color(HIGHLIGHT_COLOR)],
    )
    fig.update_layout(bargap=0.2)
    fig.update_layout(yaxis_title="Number of requests")
    return fig_to_image(fig=fig, content_width=MAX_CONTENT_WIDTH)


def get_request_timestamps(
    runner_state: RunnerState, time_relative_to_test_start: bool
) -> tuple[list[datetime] | list[float], str]:
    request_timestamps: list[datetime] = [request_entry.timestamp for request_entry in runner_state.request_history]
    base_timestamp = runner_state.interaction_timestamp(interaction_type=ClientInteractionType.TEST_PROCEDURE_START)
    timestamps: list[datetime] | list[float]

    if time_relative_to_test_start and base_timestamp is not None:
        # Timedeltas (timestamp - base_timestamp) are represented strangely by plotly
        # For example it displays 0, 5B, 10B to mean 0, 5 and 10 seconds.
        # Here convert the timedeltas to total seconds to avoid this problem.
        timestamps = [(timestamp - base_timestamp).total_seconds() for timestamp in request_timestamps]
        description = "Time relative to start of test (s)"
    else:
        timestamps = request_timestamps
        description = "Time (UTC)"

    return timestamps, description


def get_requests_with_errors(runner_state: RunnerState) -> dict[int, RequestEntry]:
    return {
        index: request_entry
        for index, request_entry in enumerate(runner_state.request_history)
        if request_entry.status >= HTTPStatus(400)
    }


def get_requests_with_validation_errors(runner_state: RunnerState) -> dict[int, RequestEntry]:
    return {
        index: request_entry
        for index, request_entry in enumerate(runner_state.request_history)
        if len(request_entry.body_xml_errors) > 0
    }


def generate_requests_with_errors_table(requests_with_errors: dict[int, RequestEntry], stylesheet: StyleSheet) -> Table:
    data = [
        [
            i,
            req.timestamp,
            f"{str(req.method)} {req.path}",
            f"{req.status.name.replace("_", " ").title()} ({req.status.value})",
        ]
        for i, req in requests_with_errors.items()
    ]

    data.insert(0, ["#", "Time (UTC)", "Request", "Error Status"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.1, 0.45, 0.2, 0.25]]
    table = Table(data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    return table


def generate_requests_with_validation_errors_table(
    requests_with_validation_errors: dict[int, RequestEntry], stylesheet: StyleSheet
) -> Table:
    data = []
    for i, req in requests_with_validation_errors.items():
        request_description = f"{str(req.method)} {req.path} {req.status}"
        validation_errors = "\n".join(req.body_xml_errors)

        # Limit to a reason size the validation error information
        if len(validation_errors) > stylesheet.max_cell_length_chars:
            validation_errors = validation_errors[: stylesheet.max_cell_length_chars] + stylesheet.truncation_marker

        data.append(
            [
                i,
                request_description,
                Paragraph(validation_errors),
            ]
        )

    data.insert(0, ["", "", "Validation Errors"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.2, 0.2, 0.6]]
    table = Table(data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    return table


def generate_communications_section(
    runner_state: RunnerState, stylesheet: StyleSheet, time_relative_to_test_start: bool = True
) -> list[Flowable]:
    have_requests = len(runner_state.request_history) > 0

    elements: list[Flowable] = []
    elements.append(Paragraph("Communications", stylesheet.heading))
    elements[-1].keepWithNext = True
    if have_requests:
        timestamps, description = get_request_timestamps(
            runner_state=runner_state, time_relative_to_test_start=time_relative_to_test_start
        )
        elements.append(generate_requests_histogram(request_timestamps=timestamps, x_axis_label=description))

        requests_with_errors = get_requests_with_errors(runner_state=runner_state)
        if requests_with_errors:
            elements.append(stylesheet.spacer)
            elements.append(Paragraph("Requests with errors", stylesheet.subheading))
            elements.append(
                generate_requests_with_errors_table(requests_with_errors=requests_with_errors, stylesheet=stylesheet)
            )

        requests_with_validation_errors = get_requests_with_validation_errors(runner_state=runner_state)
        if requests_with_validation_errors:
            elements.append(stylesheet.spacer)
            elements.append(
                generate_requests_with_validation_errors_table(
                    requests_with_validation_errors=requests_with_validation_errors, stylesheet=stylesheet
                )
            )
    else:
        elements.append(Paragraph("No requests were received by utility server during the test procedure."))
    elements.append(stylesheet.spacer)
    return elements


def generate_der_table_data(obj: object, attributes_to_include: list[str]) -> list:
    def attribute_short_form(attribute: str) -> str:
        suffix = "_value"
        if attribute.endswith(suffix):
            return attribute.removesuffix(suffix)
        return attribute

    def attribute_value(obj: object, attribute: str):
        multiplier_suffix = "_multiplier"
        multiplier_attribute = attribute_short_form(attribute) + multiplier_suffix
        if hasattr(obj, multiplier_attribute):
            return Paragraph(f"{getattr(obj, attribute)} x 10<super>{getattr(obj, multiplier_attribute)}</super>")
        return f"{getattr(obj, attribute)}"

    table_data = [
        [attribute_short_form(attribute), attribute_value(obj, attribute)] for attribute in attributes_to_include
    ]
    return table_data


def generate_site_der_rating_table(site_der_rating: SiteDERRating, stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []
    attributes_to_include = [
        "created_time",
        "changed_time",
        "modes_supported",
        "abnormal_category",
        "max_a_value",
        "max_ah_value",
        "max_charge_rate_va_value",
        "max_charge_rate_w_value",
        "max_discharge_rate_va_value",
        "max_discharge_rate_w_value",
        "max_v_value",
        "max_va_value",
        "max_var_value",
        "max_var_neg_value",
        "max_w_value",
        "max_wh_value",
        "min_pf_over_excited_displacement",
        "min_pf_under_excited_displacement",
        "min_v_value",
        "normal_category",
        "over_excited_pf_displacement",
        "over_excited_w_value",
        "reactive_susceptance_value",
        "under_excited_pf_displacement",
        "under_excited_w_value",
        "v_nom_value",
        "der_type",
        "doe_modes_supported",
    ]
    table_data = generate_der_table_data(site_der_rating, attributes_to_include)
    table_data.insert(0, ["DER Rating", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_site_der_setting_table(site_der_setting: SiteDERSetting, stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []
    attributes_to_include = [
        "created_time",
        "changed_time",
        "modes_enabled",
        "es_delay",
        "es_high_freq",
        "es_high_volt",
        "es_low_freq",
        "es_low_volt",
        "es_ramp_tms",
        "es_random_delay",
        "grad_w",
        "max_a_value",
        "max_ah_value",
        "max_charge_rate_va_value",
        "max_charge_rate_w_value",
        "max_discharge_rate_va_value",
        "max_discharge_rate_w_value",
        "max_v_value",
        "max_va_value",
        "max_var_value",
        "max_var_neg_value",
        "max_w_value",
        "max_wh_value",
        "min_pf_over_excited_displacement",
        "min_pf_under_excited_displacement",
        "min_v_value",
        "soft_grad_w",
        "v_nom_value",
        "v_ref_value",
        "v_ref_ofs_value",
        "doe_modes_enabled",
    ]
    table_data = generate_der_table_data(site_der_setting, attributes_to_include)
    table_data.insert(0, ["DER Setting", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_site_der_availability_table(
    site_der_availability: SiteDERAvailability, stylesheet: StyleSheet
) -> list[Flowable]:
    elements: list[Flowable] = []
    attributes_to_include = [
        "created_time",
        "changed_time",
        "availability_duration_sec",
        "max_charge_duration_sec",
        "reserved_charge_percent",
        "reserved_deliver_percent",
        "estimated_var_avail_value",
        "estimated_w_avail_value",
    ]
    table_data = generate_der_table_data(site_der_availability, attributes_to_include)
    table_data.insert(0, ["DER Availability", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_site_der_status_table(site_der_status: SiteDERStatus, stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []
    attributes_to_include = [
        "created_time",
        "changed_time",
        "alarm_status",
        "generator_connect_status",
        "generator_connect_status_time",
        "inverter_status",
        "inverter_status_time",
        "local_control_mode_status",
        "local_control_mode_status_time",
        "manufacturer_status",
        "manufacturer_status_time",
        "operational_mode_status",
        "operational_mode_status_time",
        "state_of_charge_status",
        "state_of_charge_status_time",
        "storage_mode_status",
        "storage_mode_status_time",
        "storage_connect_status",
        "storage_connect_status_time",
    ]
    table_data = generate_der_table_data(site_der_status, attributes_to_include)
    table_data.insert(0, ["DER Status", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_site_section(site: Site, stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []

    if site.nmi:
        section_title = f"EndDevice {site.site_id} (nmi: {site.nmi})"
        site_description = f"Created at {site.created_time.strftime(stylesheet.date_format)}"
    else:
        section_title = f"EndDevice {site.site_id}"
        site_description = "Generated as part of test procedure precondition."
    elements.append(Paragraph(section_title, stylesheet.subheading))
    elements.append(Paragraph(site_description))
    elements.append(stylesheet.spacer)
    if site.site_ders:
        site_der = site.site_ders[0]
        if site_der.site_der_rating is not None:
            elements.extend(
                generate_site_der_rating_table(site_der_rating=site_der.site_der_rating, stylesheet=stylesheet)
            )
            elements.append(stylesheet.spacer)
        if site_der.site_der_setting is not None:
            elements.extend(
                generate_site_der_setting_table(site_der_setting=site_der.site_der_setting, stylesheet=stylesheet)
            )
            elements.append(stylesheet.spacer)
        if site_der.site_der_availability is not None:
            elements.extend(
                generate_site_der_availability_table(
                    site_der_availability=site_der.site_der_availability, stylesheet=stylesheet
                )
            )
            elements.append(stylesheet.spacer)
        if site_der.site_der_status is not None:
            elements.extend(
                generate_site_der_status_table(site_der_status=site_der.site_der_status, stylesheet=stylesheet)
            )
    else:
        elements.append(Paragraph("No Site DER registered for this site."))
    return elements


def generate_devices_section(sites: list[Site], stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []
    elements.append(Paragraph("Devices", stylesheet.heading))
    elements[-1].keepWithNext = True
    if sites:
        for site in sites:
            elements.extend(generate_site_section(site=site, stylesheet=stylesheet))
    else:
        elements.append(Paragraph("No devices registered either out-of-band or in-band during this test procedure."))
    elements.append(stylesheet.spacer)
    return elements


def generate_controls_chart(controls: list[DynamicOperatingEnvelope]) -> Image:
    data = [
        dict(Control=f"{i}", Start=control.start_time, Finish=control.end_time)
        for i, control in enumerate(controls, start=1)
    ]
    df = pd.DataFrame(data)
    fig = px.timeline(df, x_start="Start", x_end="Finish", y="Control")
    fig.update_yaxes(autorange="reversed")
    return fig_to_image(fig=fig, content_width=MAX_CONTENT_WIDTH)


def generate_controls_table(controls: list[DynamicOperatingEnvelope], stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []

    table_data = [
        [
            f"{i}",
            control.start_time,
            control.duration_seconds,
            "-" if control.import_limit_active_watts is None else control.import_limit_active_watts,
            "-" if control.export_limit_watts is None else control.export_limit_watts,
            "-" if control.load_limit_active_watts is None else control.load_limit_active_watts,
        ]
        for i, control in enumerate(controls, start=1)
    ]
    table_data.insert(0, ["", "Start (UTC)", "Duration (s)", "Import Limit (W)", "Export Limit (W)", "Load Limit (W)"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.06, 0.35, 0.11, 0.16, 0.16, 0.16]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_controls_section(controls: list[DynamicOperatingEnvelope], stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []
    elements.append(Paragraph("Controls", stylesheet.heading))
    elements[-1].keepWithNext = True
    if controls:
        elements.append(generate_controls_chart(controls=controls))
        elements.append(stylesheet.spacer)
        elements.extend(generate_controls_table(controls=controls, stylesheet=stylesheet))
    else:
        elements.append(Paragraph("No controls active during this test procedure."))
    elements.append(stylesheet.spacer)
    return elements


def generate_readings_timeline(
    readings_df: pd.DataFrame, quantity: str, runner_state: RunnerState, time_relative_to_test_start: bool = True
) -> Image:
    x_axis_column = "time_period_start"
    x_axis_label = "Time (UTC)"

    base_timestamp = runner_state.interaction_timestamp(interaction_type=ClientInteractionType.TEST_PROCEDURE_START)
    alternative_x_axis_label = "Time relative to test start (s)"
    if time_relative_to_test_start and base_timestamp is not None:
        new_x_axis_column = "timedelta_from_start"
        readings_df[new_x_axis_column] = readings_df[x_axis_column] - base_timestamp
        x_axis_column = new_x_axis_column
        x_axis_label = alternative_x_axis_label

    fig = px.line(
        readings_df,
        x=x_axis_column,
        y="scaled_value",
        markers=True,
        color_discrete_sequence=[rl_to_plotly_color(HIGHLIGHT_COLOR)],
    )

    fig.update_layout(
        xaxis=dict(title=dict(text=x_axis_label)),
        yaxis=dict(title=dict(text=quantity)),
    )

    return fig_to_image(fig=fig, content_width=MAX_CONTENT_WIDTH)


def reading_quantity(srt: SiteReadingType) -> str:
    quantity = UomType(srt.uom).name
    quantity = quantity.replace("_", " ").title()
    return quantity


def reading_description(srt: SiteReadingType, exclude_mup: bool = False) -> str:
    mup = srt.site_reading_type_id
    quantity = reading_quantity(srt)
    qualifier = DataQualifierType(srt.data_qualifier).name
    qualifier = qualifier.replace("_", " ").title()
    mup_text = "" if exclude_mup else f"/mup/{mup}:"
    if srt.phase == 0:
        description = f"{mup_text} {quantity} ({qualifier})"
    else:
        phase = PhaseCode(srt.phase).name
        phase = phase.replace("_", " ").title()
        description = f"{mup_text} {quantity} ({qualifier}, {phase})"

    return description


def generate_reading_count_table(reading_counts: dict[SiteReadingType, int], stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []

    table_data = [
        [reading_type.site_reading_type_id, reading_description(reading_type, exclude_mup=True), count]
        for reading_type, count in reading_counts.items()
    ]
    table_data.insert(0, ["/mup", "Description", "Number received"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.13, 0.63, 0.24]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(stylesheet.spacer)
    return elements


def generate_readings_section(
    runner_state: RunnerState,
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    stylesheet: StyleSheet,
) -> list[Flowable]:

    elements: list[Flowable] = []
    elements.append(Paragraph("Readings", stylesheet.heading))
    elements[-1].keepWithNext = True

    # Add table to show how many of each reading type was sent to the utility server (all reading types)
    if reading_counts:
        elements.append(stylesheet.spacer)
        elements.extend(generate_reading_count_table(reading_counts=reading_counts, stylesheet=stylesheet))

        # Add charts for each of the different reading types
        if readings:
            for reading_type, readings_df in readings.items():
                elements.append(Paragraph(reading_description(reading_type), style=stylesheet.subheading))
                elements[-1].keepWithNext = True
                elements.append(
                    generate_readings_timeline(
                        readings_df=readings_df, quantity=reading_quantity(reading_type), runner_state=runner_state
                    )
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
    test_run_id: str,
    check_results: dict[str, CheckResult],
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    sites: list[Site],
    controls: list[DynamicOperatingEnvelope],
    stylesheet: StyleSheet,
) -> list[Flowable]:
    active_test_procedure = runner_state.active_test_procedure
    if active_test_procedure is None:
        raise ValueError("'active_test_procedure' attribute of 'runner_state' cannot be None")

    page_elements: list[Flowable] = []

    test_procedure_name = active_test_procedure.name
    test_procedure_description = active_test_procedure.definition.description

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
                test_run_id=test_run_id,
                init_timestamp=init_timestamp,
                start_timestamp=start_timestamp,
                client_lfdi=active_test_procedure.client_lfdi,
                client_cert_type=active_test_procedure.client_certificate_type,
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
    page_elements.extend(generate_test_progress_section(runner_state=runner_state, stylesheet=stylesheet))

    # Communications Section
    page_elements.extend(generate_communications_section(runner_state=runner_state, stylesheet=stylesheet))

    # Devices Section
    page_elements.extend(generate_devices_section(sites=sites, stylesheet=stylesheet))

    # Controls Section
    page_elements.extend(generate_controls_section(controls=controls, stylesheet=stylesheet))

    # Readings Section
    page_elements.extend(
        generate_readings_section(
            runner_state=runner_state, readings=readings, reading_counts=reading_counts, stylesheet=stylesheet
        )
    )

    return page_elements


def pdf_report_as_bytes(
    runner_state: RunnerState,
    check_results: dict[str, CheckResult],
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    sites: list[Site],
    controls: list[DynamicOperatingEnvelope],
) -> bytes:
    stylesheet = get_stylesheet()

    if runner_state.active_test_procedure is None:
        raise ValueError("Unable to generate report - no active test procedure")

    run_id = runner_state.active_test_procedure.run_id
    test_run_id = "UNKNOWN" if run_id is None else run_id

    page_elements = generate_page_elements(
        runner_state=runner_state,
        test_run_id=test_run_id,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        controls=controls,
        stylesheet=stylesheet,
    )

    test_procedure_name = runner_state.active_test_procedure.name
    first_page = partial(first_page_template, test_procedure_name=test_procedure_name, test_run_id=test_run_id)
    later_pages = partial(later_pages_template, test_procedure_name=test_procedure_name, test_run_id=test_run_id)

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
