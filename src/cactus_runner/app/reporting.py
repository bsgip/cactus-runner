import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from http import HTTPStatus
from typing import Sequence

import pandas as pd
import PIL.Image as PilImage
import plotly.express as px  # type: ignore
import plotly.graph_objects as go  # type: ignore
from cactus_test_definitions import CSIPAusVersion
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
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    DeviceCategory,
    KindType,
    PhaseCode,
    RoleFlagsType,
    UomType,
)
from reportlab.lib import colors
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
    KeepTogether,
    NullDraw,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cactus_runner import __version__ as cactus_runner_version
from cactus_runner.app.check import CheckResult
from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.app.timeline import Timeline, duration_to_label
from cactus_runner.models import (
    ClientCertificateType,
    ClientInteraction,
    ClientInteractionType,
    RequestEntry,
    RunnerState,
    StepStatus,
)

logger = logging.getLogger(__name__)

WITNESS_TEST_CLASSES: list[str] = ["DER-A", "DER-G", "DER-L"]  # Classes from section 14 of sa-ts-5573-2025

CHART_MARGINS = dict(l=80, r=20, t=40, b=80)


class ConditionalSpacer(Spacer):
    """A Spacer that takes up a variable amount of vertical space.

    It takes up the avilable space, up to but not exceeding
    the requested height of the spacer.
    """

    def wrap(self, aW, aH):
        height = min(self.height, aH - 1e-8)
        return (aW, height)


PAGE_WIDTH, PAGE_HEIGHT = A4
DEFAULT_SPACER = ConditionalSpacer(1, 0.25 * inch)
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
GENTLE_WARNING_COLOR = HexColor(0xFFC107)

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
    spacer: Spacer | NullDraw
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


def first_page_template(
    canvas: Canvas, doc: BaseDocTemplate, test_procedure_name: str, test_run_id: str, csip_aus_version: CSIPAusVersion
) -> None:
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
    canvas.drawRightString(
        PAGE_WIDTH - MARGIN, PAGE_HEIGHT - BANNER_HEIGHT - 0.65 * inch, f"CSIP Aus {csip_aus_version}"
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
    run_group_name: str,
    init_timestamp: datetime,
    start_timestamp: datetime,
    client_cert_type: ClientCertificateType,
    client_lfdi: str,
    client_pen: int,
    duration: timedelta,
    stylesheet: StyleSheet,
    playlist_info: str | None = None,
) -> list[Flowable]:
    elements: list[Flowable] = []
    elements.append(Paragraph(test_procedure_name, style=stylesheet.title))
    elements.append(Paragraph(test_procedure_description, style=stylesheet.subheading))
    elements.append(stylesheet.spacer)

    overview_data = [
        [
            "Run ID",
            test_run_id,
            "",
            "Initialisation time (UTC)",
            init_timestamp.strftime(stylesheet.date_format),
        ],
        [
            "Run Group",
            run_group_name,
            "",
            "Start time (UTC)",
            start_timestamp.strftime(stylesheet.date_format),
        ],
        [
            f"{client_cert_type} LFDI",
            client_lfdi,
            "",
            "Duration",
            str(duration).split(".")[0],
        ],  # remove microseconds from output
        [
            "PEN",
            str(client_pen) if client_pen else "Not supplied",
            "",
            "Playlist" if playlist_info else "",
            playlist_info or "",
        ],
    ]
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.15, 0.4, 0.05, 0.2, 0.2]]
    table = Table(overview_data, colWidths=column_widths)
    tstyle = TableStyle(
        [
            ("BACKGROUND", (0, 0), (1, -1), OVERVIEW_BACKGROUND),
            ("BACKGROUND", (3, 0), (4, -1), OVERVIEW_BACKGROUND),
            ("TEXTCOLOR", (0, 0), (-1, -1), TABLE_TEXT_COLOR),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (3, 0), (3, -1), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (4, 0), 6),
            ("BOTTOMPADDING", (0, -1), (4, -1), 6),
        ]
    )
    table.setStyle(tstyle)
    elements.append(table)
    if client_cert_type is ClientCertificateType.DEVICE:
        elements.append(
            Paragraph(
                "The device LFDI is the LFDI encoded in the certificate used for authentication, not be confused with the LFDI of any devices registered during this test procedure.",  # noqa 501
                style=ParagraphStyle(name="TableNote", fontSize=6),
            )
        )
    elements.append(stylesheet.spacer)
    return elements


def generate_criteria_summary_chart(num_passed: int, num_failed: int, requires_witness_testing: bool) -> Image:
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
    if num_passed == total:
        annotation = "<b>All</b><br>passed" + ("*" if requires_witness_testing else "")
    else:
        annotation = f"<b>{num_passed}</b> / <b>{total}</b><br>passed"

    fig.add_annotation(
        x=0.5,
        y=0.5,
        text=annotation,
        font=dict(size=40),
        showarrow=False,
    )

    # Set the colors of the segments
    if num_failed == 0 and requires_witness_testing:
        colors = [rl_to_plotly_color(GENTLE_WARNING_COLOR), rl_to_plotly_color(FAIL_COLOR)]
    else:
        colors = [rl_to_plotly_color(PASS_COLOR), rl_to_plotly_color(FAIL_COLOR)]

    fig.update_traces(marker=dict(colors=colors))

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


def generate_criteria_section(
    check_results: dict[str, CheckResult], requires_witness_testing: bool, stylesheet: StyleSheet
) -> list[Flowable]:
    check_values = [check_result.passed for check_result in check_results.values()]
    num_passed = sum(check_values)
    num_failed = len(check_values) - num_passed

    elements: list[Flowable] = []
    elements.append(Paragraph("Criteria", stylesheet.heading))
    chart = generate_criteria_summary_chart(
        num_passed=num_passed, num_failed=num_failed, requires_witness_testing=requires_witness_testing
    )
    table = generate_criteria_summary_table(check_results=check_results, stylesheet=stylesheet)
    elements.append(BalancedColumns([chart, table]))
    elements.append(stylesheet.spacer)

    if num_failed == 0 and requires_witness_testing:
        elements.append(
            Paragraph(
                "<b>*Self tests have passed. "
                "<font backColor='#ffe69b'>However, a witness test operator will need to review these results.</font> "
                "</b>"
            )
        )

    # Criteria Failure Table (only shown if there are failures present)
    if num_failed > 0:
        elements.append(stylesheet.spacer)
        elements.append(generate_criteria_failure_table(check_results=check_results, stylesheet=stylesheet))
    elements.append(stylesheet.spacer)
    return elements


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
            req.timestamp.strftime("%Y-%m-%d %H:%M"),
            f"{req.method} {req.path}",
            f"{req.status.name.replace('_', ' ').title()} ({req.status.value})",
        ]
        for i, req in requests_with_errors.items()
    ]

    data.insert(0, ["#", "Time (UTC)", "Request", "Error Status"])

    column_widths = [
        int(0.07 * stylesheet.table_width),  # #
        int(0.28 * stylesheet.table_width),  # Time
        int(0.55 * stylesheet.table_width),  # Request
        int(0.20 * stylesheet.table_width),  # Error Status
    ]

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

        # Limit to a reasonable size the validation error information
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


def get_non_null_attributes(obj: object, attributes_to_include: list[str]) -> list[str]:
    return [attribute for attribute in attributes_to_include if getattr(obj, attribute) is not None]


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


def make_null_attributes_paragraph(attributes_to_include: list[str], non_null_attributes: list[str]) -> Paragraph:
    null_attributes = [attr.strip() for attr in attributes_to_include if attr not in non_null_attributes]

    paragraph = Paragraph(
        f"Optional elements which have not been set: {', '.join(null_attributes)}",
        style=ParagraphStyle(
            name="TableFootNote",
            fontSize=6,
            leading=6,  # keeps line spacing tight (single spaced)
        ),
    )
    return paragraph


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
    non_null_attributes = get_non_null_attributes(site_der_rating, attributes_to_include)
    null_attributes_paragraph = make_null_attributes_paragraph(attributes_to_include, non_null_attributes)
    table_data = generate_der_table_data(site_der_rating, non_null_attributes)
    table_data.insert(0, ["DER Rating", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(null_attributes_paragraph)
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
    non_null_attributes = get_non_null_attributes(site_der_setting, attributes_to_include)
    null_attributes_paragraph = make_null_attributes_paragraph(attributes_to_include, non_null_attributes)
    table_data = generate_der_table_data(site_der_setting, non_null_attributes)
    table_data.insert(0, ["DER Setting", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(null_attributes_paragraph)
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
    non_null_attributes = get_non_null_attributes(site_der_availability, attributes_to_include)
    null_attributes_paragraph = make_null_attributes_paragraph(attributes_to_include, non_null_attributes)
    table_data = generate_der_table_data(site_der_availability, non_null_attributes)
    table_data.insert(0, ["DER Availability", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(null_attributes_paragraph)
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
    non_null_attributes = get_non_null_attributes(site_der_status, attributes_to_include)
    null_attributes_paragraph = make_null_attributes_paragraph(attributes_to_include, non_null_attributes)
    table_data = generate_der_table_data(site_der_status, non_null_attributes)
    table_data.insert(0, ["DER Status", "Value"])
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.5, 0.5]]
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    elements.append(table)
    elements.append(null_attributes_paragraph)
    elements.append(stylesheet.spacer)
    return elements


def device_category_to_string(device_category: DeviceCategory) -> str:
    if device_category == 0:
        return "Unspecified device category (0)"
    flags = [flag.replace("_", " ").lower() for flag in repr(device_category).split(".")[1].split(":")[0].split("|")]
    return " | ".join(flags)


def generate_device_overview_table(
    site: Site,
    generation_method: str,
    stylesheet: StyleSheet,
) -> list[Flowable]:
    elements: list[Flowable] = []

    # Convert device category to a useful string
    device_data = [
        ["NMI", site.nmi if site.nmi else "Unspecified"],
        ["LFDI", site.lfdi],
        ["Device Category", device_category_to_string(device_category=DeviceCategory(site.device_category))],
        ["Site Generation", generation_method],
    ]
    column_widths = [int(fraction * stylesheet.table_width) for fraction in [0.2, 0.5]]
    table = Table(device_data, colWidths=column_widths)
    tstyle = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), OVERVIEW_BACKGROUND),
            ("TEXTCOLOR", (0, 0), (-1, -1), TABLE_TEXT_COLOR),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ]
    )
    table.setStyle(tstyle)
    elements.append(KeepTogether(table))
    return elements


def generate_site_section(site: Site, stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []

    if site.nmi:
        section_title = f"EndDevice {site.site_id} (nmi: {site.nmi})"
        generation_method = f"Created at {site.created_time.strftime(stylesheet.date_format)}"
    else:
        section_title = f"EndDevice {site.site_id}"
        generation_method = "Generated as part of test procedure precondition."
    elements.append(Paragraph(section_title, stylesheet.subheading))
    elements.append(stylesheet.spacer)
    elements.extend(
        generate_device_overview_table(site=site, generation_method=generation_method, stylesheet=stylesheet)
    )
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


def generate_devices_section(sites: Sequence[Site], stylesheet: StyleSheet) -> list[Flowable]:
    elements: list[Flowable] = []
    elements.append(Paragraph("Devices", stylesheet.heading))
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


def generate_timeline_chart(timeline: Timeline, sites: Sequence[Site]) -> Image:
    fig = go.Figure()

    # Add data streams with numeric x-axis
    for ds in timeline.data_streams:
        x_data = list(range(len(ds.offset_watt_values)))
        y_data = ds.offset_watt_values
        line_shape = "hv" if ds.stepped else "linear"
        line = {"dash": "dash"} if ds.dashed else {}
        fig.add_trace(go.Scatter(x=x_data, y=y_data, name=ds.label, line_shape=line_shape, line=line))

    # Figure out if we have a setMaxW to utilise
    set_max_w: int | None = None
    for site in sites:
        try:
            der_setting = site.site_ders[0].site_der_setting
            if der_setting:
                set_max_w = int(der_setting.max_w_value * pow(10, der_setting.max_w_multiplier))
                break
        except Exception as exc:
            logger.error(f"Failing looking up setMaxW for site {site.site_id}", exc_info=exc)

    shapes = [
        # This adds emphasis to the zero line
        dict(
            type="line",
            xref="paper",
            x0=0,
            x1=1,
            yref="y",
            y0=0,
            y1=0,
            line=dict(color="black", width=2, dash="dash"),
            opacity=0.5,
        )
    ]
    annotations = []

    if set_max_w is not None:
        shapes.extend(
            [
                dict(
                    type="line",
                    xref="paper",
                    x0=0,
                    x1=1,
                    yref="y",
                    y0=set_max_w,
                    y1=set_max_w,
                    line=dict(color="red", width=2, dash="dash"),
                ),
                dict(
                    type="line",
                    xref="paper",
                    x0=0,
                    x1=1,
                    yref="y",
                    y0=-set_max_w,
                    y1=-set_max_w,
                    line=dict(color="red", width=2, dash="dash"),
                ),
            ]
        )
        annotations.extend(
            [
                dict(
                    xref="paper",
                    x=0.5,  # Middle of the page
                    y=set_max_w,
                    xanchor="center",
                    yanchor="middle",
                    text="setMaxW",
                    showarrow=False,
                    font=dict(color="red", size=12),
                    bgcolor="white",
                    bordercolor="red",
                ),
                dict(
                    xref="paper",
                    x=0.5,  # Middle of the page
                    y=-set_max_w,
                    xanchor="center",
                    yanchor="middle",
                    text="setMaxW",
                    showarrow=False,
                    font=dict(color="red", size=12),
                    bgcolor="white",
                    bordercolor="red",
                ),
            ]
        )

    # Generate x-axis labels
    num_intervals = max(len(ds.offset_watt_values) for ds in timeline.data_streams) if timeline.data_streams else 0
    tick_spacing = max(1, num_intervals // 10)

    tickvals = list(range(0, num_intervals, tick_spacing))
    x_labels = [duration_to_label(timeline.interval_seconds * i) for i in tickvals]

    fig.update_xaxes(
        title="Time",
        type="linear",
        tickmode="array",
        tickvals=tickvals,
        ticktext=x_labels,
        range=[0, max(num_intervals - 1, 1)],
    )

    fig.update_yaxes(title="Watts", zeroline=True)
    fig.update_layout(
        margin=dict(b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        shapes=shapes,
        annotations=annotations,
    )

    return fig_to_image(fig=fig, content_width=MAX_CONTENT_WIDTH)


def _add_step_completion_markers(
    fig: go.Figure, runner_state: RunnerState, timeline: Timeline, num_intervals: int
) -> None:
    """Add vertical lines marking when test steps were completed."""
    if not (runner_state.active_test_procedure and runner_state.active_test_procedure.step_status):
        return

    # Collect completed steps with their timestamps
    completed_steps = [
        (step_name, step_info.completed_at)
        for step_name, step_info in runner_state.active_test_procedure.step_status.items()
        if step_info.get_step_status() == StepStatus.RESOLVED
    ]

    if not completed_steps:
        return

    # Group timestamps by step name
    step_groups: dict[str, list] = {}
    for step_name, completed_at in completed_steps:
        step_groups.setdefault(step_name, []).append(completed_at)

    colors = px.colors.qualitative.Plotly

    # Add vertical lines for each step completion
    for step_idx, (step_name, timestamps) in enumerate(step_groups.items()):
        step_color = colors[step_idx % len(colors)]

        # Calculate interval positions for all timestamps
        x_positions = []
        for completed_at in timestamps:
            time_offset = (completed_at - timeline.start).total_seconds()
            interval_position = time_offset / timeline.interval_seconds
            if 0 <= interval_position <= num_intervals:
                x_positions.append(interval_position)

        # Add a line for each occurrence
        for i, x_pos in enumerate(x_positions):
            fig.add_trace(
                go.Scatter(
                    x=[x_pos, x_pos],
                    y=[0.05, 0.95],
                    mode="lines",
                    name=step_name,
                    line=dict(color=step_color, width=3),
                    showlegend=(i == 0),  # Only show legend for first occurrence
                    hovertemplate=f"{step_name}<extra></extra>",
                )
            )


def generate_timeline_checklist(timeline: Timeline, runner_state: RunnerState) -> Image:
    """
    Generates a horizontal activity chart showing request activity and step completions.
    This chart shares the same x-axis (time) scale as the timeline chart above it.
    We must manually keep the axis identical, it is not a shared axis.
    """
    fig = go.Figure()

    # Calculate time range
    num_intervals = max(len(ds.offset_watt_values) for ds in timeline.data_streams) if timeline.data_streams else 0
    total_duration = timeline.interval_seconds * num_intervals

    # Add request lines - one line per request at precise position
    request_positions = []

    for request_entry in runner_state.request_history:
        time_offset = (request_entry.timestamp - timeline.start).total_seconds()
        if 0 <= time_offset <= total_duration:
            # Convert time offset to precise interval position (float)
            interval_position = time_offset / timeline.interval_seconds
            request_positions.append(interval_position)

    if request_positions:
        # Add legend entry for requests
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="lines",
                line=dict(color="grey", width=2),
                name="Requests",
                showlegend=True,
            )
        )

        # Add request lines at their precise positions
        # Overlapping lines will naturally create darker appearance
        for interval_position in request_positions:
            fig.add_trace(
                go.Scatter(
                    x=[interval_position, interval_position],
                    y=[0.05, 0.95],
                    mode="lines",
                    line=dict(color="rgba(0, 0, 0, 0.4)", width=2),
                    showlegend=False,
                    hovertemplate="Request<extra></extra>",
                )
            )

    # Add step completion markers
    _add_step_completion_markers(fig, runner_state, timeline, num_intervals)

    fig.update_xaxes(
        title="",
        type="linear",
        tickmode="array",
        tickvals=list(range(num_intervals)),
        ticktext=[],
        showticklabels=False,
        range=[0, max(num_intervals - 1, 1)],  # CRITICAL: Match top chart range exactly
    )
    fig.update_yaxes(title="", showticklabels=False, showgrid=False, range=[0, 1])
    fig.update_layout(
        height=150, legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="center", x=0.5), margin=dict(t=0, b=80)
    )

    return fig_to_image(fig=fig, content_width=MAX_CONTENT_WIDTH)


def generate_timeline_section(
    timeline: Timeline | None, runner_state: RunnerState, sites: Sequence[Site], stylesheet: StyleSheet
) -> list[Flowable]:
    elements: list[Flowable] = []
    elements.append(Paragraph("Timeline", stylesheet.heading))
    if timeline is not None:
        elements.append(
            Paragraph(
                f"This chart is based from when the test was started: {timeline.start.strftime(stylesheet.date_format)}"
            )
        )
        elements.append(generate_timeline_chart(timeline=timeline, sites=sites))
        elements.append(generate_timeline_checklist(timeline=timeline, runner_state=runner_state))
    else:
        elements.append(Paragraph("Timeline chart is unavailable due to a lack of data."))
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
        readings_df[new_x_axis_column] = readings_df[x_axis_column] - base_timestamp  # type: ignore
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


def uom_to_string(uom: UomType | int) -> str:
    return UomType(uom).name.replace("_", " ").lower()


def data_qualifier_to_string(qualifier: DataQualifierType | int) -> str:
    return DataQualifierType(qualifier).name.replace("_", " ").lower()


def phase_to_string(phase: PhaseCode | int) -> str:
    if PhaseCode(phase) == PhaseCode.NOT_APPLICABLE:
        return "n/a"
    return PhaseCode(phase).name.replace("_", " ").lower()


def kind_to_string(kind: KindType | int) -> str:
    return KindType(kind).name.replace("_", " ").lower()


def reading_description(srt: SiteReadingType, exclude_mup: bool = False) -> str:
    mup = srt.site_reading_type_id
    uom_string = uom_to_string(srt.uom)
    qualifier_string = data_qualifier_to_string(srt.data_qualifier)
    mup_text = "" if exclude_mup else f"/mup/{mup}:"
    if srt.phase == 0:
        description = f"{mup_text} {uom_string} ({qualifier_string})"
    else:
        phase = phase_to_string(srt.phase)
        description = f"{mup_text} {uom_string} ({qualifier_string}, {phase})"

    return description


def get_site_type(role_flags: RoleFlagsType) -> str:
    """Convert role flags to 'site' or 'device' based on ReadingLocation bitmask."""

    if (role_flags & ReadingLocation.SITE_READING) == ReadingLocation.SITE_READING:
        return "site"
    elif (role_flags & ReadingLocation.DEVICE_READING) == ReadingLocation.DEVICE_READING:
        return "device"

    return "unknown"


def truncate_mrid(mrid: str) -> str:
    return mrid[:7] + "..." if len(mrid) > 7 else mrid


def validate_cell(reading_type: SiteReadingType, col_idx: int, row_num: int) -> str | None:
    """
    Validates a cell value and returns an error message if invalid.
    These validation steps come from SA TS 5573:2025, Table 8.1

    Args:
        reading_type: The SiteReadingType object
        col_idx: Column index (0-based)
        row_num: Row number for error message (1-based, excluding header)

    Returns:
        Error message string if invalid, None if valid
    """

    if col_idx == 2:  # Site type
        site_type = get_site_type(reading_type.role_flags)
        if site_type == "unknown":
            return "Site type is unknown - check the RoleFlagsType field"

    elif col_idx == 3:  # UOM
        uom = UomType(reading_type.uom)
        if uom not in [UomType.REAL_POWER_WATT, UomType.REACTIVE_POWER_VAR, UomType.FREQUENCY_HZ, UomType.VOLTAGE]:
            return f"UOM {uom.name} ({uom.value}) is not supported"

    elif col_idx == 4:  # Data qualifier
        qualifier = DataQualifierType(reading_type.data_qualifier)
        if qualifier not in [
            DataQualifierType.AVERAGE,
            DataQualifierType.STANDARD,
            DataQualifierType.MAXIMUM,
            DataQualifierType.MINIMUM,
        ]:
            return f"Data qualifier {qualifier.name} ({qualifier.value}) is not supported"

    elif col_idx == 5:  # Kind
        kind = KindType(reading_type.kind)
        if kind != KindType.POWER:
            return f"KindType is expected to be Power (37), got {kind.name} ({kind.value})"

    elif col_idx == 6:  # Phase (Only applicable to voltage readings)
        uom = UomType(reading_type.uom)
        if uom == UomType.VOLTAGE:
            phase = PhaseCode(reading_type.phase)
            if phase not in [
                PhaseCode.PHASE_ABC,
                PhaseCode.PHASE_AN_S1N,
                PhaseCode.PHASE_BN,
                PhaseCode.PHASE_CN_S2N,
            ]:
                return f"Phase (for voltage) has specific requirements - {phase.name} ({phase.value}) is not supported"

    return None


def format_cell_value(value, is_error: bool) -> str | Paragraph:
    """
    Format a cell value, highlighting it with red background if there's an error.

    Returns:
        Formatted value (Paragraph with red background if error, original value otherwise)
    """
    if is_error:
        return Paragraph(
            f"<para backColor='red'><font color='white'>{value}</font></para>",
            style=ParagraphStyle(name="ErrorCell", fontSize=10, leading=12),
        )
    return value


def allow_zero_width_duration(srt: SiteReadingType) -> bool:
    """Certain SiteReadingTypes (eg BESS types) expect zero duration readings. Most other reading types do NOT

    Returns True if the supplied SiteReadingType is permitted to have zero duration readings. False otherwise"""
    return srt.uom == UomType.REAL_ENERGY_WATT_HOURS  # This is our shorthand for identifying BESS readings


def validate_reading_duration(readings_df: pd.DataFrame, allow_zero_duration: bool) -> tuple[int, int, list[str]]:
    """
    Validate reading durations and return warning messages.
    Returns tuple: (dropped_count, invalid_duration_count, warning_messages)
    """
    if readings_df.empty or "time_period_seconds" not in readings_df.columns:
        return 0, 0, []

    warnings: list[str] = []

    # Isolate null or zero durations
    # For v1.3-beta/storage, duration 0 or null values are acceptable for storage readings.
    durations = readings_df["time_period_seconds"]
    null_or_zero = durations.isna() | (durations == 0)
    dropped_count = 0

    if not allow_zero_duration:
        dropped_count = int(null_or_zero.sum())
        if dropped_count > 0:
            warnings.append(
                f"{dropped_count} reading{'s' if dropped_count != 1 else ''}"
                " excluded from timeline generation due to null or zero duration values"
            )

    # Check remaining readings are divisible by 60s
    valid_durations = durations[~null_or_zero]
    invalid_count = 0

    if len(valid_durations) > 0:
        invalid_mod60 = valid_durations[valid_durations % 60 != 0]
        invalid_count = int(len(invalid_mod60))

        if invalid_count > 0:
            warnings.append(
                f"{invalid_count} {"readings have" if invalid_count != 1 else "reading has"}"
                " invalid duration (not divisible by 60). This may indicate a configuration issue."
            )

    return dropped_count, invalid_count, warnings


def generate_reading_count_table(
    reading_counts: dict[SiteReadingType, int],
    stylesheet: StyleSheet,
    readings: dict[SiteReadingType, pd.DataFrame] | None = None,
):
    """
    Generate reading count table with validation and error highlighting.
    Errors are displayed as merged rows immediately below the affected data row.
    """
    elements: list[Flowable] = []
    error_cells = set()
    row_errors = {}
    table_data: list[list] = []
    table_row_idx = 1  # start after header

    # Build table data and validation results
    for data_row_idx, (reading_type, count) in enumerate(reading_counts.items(), start=1):
        row_data = [
            reading_type.site_reading_type_id,
            truncate_mrid(reading_type.mrid),
            get_site_type(reading_type.role_flags),
            uom_to_string(reading_type.uom),
            str(reading_type.data_qualifier),
            str(reading_type.kind),
            phase_to_string(reading_type.phase),
            count,
        ]

        current_row_errors = []

        # Validate existing columns
        for col_idx in [2, 3, 4, 5, 6]:
            error_msg = validate_cell(reading_type, col_idx, data_row_idx)
            if error_msg:
                current_row_errors.append(error_msg)
                error_cells.add((table_row_idx, col_idx))

        # Validate duration if readings DataFrame is available
        if readings and reading_type in readings:
            dropped_count, invalid_count, duration_warnings = validate_reading_duration(
                readings[reading_type], allow_zero_width_duration(reading_type)
            )

            if duration_warnings:
                current_row_errors.extend(duration_warnings)
                # Highlight MUP column (column 0) if any duration issues
                error_cells.add((table_row_idx, 0))

        table_data.append(row_data)

        if current_row_errors:
            row_errors[table_row_idx] = current_row_errors
            # 7 columns for the error row (exclude grey column)
            error_row = [", ".join(current_row_errors)] + [""] * 6
            table_data.append(error_row)
            table_row_idx += 2
        else:
            table_row_idx += 1

    headers = ["/MUP", "MMR", "Site type", "Unit", "Data Qualifier", "Kind", "Phase", "# Readings"]
    table_data.insert(0, headers)

    fractions = [0.07, 0.1, 0.1, 0.2, 0.14, 0.08, 0.1, 0.14]
    column_widths = [int(f * stylesheet.table_width) for f in fractions]

    styles: list[tuple] = [
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.Color(0.85, 0.85, 0.85)),
    ]

    # Error row styling
    for data_row_idx in row_errors:
        error_row_idx = data_row_idx + 1
        styles.extend(
            [
                ("LINEBELOW", (0, data_row_idx), (-1, data_row_idx), 0, colors.white),
                ("BACKGROUND", (0, error_row_idx), (6, error_row_idx), colors.white),
                ("TEXTCOLOR", (0, error_row_idx), (6, error_row_idx), colors.red),
                ("FONTSIZE", (0, error_row_idx), (6, error_row_idx), 6),
                ("TOPPADDING", (0, error_row_idx), (-1, error_row_idx), 2),
                ("BOTTOMPADDING", (0, error_row_idx), (-1, error_row_idx), 2),
                ("LEFTPADDING", (0, error_row_idx), (0, error_row_idx), column_widths[0]),
                ("ALIGNMENT", (0, error_row_idx), (6, error_row_idx), "LEFT"),
                ("ROWHEIGHT", (0, error_row_idx), (6, error_row_idx), 12),
            ]
        )

    # Error cell highlighting
    for row_idx, col_idx in error_cells:
        styles.extend(
            [
                ("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), colors.Color(1, 0.9, 0.9)),
                ("TEXTCOLOR", (col_idx, row_idx), (col_idx, row_idx), colors.red),
            ]
        )

    # Create and style table
    table = Table(table_data, colWidths=column_widths)
    table.setStyle(stylesheet.table)
    table.setStyle(TableStyle(styles))

    elements.append(table)

    footnote = Paragraph(
        "For more information, see Standards Australia SA TS 5573:2025, Table 8.1.",
        ParagraphStyle(name="TableFootNote", fontSize=6, leading=6),
    )
    elements.extend([footnote, stylesheet.spacer])

    return elements


def generate_readings_section(
    runner_state: RunnerState,
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    stylesheet: StyleSheet,
) -> list[Flowable]:

    elements: list[Flowable] = []
    elements.append(Paragraph("Readings", stylesheet.heading))

    # Add table to show how many of each reading type was sent to the utility server (all reading types)
    if reading_counts:
        elements.append(stylesheet.spacer)
        elements.extend(
            generate_reading_count_table(reading_counts=reading_counts, stylesheet=stylesheet, readings=readings)
        )

        # Add charts for each of the different reading types
        if readings:
            for reading_type, readings_df in readings.items():
                elements.append(Paragraph(reading_description(reading_type), style=stylesheet.subheading))
                elements.append(
                    generate_readings_timeline(
                        readings_df=readings_df, quantity=uom_to_string(reading_type.uom), runner_state=runner_state
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
    run_group_name: str,
    check_results: dict[str, CheckResult],
    readings: dict[SiteReadingType, pd.DataFrame],
    reading_counts: dict[SiteReadingType, int],
    sites: Sequence[Site],
    timeline: Timeline | None,
    stylesheet: StyleSheet,
) -> list[Flowable]:
    active_test_procedure = runner_state.active_test_procedure
    if active_test_procedure is None:
        raise ValueError("'active_test_procedure' attribute of 'runner_state' cannot be None")

    page_elements: list[Flowable] = []

    test_procedure_name = active_test_procedure.name
    test_procedure_description = active_test_procedure.definition.description
    test_procedure_classes = active_test_procedure.definition.classes

    # The title is handles by the first page banner
    # We need a space to skip past the banner
    page_elements.append(Spacer(1, MARGIN))

    # Check if the test contains classes that require witness testing
    requires_witness_testing = any(test_class in WITNESS_TEST_CLASSES for test_class in test_procedure_classes)

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
                run_group_name=run_group_name,
                init_timestamp=init_timestamp,
                start_timestamp=start_timestamp,
                client_lfdi=active_test_procedure.client_lfdi,
                client_cert_type=active_test_procedure.client_certificate_type,
                client_pen=active_test_procedure.pen,
                duration=duration,
                stylesheet=stylesheet,
                playlist_info=(
                    f"Test {runner_state.playlist_index + 1} of {len(runner_state.playlist)}"
                    if runner_state.playlist
                    else None
                ),
            )
        )
    except ValueError as e:
        # ValueError is raised by 'first_client_interaction_of_type' if it can find the required
        # client interations. This is a guard-rail. If we have an active test procedure then
        # the appropriate client interactions SHOULD be defined in the runner state.
        logger.error(f"Unable to add 'test procedure overview' to PDF report. Reason={repr(e)}")

    # Criteria Section
    page_elements.extend(
        generate_criteria_section(
            check_results=check_results, stylesheet=stylesheet, requires_witness_testing=requires_witness_testing
        )
    )

    # Timeline Section
    page_elements.extend(
        generate_timeline_section(timeline=timeline, runner_state=runner_state, sites=sites, stylesheet=stylesheet)
    )

    # Devices Section
    page_elements.extend(generate_devices_section(sites=sites, stylesheet=stylesheet))

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
    sites: Sequence[Site],
    timeline: Timeline | None,
    no_spacers: bool = False,
) -> bytes:
    stylesheet = get_stylesheet()
    if no_spacers:
        stylesheet.spacer = NullDraw()

    if runner_state.active_test_procedure is None:
        raise ValueError("Unable to generate report - no active test procedure")

    run_id = runner_state.active_test_procedure.run_id
    test_run_id = "UNKNOWN" if run_id is None else run_id
    name = runner_state.active_test_procedure.run_group_name
    run_group_name = "" if name is None else name

    page_elements = generate_page_elements(
        runner_state=runner_state,
        test_run_id=test_run_id,
        run_group_name=run_group_name,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=timeline,
        stylesheet=stylesheet,
    )

    test_procedure_name = runner_state.active_test_procedure.name
    first_page = partial(
        first_page_template,
        test_procedure_name=test_procedure_name,
        test_run_id=test_run_id,
        csip_aus_version=runner_state.active_test_procedure.csip_aus_version,
    )
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
