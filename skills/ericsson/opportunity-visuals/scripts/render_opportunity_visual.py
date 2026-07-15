#!/usr/bin/env python3
"""Render normalized opportunity data as deterministic, local-only SVG pages."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree


MARGIN = 48
TITLE_HEIGHT = 70
HEADER_HEIGHT = 50
AREA_HEIGHT = 30
ROW_HEIGHT = 46
FOOTER_HEIGHT = 26
FIXED_COLUMNS = (
    ("area", 120),
    ("sub_area", 150),
    ("opportunity_name", 300),
    ("tcv", 110),
    ("probability", 150),
)
MIN_MONTH_WIDTH = 116
MAX_DIMENSION = 16384
PNG_UNAVAILABLE_REASON = "Install playwright>=1.52 and Chromium to enable PNG output"

STAGE_COLORS = {
    "initial": "#A6A6A6",
    "positive": "#23969A",
    "negative": "#E65D6A",
    "neutral": "#A6A6A6",
    "mixed": "#A6A6A6",
    "unknown": "#A6A6A6",
    "won": "#23969A",
    "lost": "#E65D6A",
}
VIEW_TITLES = {
    "wins": "Ericsson Opportunity Wins — Stage Progression, TCV & Probability",
    "losses": "Ericsson Opportunity Losses — Stage Progression, TCV & Probability",
    "all-progression": "Ericsson Opportunity Stage Progression — Monthly History",
    "positive-progression": "Ericsson Opportunity Progression — Positive Movement",
}
PROBABILITY_COLORS = {
    "low": "#E65D6A",
    "medium": "#A6A6A6",
    "high": "#23969A",
    "certain": "#23969A",
}

SVG_NS = "http://www.w3.org/2000/svg"
_SVG = f"{{{SVG_NS}}}"
_FIXED_LABELS = {
    "area": "Area",
    "sub_area": "Sub-area",
    "opportunity_name": "Opportunity Name",
    "tcv": "TCV",
    "probability": "Probability",
}
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_AUDIT_CLASSIFICATIONS = frozenset({*STAGE_COLORS, "empty"})


class RenderError(ValueError):
    """A safe renderer contract error suitable for structured CLI output."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class RasterUnavailable(RuntimeError):
    """The optional local PNG renderer cannot run in this environment."""


@dataclass(frozen=True)
class PagePlan:
    number: int
    month_keys: tuple[str, ...]
    row_ids: tuple[str, ...]
    continued_areas: tuple[str, ...]
    horizontal_index: int
    width: int = 1920
    height: int = 1080


@dataclass(frozen=True)
class _OwnedPublication:
    path: Path
    owner_handle: BinaryIO
    owner_stat: os.stat_result
    stage_path: Path
    expected_sha256: str
    cleanup_dir: Path | None = None


@dataclass(frozen=True)
class _VerifiedArtifactDigests:
    svg: str
    html: str
    png: str | None


def _number(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _scale(width: int) -> float:
    return width / 1920


def _require_dimensions(width: object, height: object) -> tuple[int, int]:
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or width > MAX_DIMENSION
        or height > MAX_DIMENSION
    ):
        raise RenderError(
            "invalid_dimensions",
            f"Width and height must be between 1 and {MAX_DIMENSION} pixels",
        )
    return width, height


def _validate_xml_text(value: str) -> str:
    if any(
        not (
            character in "\t\n\r"
            or "\x20" <= character <= "\ud7ff"
            or "\ue000" <= character <= "\ufffd"
            or "\U00010000" <= character <= "\U0010ffff"
        )
        for character in value
    ):
        raise RenderError("invalid_xml_text", "User value is not valid XML text")
    return value


def _is_positive_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _validate_safe_string(value: object, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise RenderError("invalid_document", message)
    _validate_xml_text(value)
    return value


def _validated_source(source: object) -> dict[str, object]:
    if not isinstance(source, dict):
        raise RenderError("invalid_document", "Normalized source metadata is invalid")
    basename = _validate_safe_string(
        source.get("basename"), "Normalized source metadata is invalid"
    )
    source_hash = source.get("sha256")
    sheet = source.get("sheet")
    if (
        basename in {".", ".."}
        or "/" in basename
        or "\\" in basename
        or not isinstance(source_hash, str)
        or _SHA256.fullmatch(source_hash) is None
    ):
        raise RenderError("invalid_document", "Normalized source metadata is invalid")
    if sheet is not None:
        sheet = _validate_safe_string(sheet, "Normalized source metadata is invalid")
        if any(character in sheet for character in "[]:*?/\\"):
            raise RenderError("invalid_document", "Normalized source metadata is invalid")
    return {"basename": basename, "sha256": source_hash, "sheet": sheet}


def _validate_warning(warning: object, *, top_level: bool) -> None:
    if not isinstance(warning, dict):
        raise RenderError("invalid_document", "Normalized warnings are invalid")
    _validate_safe_string(warning.get("code"), "Normalized warnings are invalid")
    message = warning.get("message")
    if not isinstance(message, str):
        raise RenderError("invalid_document", "Normalized warnings are invalid")
    if top_level:
        _validate_safe_string(warning.get("id"), "Normalized warnings are invalid")
        if not _is_positive_int(warning.get("source_row")):
            raise RenderError("invalid_document", "Normalized warnings are invalid")
    month = warning.get("month")
    if month is not None:
        _validate_safe_string(month, "Normalized warnings are invalid")
    skipped = warning.get("skipped_months")
    if skipped is not None and (
        not isinstance(skipped, list)
        or any(not isinstance(item, str) for item in skipped)
    ):
        raise RenderError("invalid_document", "Normalized warnings are invalid")


def _validate_audit_structure(
    document: dict[str, object], selected_keys: set[str]
) -> None:
    _validated_source(document.get("source"))
    for key in ("mapping", "semantics", "filters"):
        if not isinstance(document.get(key), dict):
            raise RenderError("invalid_document", f"Normalized {key} metadata is invalid")
    try:
        json.dumps(
            {
                "mapping": document["mapping"],
                "semantics": document["semantics"],
                "filters": document["filters"],
            },
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise RenderError("invalid_document", "Normalized audit metadata is invalid") from None

    exclusions = document.get("exclusions")
    if not isinstance(exclusions, list):
        raise RenderError("invalid_document", "Normalized exclusions are invalid")
    for exclusion in exclusions:
        if not isinstance(exclusion, dict):
            raise RenderError("invalid_document", "Normalized exclusions are invalid")
        _validate_safe_string(exclusion.get("id"), "Normalized exclusions are invalid")
        _validate_safe_string(exclusion.get("code"), "Normalized exclusions are invalid")
        if not _is_positive_int(exclusion.get("source_row")):
            raise RenderError("invalid_document", "Normalized exclusions are invalid")
        if not isinstance(exclusion.get("message"), str):
            raise RenderError("invalid_document", "Normalized exclusions are invalid")

    warnings = document.get("warnings")
    if not isinstance(warnings, list):
        raise RenderError("invalid_document", "Normalized warnings are invalid")
    for warning in warnings:
        _validate_warning(warning, top_level=True)

    for record in document["records"]:
        nested_warnings = record.get("warnings")
        if not isinstance(nested_warnings, list):
            raise RenderError("invalid_document", "Normalized record warnings are invalid")
        for warning in nested_warnings:
            _validate_warning(warning, top_level=False)
        for month in record["months"]:
            key = month["key"]
            classification = month["classification"]
            skipped = month.get("skipped_months")
            if key not in selected_keys:
                raise RenderError("invalid_record", "Normalized record month is invalid")
            if classification not in _AUDIT_CLASSIFICATIONS:
                raise RenderError(
                    "invalid_record", "Normalized stage classification is invalid"
                )
            if (not month["stage"].strip()) != (classification == "empty"):
                raise RenderError(
                    "invalid_record", "Normalized stage classification is invalid"
                )
            if not isinstance(skipped, list) or any(
                not isinstance(item, str) for item in skipped
            ):
                raise RenderError("invalid_record", "Normalized skipped months are invalid")


def _validate_structure(
    document: object,
    *,
    require_records: bool = True,
    validate_render_values: bool = False,
) -> dict[str, object]:
    if not isinstance(document, dict):
        raise RenderError("invalid_document", "Normalized data must be a JSON object")
    if document.get("schema_version") != 1:
        raise RenderError("unsupported_schema", "Normalized schema version must be 1")
    view = document.get("view")
    if view not in VIEW_TITLES:
        raise RenderError("unsupported_view", "Normalized data contains an unsupported view")
    selected_months = document.get("selected_months")
    if not isinstance(selected_months, list) or not selected_months:
        raise RenderError("missing_months", "Normalized data must contain selected months")
    month_keys: list[str] = []
    for selected in selected_months:
        if (
            not isinstance(selected, dict)
            or not isinstance(selected.get("key"), str)
            or not selected["key"]
            or not isinstance(selected.get("label"), str)
        ):
            raise RenderError("invalid_month", "Selected month entries are invalid")
        month_keys.append(selected["key"])
        if validate_render_values:
            _validate_xml_text(selected["key"])
            _validate_xml_text(selected["label"])
    if len(set(month_keys)) != len(month_keys):
        raise RenderError("duplicate_month", "Selected month keys must be unique")

    records = document.get("records")
    if not isinstance(records, list):
        raise RenderError("invalid_records", "Normalized records must be a list")
    if require_records and not records:
        raise RenderError("empty_records", "Normalized records must not be empty")
    row_ids: list[str] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
            raise RenderError("invalid_record", "Normalized record is invalid")
        if (
            not isinstance(record.get("area"), str)
            or not isinstance(record.get("sub_area"), str)
            or not isinstance(record.get("opportunity_name"), str)
        ):
            raise RenderError("invalid_record", "Normalized record grouping is invalid")
        if (
            isinstance(record.get("source_row"), bool)
            or not isinstance(record.get("source_row"), int)
            or record["source_row"] < 1
        ):
            raise RenderError("invalid_record", "Normalized record source row is invalid")
        if not isinstance(record.get("months"), list):
            raise RenderError("invalid_record", "Normalized record months are invalid")
        if validate_render_values:
            for ranked_field in ("tcv", "probability"):
                ranked = record.get(ranked_field)
                if not isinstance(ranked, dict) or not isinstance(ranked.get("display"), str):
                    raise RenderError("invalid_record", "Normalized record values are invalid")
                _validate_xml_text(ranked["display"])
            _validate_xml_text(record["id"])
            _validate_xml_text(record["area"])
            _validate_xml_text(record["sub_area"])
            _validate_xml_text(record["opportunity_name"])
            record_month_keys: list[str] = []
            for month in record["months"]:
                if (
                    not isinstance(month, dict)
                    or not isinstance(month.get("key"), str)
                    or not isinstance(month.get("stage"), str)
                    or not isinstance(month.get("classification"), str)
                ):
                    raise RenderError("invalid_record", "Normalized record months are invalid")
                record_month_keys.append(_validate_xml_text(month["key"]))
                _validate_xml_text(month["stage"])
            if len(set(record_month_keys)) != len(record_month_keys):
                raise RenderError("invalid_record", "Normalized record month keys must be unique")
        row_ids.append(record["id"])
    if len(set(row_ids)) != len(row_ids):
        raise RenderError("duplicate_record", "Normalized record IDs must be unique")
    if validate_render_values:
        _validate_audit_structure(document, set(month_keys))
    return document


def _probability_color(record: dict[str, object]) -> str:
    probability = record.get("probability")
    if not isinstance(probability, dict):
        raise RenderError("invalid_probability", "Normalized probability is invalid")
    display = probability.get("display")
    kind = probability.get("kind")
    rank = probability.get("sort")
    if not isinstance(display, str):
        raise RenderError("invalid_probability", "Normalized probability is invalid")
    if kind == "numeric":
        if isinstance(rank, bool) or not isinstance(rank, Real):
            raise RenderError("invalid_probability", "Normalized probability is invalid")
        try:
            numeric = float(rank)
        except (TypeError, ValueError, OverflowError):
            raise RenderError(
                "invalid_probability", "Normalized probability is invalid"
            ) from None
        if not math.isfinite(numeric):
            raise RenderError("invalid_probability", "Normalized probability is invalid")
        if not 0 <= numeric <= 100:
            raise RenderError(
                "invalid_probability",
                "Numeric probability must be between 0 and 100",
            )
        if numeric < 40:
            return "#E65D6A"
        if numeric < 70:
            return "#A6A6A6"
        return "#23969A"
    if kind != "categorical":
        raise RenderError("invalid_probability", "Normalized probability kind is invalid")
    return PROBABILITY_COLORS.get(display.casefold(), "#A6A6A6")


def paginate(document: dict[str, object], width: int, height: int) -> list[PagePlan]:
    """Plan contiguous horizontal month slices and complete vertical row slices."""

    document = _validate_structure(document)
    width, height = _require_dimensions(width, height)
    scale = _scale(width)
    fixed_width = sum(column_width for _, column_width in FIXED_COLUMNS) * scale
    available_month_width = width - 2 * MARGIN * scale - fixed_width
    minimum_month_width = MIN_MONTH_WIDTH * scale
    months_per_slice = math.floor((available_month_width + 1e-9) / minimum_month_width)
    if months_per_slice < 1:
        raise RenderError("dimensions_too_small", "Page width cannot fit one month column")

    selected_months = document["selected_months"]
    month_slices = [
        tuple(month["key"] for month in selected_months[start : start + months_per_slice])
        for start in range(0, len(selected_months), months_per_slice)
    ]
    records = document["records"]
    content_top = (MARGIN + TITLE_HEIGHT + HEADER_HEIGHT) * scale
    content_bottom = height - (MARGIN + FOOTER_HEIGHT) * scale
    area_height = AREA_HEIGHT * scale
    row_height = ROW_HEIGHT * scale
    if content_top + area_height + row_height > content_bottom + 1e-9:
        raise RenderError("dimensions_too_small", "Page height cannot fit one opportunity row")

    pages: list[PagePlan] = []
    for horizontal_index, month_keys in enumerate(month_slices):
        start = 0
        while start < len(records):
            y = content_top
            row_ids: list[str] = []
            last_area: str | None = None
            continued: tuple[str, ...] = ()
            if start > 0 and records[start - 1]["area"] == records[start]["area"]:
                continued = (str(records[start]["area"]),)

            index = start
            while index < len(records):
                area = str(records[index]["area"])
                required = row_height + (area_height if last_area != area else 0)
                if y + required > content_bottom + 1e-9:
                    break
                if last_area != area:
                    y += area_height
                    last_area = area
                row_ids.append(str(records[index]["id"]))
                y += row_height
                index += 1

            if not row_ids:
                raise RenderError("dimensions_too_small", "Page height cannot fit one opportunity row")
            pages.append(
                PagePlan(
                    number=len(pages) + 1,
                    month_keys=month_keys,
                    row_ids=tuple(row_ids),
                    continued_areas=continued,
                    horizontal_index=horizontal_index,
                    width=width,
                    height=height,
                )
            )
            start = index
    return pages


def ellipsize(value: str, max_width: float, font_size: float = 16) -> str:
    """Return a deterministic single-line approximation that fits the width."""

    if max_width <= 0 or font_size <= 0:
        return "…" if value else value
    max_characters = max(1, math.floor(max_width / (font_size * 0.56)))
    if len(value) <= max_characters:
        return value
    if max_characters == 1:
        return "…"
    return value[: max_characters - 1] + "…"


def add_rect(
    parent: ElementTree.Element,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    fill: str,
    class_name: str | None = None,
    metadata: dict[str, str] | None = None,
    radius: float | None = None,
) -> ElementTree.Element:
    attributes: dict[str, str] = dict(metadata or {})
    attributes.update(
        {
            "x": _number(x),
            "y": _number(y),
            "width": _number(width),
            "height": _number(height),
            "fill": fill,
        }
    )
    if class_name is not None:
        attributes["class"] = class_name
    if radius is not None:
        attributes["rx"] = _number(radius)
        attributes["ry"] = _number(radius)
    return ElementTree.SubElement(parent, f"{_SVG}rect", attributes)


def add_text(
    parent: ElementTree.Element,
    x: float,
    y: float,
    value: str,
    *,
    class_name: str,
    max_width: float | None = None,
    font_size: float = 16,
    attributes: dict[str, str] | None = None,
) -> ElementTree.Element:
    element_attributes = {
        "x": _number(x),
        "y": _number(y),
        "class": class_name,
        **(attributes or {}),
    }
    displayed = value
    if max_width is not None:
        displayed = ellipsize(value, max_width, font_size)
        if displayed != value:
            element_attributes["data-full-value"] = value
    element = ElementTree.SubElement(parent, f"{_SVG}text", element_attributes)
    element.text = displayed
    return element


def add_line(
    parent: ElementTree.Element,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    class_name: str = "grid",
) -> ElementTree.Element:
    return ElementTree.SubElement(
        parent,
        f"{_SVG}line",
        {
            "x1": _number(x1),
            "y1": _number(y1),
            "x2": _number(x2),
            "y2": _number(y2),
            "class": class_name,
        },
    )


def add_stage_pill(
    parent: ElementTree.Element,
    x: float,
    y: float,
    width: float,
    height: float,
    stage: str,
    classification: str,
    *,
    font_size: float,
    opportunity_id: str,
    month_key: str,
) -> ElementTree.Element:
    if classification not in STAGE_COLORS:
        raise RenderError("invalid_classification", "Normalized stage classification is invalid")
    group = ElementTree.SubElement(
        parent,
        f"{_SVG}g",
        {
            "data-opportunity-id": opportunity_id,
            "data-month": month_key,
            "data-classification": classification,
        },
    )
    add_rect(
        group,
        x,
        y,
        width,
        height,
        fill=STAGE_COLORS[classification],
        radius=height / 2,
    )
    add_text(
        group,
        x + width / 2,
        y + height / 2 + font_size * 0.35,
        stage,
        class_name="stage",
        max_width=max(1, width - 16 * (font_size / 16)),
        font_size=font_size,
        attributes={
            "text-anchor": "middle",
            "style": f"font-size:{_number(font_size)}px",
        },
    )
    return group


def _record_lookup(document: dict[str, object], page: PagePlan) -> list[dict[str, object]]:
    by_id = {record["id"]: record for record in document["records"]}
    try:
        return [by_id[row_id] for row_id in page.row_ids]
    except KeyError:
        raise RenderError("invalid_page", "Page references an unknown record") from None


def _month_lookup(document: dict[str, object], page: PagePlan) -> list[dict[str, object]]:
    by_key = {month["key"]: month for month in document["selected_months"]}
    try:
        return [by_key[key] for key in page.month_keys]
    except KeyError:
        raise RenderError("invalid_page", "Page references an unknown month") from None


def _load_template(template_path: Path) -> ElementTree.Element:
    try:
        root = ElementTree.parse(template_path).getroot()
    except (OSError, ElementTree.ParseError):
        raise RenderError("invalid_template", "SVG template is unavailable or invalid") from None
    if root.tag != f"{_SVG}svg":
        raise RenderError("invalid_template", "SVG template root is invalid")
    return root


def render_svg_page(
    document: dict[str, object],
    page: PagePlan,
    template_path: Path,
) -> str:
    """Populate the reviewed XML template using ElementTree-only data insertion."""

    document = _validate_structure(document, validate_render_values=True)
    width, height = _require_dimensions(page.width, page.height)
    records = _record_lookup(document, page)
    selected_months = _month_lookup(document, page)
    for record in records:
        _probability_color(record)

    root = _load_template(Path(template_path))
    root.set("width", str(width))
    root.set("height", str(height))
    root.set("viewBox", f"0 0 {width} {height}")
    title = root.find(f"{_SVG}title")
    description = root.find(f"{_SVG}desc")
    background = root.find(f"{_SVG}rect[@id='background']")
    content = root.find(f"{_SVG}g[@id='content']")
    if title is None or description is None or background is None or content is None:
        raise RenderError("invalid_template", "SVG template is missing required elements")
    title.text = VIEW_TITLES[str(document["view"])]
    description.text = f"Opportunity stage progression table, page {page.number}"
    background.set("width", str(width))
    background.set("height", str(height))
    content.clear()
    content.set("id", "content")

    scale = _scale(width)
    margin = MARGIN * scale
    title_height = TITLE_HEIGHT * scale
    header_height = HEADER_HEIGHT * scale
    area_height = AREA_HEIGHT * scale
    row_height = ROW_HEIGHT * scale
    footer_height = FOOTER_HEIGHT * scale
    body_font = max(16, 16 * scale) if width >= 1920 else 16 * scale
    header_font = 17 * scale
    stage_font = max(16, 16 * scale) if width >= 1920 else 16 * scale
    fixed_columns = [(key, value * scale) for key, value in FIXED_COLUMNS]
    fixed_width = sum(value for _, value in fixed_columns)
    table_width = width - 2 * margin
    month_width = (table_width - fixed_width) / len(selected_months)

    add_text(
        content,
        margin,
        margin + 34 * scale,
        VIEW_TITLES[str(document["view"])],
        class_name="title",
        max_width=table_width - 180 * scale,
        font_size=30 * scale,
        attributes={"style": f"font-size:{_number(30 * scale)}px"},
    )
    add_text(
        content,
        width - margin,
        margin + 30 * scale,
        f"Page {page.number}",
        class_name="header",
        font_size=header_font,
        attributes={"text-anchor": "end", "style": f"font-size:{_number(header_font)}px"},
    )

    header_y = margin + title_height
    add_rect(content, margin, header_y, table_width, header_height, fill="#1174E6")
    x = margin
    for key, column_width in fixed_columns:
        add_text(
            content,
            x + 8 * scale,
            header_y + header_height / 2 + header_font * 0.35,
            _FIXED_LABELS[key],
            class_name="header",
            max_width=column_width - 16 * scale,
            font_size=header_font,
            attributes={"fill": "#FFFFFF", "style": f"font-size:{_number(header_font)}px;fill:#FFFFFF"},
        )
        x += column_width
    for selected in selected_months:
        add_text(
            content,
            x + month_width / 2,
            header_y + header_height / 2 + header_font * 0.35,
            str(selected["label"]),
            class_name="header",
            max_width=month_width - 12 * scale,
            font_size=header_font,
            attributes={
                "text-anchor": "middle",
                "fill": "#FFFFFF",
                "style": f"font-size:{_number(header_font)}px;fill:#FFFFFF",
            },
        )
        x += month_width

    y = header_y + header_height
    last_area: str | None = None
    for record in records:
        area = str(record["area"])
        if area != last_area:
            add_rect(
                content,
                margin,
                y,
                table_width,
                area_height,
                fill="#F2F2F2",
                class_name="grid",
                metadata={"data-area": area},
            )
            continuation = " (continued)" if area in page.continued_areas else ""
            add_text(
                content,
                margin + 8 * scale,
                y + area_height / 2 + body_font * 0.35,
                f"Area: {area}{continuation}",
                class_name="body",
                max_width=table_width - 16 * scale,
                font_size=body_font,
                attributes={"style": f"font-size:{_number(body_font)}px;font-weight:600"},
            )
            y += area_height
            last_area = area

        row_group = ElementTree.SubElement(
            content,
            f"{_SVG}g",
            {
                "data-opportunity-id": str(record["id"]),
                "data-area": area,
                "data-sub-area": str(record["sub_area"]),
            },
        )
        x = margin
        fixed_values = {
            "area": area,
            "sub_area": str(record["sub_area"]),
            "opportunity_name": str(record["opportunity_name"]),
            "tcv": str(record["tcv"]["display"]),
            "probability": str(record["probability"]["display"]),
        }
        for key, column_width in fixed_columns:
            add_rect(row_group, x, y, column_width, row_height, fill="#FFFFFF", class_name="grid")
            text_x = x + 8 * scale
            if key == "probability":
                ElementTree.SubElement(
                    row_group,
                    f"{_SVG}circle",
                    {
                        "class": "probability-bullet",
                        "fill": _probability_color(record),
                        "cx": _number(x + 12 * scale),
                        "cy": _number(y + row_height / 2),
                        "r": _number(4 * scale),
                    },
                )
                text_x = x + 22 * scale
            add_text(
                row_group,
                text_x,
                y + row_height / 2 + body_font * 0.35,
                fixed_values[key],
                class_name="body",
                max_width=column_width - (30 if key == "probability" else 16) * scale,
                font_size=body_font,
                attributes={"style": f"font-size:{_number(body_font)}px"},
            )
            x += column_width

        record_months = {
            entry["key"]: entry for entry in record["months"] if isinstance(entry, dict)
        }
        for selected in selected_months:
            key = str(selected["key"])
            entry = record_months.get(key)
            metadata = None
            if entry is not None and not str(entry.get("stage", "")).strip():
                metadata = {
                    "data-opportunity-id": str(record["id"]),
                    "data-month": key,
                    "data-empty": "true",
                }
            add_rect(
                row_group,
                x,
                y,
                month_width,
                row_height,
                fill="#FFFFFF",
                class_name="grid",
                metadata=metadata,
            )
            if entry is not None and str(entry.get("stage", "")).strip():
                inset_x = 6 * scale
                inset_y = 7 * scale
                add_stage_pill(
                    row_group,
                    x + inset_x,
                    y + inset_y,
                    month_width - 2 * inset_x,
                    row_height - 2 * inset_y,
                    str(entry["stage"]),
                    str(entry.get("classification", "")),
                    font_size=stage_font,
                    opportunity_id=str(record["id"]),
                    month_key=key,
                )
            x += month_width
        y += row_height

    footer_y = height - margin - footer_height / 2
    add_line(content, margin, footer_y - 10 * scale, width - margin, footer_y - 10 * scale)
    add_text(
        content,
        width - margin,
        footer_y + body_font * 0.35,
        f"Page {page.number} · Month set {page.horizontal_index + 1}",
        class_name="body",
        font_size=body_font,
        attributes={"text-anchor": "end", "style": f"font-size:{_number(body_font)}px"},
    )

    ElementTree.register_namespace("", SVG_NS)
    serialized = ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + serialized + "\n"


_FORBIDDEN_SVG_ELEMENTS = {
    "a",
    "foreignobject",
    "iframe",
    "image",
    "script",
    "use",
}
_URL_SCHEME = re.compile(
    r"(?:https?|file|data|javascript|vbscript|ftp)\s*:", re.IGNORECASE
)
_CSS_URL = re.compile(r"url\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].casefold()


def _contains_non_fragment_url(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if _URL_SCHEME.search(stripped) or "://" in stripped or stripped.startswith("//"):
        return True
    if "@import" in stripped.casefold():
        return True
    for match in _CSS_URL.finditer(stripped):
        target = match.group(1).strip().strip("\"'").strip()
        if not target.startswith("#"):
            return True
    return False


def _validate_embedded_svg(svg_text: str) -> ElementTree.Element:
    if re.search(r"<!DOCTYPE\b", svg_text, re.IGNORECASE):
        raise RenderError("unsafe_svg", "HTML preview requires safe local SVG")
    try:
        root = ElementTree.fromstring(svg_text)
    except (ElementTree.ParseError, ValueError):
        raise RenderError("unsafe_svg", "HTML preview requires safe local SVG") from None
    if root.tag != f"{_SVG}svg":
        raise RenderError("unsafe_svg", "HTML preview requires safe local SVG")
    for element in root.iter():
        element_name = _local_name(str(element.tag))
        if element_name in _FORBIDDEN_SVG_ELEMENTS:
            raise RenderError("unsafe_svg", "HTML preview requires safe local SVG")
        for attribute_name, raw_value in element.attrib.items():
            name = _local_name(str(attribute_name))
            value = str(raw_value)
            if name.startswith("on"):
                raise RenderError("unsafe_svg", "HTML preview requires safe local SVG")
            if name in {"href", "src"} and value.strip() and not value.strip().startswith("#"):
                raise RenderError("unsafe_svg", "HTML preview requires safe local SVG")
            if _contains_non_fragment_url(value):
                raise RenderError("unsafe_svg", "HTML preview requires safe local SVG")
        if element_name == "style" and _contains_non_fragment_url(element.text or ""):
            raise RenderError("unsafe_svg", "HTML preview requires safe local SVG")
    return root


def _html_document(svg_text: str) -> str:
    root = _validate_embedded_svg(svg_text)
    ElementTree.register_namespace("", SVG_NS)
    canonical_svg = ElementTree.tostring(
        root, encoding="unicode", short_empty_elements=True
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Ericsson Opportunity Visual</title>"
        "<style>html,body{margin:0;background:#fff}svg{display:block;width:100%;height:auto}</style>"
        "</head><body>"
        + canonical_svg
        + "</body></html>\n"
    )


def write_html(svg_text: str, output_path: Path) -> None:
    """Validate generated SVG and publish a self-contained HTML preview."""

    atomic_write_text(Path(output_path), _html_document(svg_text))


def rasterize_html(
    html_path: Path,
    png_path: Path,
    width: int,
    height: int,
    playwright_factory=None,
) -> None:
    """Capture one local HTML page while aborting every non-file request."""

    width, height = _require_dimensions(width, height)
    html_path = Path(html_path)
    png_path = Path(png_path)
    if png_path.exists() or png_path.is_symlink():
        raise RenderError("output_exists", "Output artifact already exists")
    if playwright_factory is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RasterUnavailable(PNG_UNAVAILABLE_REASON) from None
        playwright_factory = sync_playwright
    try:
        with playwright_factory() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": width, "height": height})

                def local_only(route):
                    if route.request.url.casefold().startswith("file:"):
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", local_only)
                page.goto(html_path.resolve().as_uri(), wait_until="load")
                page.locator("body").screenshot(path=str(png_path))
            finally:
                browser.close()
    except RenderError:
        raise
    except RasterUnavailable:
        raise RasterUnavailable(PNG_UNAVAILABLE_REASON) from None
    except Exception:
        raise RasterUnavailable(PNG_UNAVAILABLE_REASON) from None


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _capability(status: str, reason: str = "") -> dict[str, str]:
    return {"status": status, "reason": reason}


def _probe_chromium() -> dict[str, str]:
    if not _module_available("playwright"):
        return _capability("unavailable", "Playwright package is unavailable")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            browser.close()
    except Exception:
        return _capability("unavailable", "Chromium is unavailable")
    return _capability("available")


def _probe_output_directory_checked(output_dir: Path) -> dict[str, str]:
    output_dir = Path(output_dir)
    try:
        requested_mode = output_dir.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(requested_mode) or not stat.S_ISDIR(requested_mode):
            return _capability("unavailable", "Output directory is not writable")

    missing_depth = 0
    probe_parent = output_dir
    while True:
        try:
            mode = probe_parent.stat().st_mode
        except FileNotFoundError:
            try:
                probe_parent.lstat()
            except FileNotFoundError:
                pass
            else:
                # A broken symlink or a metadata race is not a usable ancestor.
                return _capability("unavailable", "Output directory is not writable")
            if probe_parent.parent == probe_parent:
                return _capability("unavailable", "Output directory is not writable")
            missing_depth += 1
            probe_parent = probe_parent.parent
            continue
        if not stat.S_ISDIR(mode):
            return _capability("unavailable", "Output directory is not writable")
        break

    private_root: Path | None = None
    probe: Path | None = None
    created_dirs: list[Path] = []
    cleanup_ok = True
    try:
        private_root = Path(
            tempfile.mkdtemp(
                prefix=".opportunity-visual-preflight-", dir=probe_parent
            )
        )
        probe_dir = private_root
        for index in range(missing_depth):
            probe_dir = probe_dir / f"nested-{index}"
            probe_dir.mkdir()
            created_dirs.append(probe_dir)
        descriptor, name = tempfile.mkstemp(
            prefix="probe-", dir=probe_dir
        )
        probe = Path(name)
        os.close(descriptor)
    except OSError:
        return _capability("unavailable", "Output directory is not writable")
    finally:
        if probe is not None:
            try:
                probe.unlink()
            except OSError:
                cleanup_ok = False
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                cleanup_ok = False
        if private_root is not None:
            try:
                private_root.rmdir()
            except OSError:
                cleanup_ok = False
    private_root_remains = False
    if private_root is not None:
        try:
            private_root.lstat()
        except FileNotFoundError:
            pass
        else:
            private_root_remains = True
    if not cleanup_ok or private_root_remains:
        return _capability("unavailable", "Output directory probe could not be removed")
    return _capability("available")


def _probe_output_directory(output_dir: Path) -> dict[str, str]:
    """Probe a destination without leaking exceptional filesystem details."""

    try:
        return _probe_output_directory_checked(Path(output_dir))
    except OSError:
        return _capability("unavailable", "Output directory is not writable")


def preflight(output_dir: Path) -> dict[str, object]:
    """Report independent local preparation and rendering capabilities."""

    openpyxl_available = _module_available("openpyxl")
    playwright_available = _module_available("playwright")
    return {
        "csv_json": _capability("available"),
        "xlsx": _capability(
            "available" if openpyxl_available else "unavailable",
            "" if openpyxl_available else "openpyxl is unavailable",
        ),
        "svg_html": _capability("available"),
        "png_package": _capability(
            "available" if playwright_available else "unavailable",
            "" if playwright_available else "Playwright package is unavailable",
        ),
        "chromium": _probe_chromium(),
        "output_directory": _probe_output_directory(Path(output_dir)),
    }


def _sha256_handle(handle: BinaryIO) -> str:
    handle.flush()
    position = handle.tell()
    handle.seek(0)
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    handle.seek(position)
    return digest.hexdigest()


def _remove_owned_name(path: Path, owner_stat: os.stat_result) -> None:
    """Remove only owner_stat's name, restoring a captured competitor safely."""

    quarantine_dir: Path | None = None
    candidate: Path | None = None
    try:
        try:
            quarantine_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{path.name}.rollback-",
                    dir=path.parent,
                )
            )
            candidate = quarantine_dir / "candidate"
            os.rename(path, candidate)
        except OSError:
            return

        candidate_stat = candidate.lstat()
        if os.path.samestat(owner_stat, candidate_stat):
            candidate.unlink()
            return
        try:
            os.link(candidate, path, follow_symlinks=False)
        except FileExistsError:
            return
        try:
            restored_stat = path.lstat()
        except FileNotFoundError:
            return
        if not os.path.samestat(candidate_stat, restored_stat):
            return
        candidate.unlink()
    finally:
        if quarantine_dir is not None:
            try:
                quarantine_dir.rmdir()
            except OSError:
                pass


def _publish_retained_handle(
    handle: BinaryIO,
    stage_path: Path,
    target: Path,
    expected_sha256: str,
    *,
    cleanup_dir: Path | None = None,
) -> _OwnedPublication:
    """Publish stage_path only when it still names the retained regular file."""

    owner_stat = os.fstat(handle.fileno())
    if (
        not stat.S_ISREG(owner_stat.st_mode)
        or _sha256_handle(handle) != expected_sha256
    ):
        raise RenderError("output_changed", "A rendered artifact changed during publication")
    try:
        stage_stat = stage_path.lstat()
    except OSError:
        raise RenderError(
            "output_changed", "A rendered artifact changed during publication"
        ) from None
    if not os.path.samestat(owner_stat, stage_stat):
        raise RenderError("output_changed", "A rendered artifact changed during publication")
    published = False
    try:
        try:
            os.link(stage_path, target)
            published = True
        except FileExistsError:
            raise RenderError("output_exists", "Output artifact already exists") from None
        try:
            visible_stat = target.lstat()
            retained_stat = os.fstat(handle.fileno())
        except OSError:
            raise RenderError(
                "output_changed", "A rendered artifact changed during publication"
            ) from None
        if (
            not stat.S_ISREG(retained_stat.st_mode)
            or not os.path.samestat(owner_stat, retained_stat)
            or not os.path.samestat(retained_stat, visible_stat)
            or _sha256_handle(handle) != expected_sha256
        ):
            raise RenderError(
                "output_changed", "A rendered artifact changed during publication"
            )
        _remove_owned_name(stage_path, retained_stat)
    except BaseException:
        if published:
            _remove_owned_name(target, owner_stat)
        raise
    return _OwnedPublication(
        path=target,
        owner_handle=handle,
        owner_stat=retained_stat,
        stage_path=stage_path,
        expected_sha256=expected_sha256,
        cleanup_dir=cleanup_dir,
    )


def _publish_owned_text(path: Path, text: str) -> _OwnedPublication:
    """Publish without clobbering while retaining the original open stage file."""

    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    encoded = text.encode("utf-8")
    expected_sha256 = hashlib.sha256(encoded).hexdigest()
    if path.exists() or path.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise RenderError("output_exists", "Output artifact already exists")
    handle: BinaryIO | None = None
    try:
        try:
            handle = temporary.open("x+b")
        except FileExistsError:
            raise RenderError("output_exists", "Output artifact already exists") from None
        handle.write(encoded)
        handle.flush()
        return _publish_retained_handle(
            handle,
            temporary,
            path,
            expected_sha256,
        )
    except BaseException as error:
        if handle is not None:
            try:
                owner_stat = os.fstat(handle.fileno())
            except OSError:
                owner_stat = None
            if owner_stat is not None:
                _remove_owned_name(temporary, owner_stat)
            handle.close()
        if isinstance(error, OSError):
            raise RenderError("output_unwritable", "Unable to write render artifacts") from None
        raise


def _release_owner(publication: _OwnedPublication) -> None:
    publication.owner_handle.close()
    _remove_owned_name(publication.stage_path, publication.owner_stat)
    if publication.cleanup_dir is not None:
        try:
            publication.cleanup_dir.rmdir()
        except OSError:
            pass


def _publish_owned_file(
    source: Path,
    target: Path,
    *,
    cleanup_dir: Path | None = None,
) -> _OwnedPublication:
    """Publish an existing regular file without clobbering its target."""

    source = Path(source)
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise RenderError("output_exists", "Output artifact already exists")
    handle: BinaryIO | None = None
    try:
        handle = source.open("rb")
        owner_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(owner_stat.st_mode):
            raise RenderError("output_unwritable", "Unable to write render artifacts")
        expected_sha256 = _sha256_handle(handle)
        return _publish_retained_handle(
            handle,
            source,
            target,
            expected_sha256,
            cleanup_dir=cleanup_dir,
        )
    except BaseException as error:
        if handle is not None:
            try:
                owner_stat = os.fstat(handle.fileno())
            except OSError:
                owner_stat = None
            handle.close()
        if isinstance(error, OSError):
            raise RenderError("output_unwritable", "Unable to write render artifacts") from None
        raise


def _verify_publications(publications: list[_OwnedPublication]) -> None:
    """Require every artifact to retain this transaction's inode and bytes."""

    for publication in publications:
        try:
            owner_stat = os.fstat(publication.owner_handle.fileno())
            visible_stat = publication.path.lstat()
        except OSError:
            raise RenderError(
                "output_changed", "A rendered artifact changed before commit"
            ) from None
        if (
            not stat.S_ISREG(owner_stat.st_mode)
            or not os.path.samestat(owner_stat, visible_stat)
        ):
            raise RenderError(
                "output_changed", "A rendered artifact changed before commit"
            )
        try:
            actual_sha256 = _sha256_handle(publication.owner_handle)
        except OSError:
            raise RenderError(
                "output_changed", "A rendered artifact changed before commit"
            ) from None
        if actual_sha256 != publication.expected_sha256:
            raise RenderError(
                "output_changed", "A rendered artifact changed before commit"
            )


def _rollback_owned(publication: _OwnedPublication) -> None:
    """Remove our visible inode, restoring a replaced competitor without clobbering.

    The private owner hard link supplies durable identity. Moving the visible name
    into a same-directory quarantine atomically captures whichever inode owns that
    name at rollback time. Non-owned content is restored through a no-clobber hard
    link. If another writer wins the restore name, quarantine is deliberately left
    in place rather than deleting either competitor.
    """

    try:
        try:
            owner_stat = os.fstat(publication.owner_handle.fileno())
        except OSError:
            return
        _remove_owned_name(publication.path, owner_stat)
    finally:
        _release_owner(publication)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically publish text without replacing an existing output."""

    publication = _publish_owned_text(path, text)
    _release_owner(publication)


def _load_document(path: Path) -> dict[str, object]:
    def reject_constant(value: str) -> object:
        raise json.JSONDecodeError("Non-standard JSON numeric constant", value, 0)

    try:
        document = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_constant,
        )
    except FileNotFoundError:
        raise RenderError("input_not_found", "Normalized data file was not found") from None
    except (OSError, UnicodeDecodeError):
        raise RenderError("input_unreadable", "Normalized data file could not be read") from None
    except json.JSONDecodeError:
        raise RenderError("invalid_json", "Normalized data JSON is malformed") from None
    return _validate_structure(document, validate_render_values=True)


def _sha256_file(path: Path, manifest_path: Path) -> str:
    path = Path(path)
    if path == manifest_path:
        raise RenderError("invalid_artifact", "Render manifest cannot hash itself")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise RenderError("output_unreadable", "Rendered artifact could not be hashed") from None


def _project_source(source: object) -> dict[str, object]:
    return _validated_source(source)


def _project_exclusions(exclusions: object) -> list[dict[str, object]]:
    if not isinstance(exclusions, list):
        raise RenderError("invalid_document", "Normalized exclusions are invalid")
    projected: list[dict[str, object]] = []
    for item in exclusions:
        if not isinstance(item, dict):
            raise RenderError("invalid_document", "Normalized exclusions are invalid")
        row_id = item.get("id")
        source_row = item.get("source_row")
        code = item.get("code")
        if (
            not isinstance(row_id, str)
            or not _is_positive_int(source_row)
            or not isinstance(code, str)
        ):
            raise RenderError("invalid_document", "Normalized exclusions are invalid")
        projected.append({"id": row_id, "source_row": source_row, "code": code})
    return projected


def _project_warnings(warnings: object) -> list[dict[str, object]]:
    if not isinstance(warnings, list):
        raise RenderError("invalid_document", "Normalized warnings are invalid")
    allowed = ("id", "source_row", "code", "message", "month", "skipped_months")
    projected: list[dict[str, object]] = []
    for warning in warnings:
        if not isinstance(warning, dict) or not isinstance(warning.get("code"), str):
            raise RenderError("invalid_document", "Normalized warnings are invalid")
        projected.append({key: warning[key] for key in allowed if key in warning})
    return projected


def _project_transitions(records: list[dict[str, object]]) -> list[dict[str, object]]:
    transitions: list[dict[str, object]] = []
    for record in records:
        warnings = record.get("warnings", [])
        if not isinstance(warnings, list):
            raise RenderError("invalid_document", "Normalized record warnings are invalid")
        warning_codes = [
            warning["code"]
            for warning in warnings
            if isinstance(warning, dict) and isinstance(warning.get("code"), str)
        ]
        transitions.append(
            {
                "id": record["id"],
                "source_row": record.get("source_row"),
                "months": [
                    {
                        "key": month["key"],
                        "classification": month["classification"],
                        "skipped_months": list(month.get("skipped_months", [])),
                    }
                    for month in record["months"]
                ],
                "warning_codes": warning_codes,
            }
        )
    return transitions


def _manifest_payload(
    document: dict[str, object],
    pages: list[PagePlan],
    artifacts: list[dict[str, object]],
    output_dir: Path,
    width: int,
    height: int,
    png_status: dict[str, str],
    *,
    _verified_digests: list[_VerifiedArtifactDigests] | None = None,
) -> dict[str, object]:
    document = _validate_structure(document, validate_render_values=True)
    width, height = _require_dimensions(width, height)
    output_dir = Path(output_dir)
    manifest_path = output_dir / "render-manifest.json"
    if len(pages) != len(artifacts):
        raise RenderError("invalid_artifact", "Render artifacts do not match page plans")
    if _verified_digests is not None and len(_verified_digests) != len(artifacts):
        raise RenderError("invalid_artifact", "Render artifacts do not match page plans")
    if (
        not isinstance(png_status, dict)
        or not isinstance(png_status.get("status"), str)
        or not isinstance(png_status.get("reason"), str)
    ):
        raise RenderError("invalid_artifact", "PNG status is invalid")
    try:
        semantics_text = json.dumps(
            document.get("semantics", {}), sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        raise RenderError("invalid_document", "Normalized semantics are invalid") from None
    selected_months = document["selected_months"]
    page_entries: list[dict[str, object]] = []
    for index, (page, artifact) in enumerate(zip(pages, artifacts, strict=True)):
        if not isinstance(artifact, dict) or artifact.get("page") != page.number:
            raise RenderError("invalid_artifact", "Render artifacts do not match page plans")
        svg_path = Path(artifact["svg"])
        html_path = Path(artifact["html"])
        png_value = artifact.get("png")
        png_path = Path(png_value) if png_value is not None else None
        verified = (
            _verified_digests[index] if _verified_digests is not None else None
        )
        page_entries.append(
            {
                "number": page.number,
                "month_keys": list(page.month_keys),
                "row_ids": list(page.row_ids),
                "dimensions": {"width": page.width, "height": page.height},
                "files": {
                    "svg": svg_path.name,
                    "html": html_path.name,
                    "png": png_path.name if png_path is not None else None,
                },
                "sha256": {
                    "svg": (
                        verified.svg
                        if verified is not None
                        else _sha256_file(svg_path, manifest_path)
                    ),
                    "html": (
                        verified.html
                        if verified is not None
                        else _sha256_file(html_path, manifest_path)
                    ),
                    "png": (
                        verified.png
                        if verified is not None
                        else _sha256_file(png_path, manifest_path)
                        if png_path is not None
                        else None
                    ),
                },
            }
        )
    records = document["records"]
    return {
        "renderer_version": 1,
        "view": document["view"],
        "range": {
            "start": selected_months[0]["key"],
            "end": selected_months[-1]["key"],
            "months": selected_months,
        },
        "mapping": document.get("mapping", {}),
        "filters": document.get("filters", {}),
        "dimensions": {"width": width, "height": height},
        "semantics_sha256": hashlib.sha256(semantics_text.encode("utf-8")).hexdigest(),
        "source": _project_source(document.get("source")),
        "included_rows": [
            {"id": record["id"], "source_row": record.get("source_row")}
            for record in records
        ],
        "excluded_rows": _project_exclusions(document.get("exclusions", [])),
        "warnings": _project_warnings(document.get("warnings", [])),
        "transitions": _project_transitions(records),
        "pages": page_entries,
        "png": dict(png_status),
    }


def _manifest_text(
    document: dict[str, object],
    pages: list[PagePlan],
    artifacts: list[dict[str, object]],
    output_dir: Path,
    width: int,
    height: int,
    png_status: dict[str, str],
    *,
    _verified_digests: list[_VerifiedArtifactDigests] | None = None,
) -> str:
    payload = _manifest_payload(
        document,
        pages,
        artifacts,
        output_dir,
        width,
        height,
        png_status,
        _verified_digests=_verified_digests,
    )
    try:
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError):
        raise RenderError("invalid_document", "Normalized audit metadata is invalid") from None


def write_render_manifest(
    document: dict[str, object],
    pages: list[PagePlan],
    artifacts: list[dict[str, object]],
    output_dir: Path,
    width: int,
    height: int,
    png_status: dict[str, str],
) -> Path:
    """Write one deterministic audit manifest without hashing the manifest itself."""

    output_dir = Path(output_dir)
    manifest_path = output_dir / "render-manifest.json"
    text = _manifest_text(
        document, pages, artifacts, output_dir, width, height, png_status
    )
    atomic_write_text(manifest_path, text)
    return manifest_path


def _write_svg_pages(
    document: dict[str, object],
    output_dir: Path,
    width: int,
    height: int,
    template_path: Path,
) -> list[str]:
    pages = paginate(document, width, height)
    names = [f"opportunity-visual-p{page.number:02d}.svg" for page in pages]
    output_dir = Path(output_dir)
    if output_dir.is_symlink():
        raise RenderError("output_exists", "Output destination already exists")
    if output_dir.exists() and not output_dir.is_dir():
        raise RenderError("output_exists", "Output destination already exists")
    for name in names:
        target = output_dir / name
        temporary = target.with_name(f".{target.name}.tmp")
        if target.exists() or target.is_symlink() or temporary.exists() or temporary.is_symlink():
            raise RenderError("output_exists", "Output artifact already exists")

    svg_pages = [render_svg_page(document, page, template_path) for page in pages]
    created_directory = not output_dir.exists()
    publications: list[_OwnedPublication] = []
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, svg in zip(names, svg_pages, strict=True):
            target = output_dir / name
            publications.append(_publish_owned_text(target, svg))
    except RenderError:
        for publication in reversed(publications):
            _rollback_owned(publication)
        if created_directory:
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise
    except OSError:
        for publication in reversed(publications):
            _rollback_owned(publication)
        for target in (output_dir / name for name in names):
            temporary = target.with_name(f".{target.name}.tmp")
            try:
                if temporary.exists() or temporary.is_symlink():
                    temporary.unlink()
            except OSError:
                pass
        if created_directory:
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise RenderError("output_unwritable", "Unable to write SVG artifacts") from None
    for publication in publications:
        _release_owner(publication)
    return names


def _validate_planned_outputs(output_dir: Path, targets: list[Path]) -> bool:
    output_dir = Path(output_dir)
    if output_dir.is_symlink():
        raise RenderError("output_exists", "Output destination already exists")
    if output_dir.exists() and not output_dir.is_dir():
        raise RenderError("output_exists", "Output destination already exists")
    for target in targets:
        temporary = target.with_name(f".{target.name}.tmp")
        if (
            target.exists()
            or target.is_symlink()
            or temporary.exists()
            or temporary.is_symlink()
        ):
            raise RenderError("output_exists", "Output artifact already exists")
    return not output_dir.exists()


def _discard_capture(capture_path: Path | None, capture_dir: Path | None) -> None:
    if capture_path is not None:
        try:
            capture_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    if capture_dir is not None:
        try:
            capture_dir.rmdir()
        except OSError:
            pass


def render_document(
    normalized_path: Path,
    output_dir: Path,
    width: int = 1920,
    height: int = 1080,
    png_mode: str = "auto",
) -> dict[str, object]:
    """Render and transactionally publish SVG, HTML, optional PNG, and manifest."""

    if png_mode not in {"auto", "never", "required"}:
        raise RenderError("invalid_png_mode", "PNG mode must be auto, never, or required")
    width, height = _require_dimensions(width, height)
    document = _load_document(Path(normalized_path))
    pages = paginate(document, width, height)
    template = Path(__file__).resolve().parents[1] / "templates/opportunity-visual.svg"
    svg_texts = [render_svg_page(document, page, template) for page in pages]
    html_texts = [_html_document(svg_text) for svg_text in svg_texts]
    output_dir = Path(output_dir)
    svg_paths = [
        output_dir / f"opportunity-visual-p{page.number:02d}.svg" for page in pages
    ]
    html_paths = [
        output_dir / f"opportunity-visual-p{page.number:02d}.html" for page in pages
    ]
    png_paths = [
        output_dir / f"opportunity-visual-p{page.number:02d}.png" for page in pages
    ]
    manifest_path = output_dir / "render-manifest.json"
    planned = [*svg_paths, *html_paths, manifest_path]
    if png_mode != "never":
        planned.extend(png_paths)
    created_directory = _validate_planned_outputs(output_dir, planned)
    publications: list[_OwnedPublication] = []
    artifacts: list[dict[str, object]] = [
        {
            "page": page.number,
            "svg": svg_path,
            "html": html_path,
            "png": None,
        }
        for page, svg_path, html_path in zip(
            pages, svg_paths, html_paths, strict=True
        )
    ]
    svg_digests: list[str] = []
    html_digests: list[str] = []
    png_digests: list[str | None] = [None] * len(artifacts)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for svg_path, svg_text in zip(svg_paths, svg_texts, strict=True):
            publication = _publish_owned_text(svg_path, svg_text)
            publications.append(publication)
            svg_digests.append(publication.expected_sha256)
        for html_path, html_text in zip(html_paths, html_texts, strict=True):
            publication = _publish_owned_text(html_path, html_text)
            publications.append(publication)
            html_digests.append(publication.expected_sha256)

        if png_mode == "never":
            png_status = {"status": "disabled", "reason": "PNG output disabled"}
        else:
            png_publications: list[_OwnedPublication] = []
            try:
                for index, (artifact, html_path, png_path) in enumerate(
                    zip(artifacts, html_paths, png_paths, strict=True)
                ):
                    capture_dir: Path | None = None
                    capture_path: Path | None = None
                    try:
                        capture_dir = Path(
                            tempfile.mkdtemp(
                                prefix=f".{png_path.name}.capture-", dir=output_dir
                            )
                        )
                        capture_path = capture_dir / "capture.png"
                        rasterize_html(
                            html_path,
                            capture_path,
                            width,
                            height,
                        )
                        publication = _publish_owned_file(
                            capture_path,
                            png_path,
                            cleanup_dir=capture_dir,
                        )
                    except BaseException:
                        _discard_capture(capture_path, capture_dir)
                        raise
                    png_publications.append(publication)
                    artifact["png"] = png_path
                    png_digests[index] = publication.expected_sha256
            except RasterUnavailable:
                for publication in reversed(png_publications):
                    _rollback_owned(publication)
                for artifact in artifacts:
                    artifact["png"] = None
                png_digests = [None] * len(artifacts)
                if png_mode == "required":
                    raise RenderError(
                        "png_unavailable",
                        "Install playwright>=1.52 and Chromium for required PNG output",
                    ) from None
                png_status = {
                    "status": "unavailable",
                    "reason": PNG_UNAVAILABLE_REASON,
                }
            except BaseException:
                for publication in reversed(png_publications):
                    _rollback_owned(publication)
                raise
            else:
                publications.extend(png_publications)
                png_status = {"status": "available", "reason": ""}

        _verify_publications(publications)
        verified_digests = [
            _VerifiedArtifactDigests(svg=svg, html=html, png=png)
            for svg, html, png in zip(
                svg_digests, html_digests, png_digests, strict=True
            )
        ]
        manifest_text = _manifest_text(
            document,
            pages,
            artifacts,
            output_dir,
            width,
            height,
            png_status,
            _verified_digests=verified_digests,
        )
        publications.append(_publish_owned_text(manifest_path, manifest_text))
        _verify_publications(publications)
    except RenderError:
        for publication in reversed(publications):
            _rollback_owned(publication)
        if created_directory:
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise
    except OSError:
        for publication in reversed(publications):
            _rollback_owned(publication)
        if created_directory:
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise RenderError("output_unwritable", "Unable to write render artifacts") from None
    except BaseException:
        for publication in reversed(publications):
            _rollback_owned(publication)
        if created_directory:
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise
    for publication in publications:
        _release_owner(publication)
    page_results = [
        {
            "svg": str(artifact["svg"]),
            "html": str(artifact["html"]),
            "png": str(artifact["png"]) if artifact["png"] is not None else None,
        }
        for artifact in artifacts
    ]
    return {
        "ok": True,
        "manifest": str(manifest_path),
        "pages": page_results,
        "png": png_status,
    }


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RenderError("invalid_arguments", "Invalid command arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="render_opportunity_visual.py")
    parser.add_argument("normalized_data", nargs="?", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--png", choices=("auto", "never", "required"), default="auto")
    parser.add_argument("--preflight", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.preflight:
            if args.normalized_data is not None:
                raise RenderError("invalid_arguments", "Invalid command arguments")
            result: dict[str, object] = {
                "ok": True,
                "preflight": preflight(args.output_dir),
            }
        else:
            if args.normalized_data is None:
                raise RenderError("invalid_arguments", "Invalid command arguments")
            result = render_document(
                args.normalized_data,
                args.output_dir,
                args.width,
                args.height,
                args.png,
            )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except RenderError as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": error.code,
                        "message": str(error),
                        "details": error.details,
                    },
                },
                separators=(",", ":"),
            )
        )
        return 2
    except OSError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "output_unwritable",
                        "message": "Unable to write render artifacts",
                        "details": {},
                    },
                },
                separators=(",", ":"),
            )
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "internal_error",
                        "message": "Unexpected renderer failure",
                        "details": {},
                    },
                },
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
