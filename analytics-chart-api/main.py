from __future__ import annotations

import base64
import glob
import math
import os
import re
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

app = FastAPI(
    title="Analytics Chart API",
    version="1.1.6",
    description=(
        "Chart rendering tool server for Open WebUI. "
        "It accepts tabular JSON rows produced by an analytics SQL API and returns a chart URL, base64 image, and markdown. "
        "Supported charts include standard charts and a custom performance_table visual for Rank / AC / Delta / DPY% / MS% / EI."
    ),
)

IMAGE_DIR = Path(os.getenv("CHART_IMAGE_DIR", "/tmp/analytics_chart_api"))
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_FORMATS = {"png": "image/png", "svg": "image/svg+xml"}
SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


class ChartRequest(BaseModel):
    chart_type: Literal[
        "line",
        "bar",
        "horizontal_bar",
        "scatter",
        "area",
        "pie",
        "performance_table",
    ] = Field(default="bar", description="Type of chart to render.")

    rows: list[dict[str, Any]] = Field(
        ..., min_length=1, description="Rows from the SQL API response, typically response.rows."
    )

    x: str | None = Field(default=None, description="Column name for X axis or labels.")
    y: str | None = Field(default=None, description="Numeric column name for Y axis or values.")
    series: str | None = Field(default=None, description="Optional column used to split data into multiple series.")

    title: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=240)
    x_label: str | None = Field(default=None, max_length=120)
    y_label: str | None = Field(default=None, max_length=120)
    width: int = Field(default=1100, ge=400, le=2600, description="Image width in pixels.")
    height: int = Field(default=650, ge=300, le=2200, description="Image height in pixels.")
    rotate_x_labels: int = Field(default=0, ge=0, le=90)
    sort_x: bool = Field(default=False, description="Sort X labels lexicographically/numerically where possible.")
    max_rows: int = Field(default=5000, ge=1, le=50000)

    columns: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional mapping for performance_table columns. "
            "Supported keys: label, rank, ac, ac_delta, dpy_pct, ms_pct, ei."
        ),
    )
    total: dict[str, Any] | None = Field(default=None, description="Optional total row for performance_table.")
    sort_by: str | None = Field(
        default=None,
        description="Optional canonical performance_table key or physical column name used for sorting rows.",
    )
    sort_direction: Literal["asc", "desc"] = Field(default="desc")

    image_format: Literal["png", "svg"] = Field(default="png", description="Output image format.")
    delivery: Literal["url", "base64"] = Field(
        default="url",
        description="url stores a short-lived image and returns markdown with an image URL; base64 embeds the image in JSON.",
    )

    @model_validator(mode="after")
    def validate_chart_request(self) -> "ChartRequest":
        if self.chart_type == "performance_table":
            return self
        if not self.x:
            raise ValueError("Field 'x' is required for this chart_type")
        if not self.y:
            raise ValueError("Field 'y' is required for this chart_type")
        if self.chart_type == "pie" and self.series:
            raise ValueError("Pie charts do not support 'series'; use x as labels and y as values")
        return self


class ChartResponse(BaseModel):
    chart_type: str
    row_count: int
    image_format: str
    mime_type: str
    image_url: str | None = None
    image_base64: str | None = None
    markdown: str
    warnings: list[str] = []


def auth_check(authorization: str | None = Header(default=None)) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is missing")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth scheme")

    expected_token = (
        os.environ.get("CHART_BEARER_TOKEN", "").strip()
        or os.environ.get("TOOL_BEARER_TOKEN", "").strip()
    )
    received_token = authorization[len("Bearer ") :].strip()

    if not expected_token:
        raise HTTPException(status_code=500, detail="CHART_BEARER_TOKEN is not configured")
    if received_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid token")


def to_number(value: Any, column_name: str) -> float:
    if value is None:
        raise HTTPException(status_code=400, detail=f"Column '{column_name}' contains null values")
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"Column '{column_name}' must be numeric, got boolean")
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = str(value).strip().replace(" ", "").replace(",", ".")
        try:
            result = float(text)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Column '{column_name}' must be numeric")
    if not math.isfinite(result):
        raise HTTPException(status_code=400, detail=f"Column '{column_name}' contains non-finite values")
    return result


def try_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value_float = float(value)
        return value_float if math.isfinite(value_float) else None
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if text == "":
        return None
    try:
        value_float = float(text)
    except ValueError:
        return None
    return value_float if math.isfinite(value_float) else None


def check_columns(rows: list[dict[str, Any]], columns: list[str]) -> None:
    available = set().union(*(row.keys() for row in rows))
    missing = [col for col in columns if col and col not in available]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"message": "Requested columns are missing", "missing": missing, "available": sorted(available)},
        )


def sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (2, "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, float(value))
    text = str(value)
    try:
        return (0, float(text))
    except ValueError:
        return (1, text)


def cleanup_old_images() -> None:
    ttl_seconds = int(os.getenv("CHART_IMAGE_TTL_SECONDS", "3600"))
    now = time.time()
    for path in IMAGE_DIR.glob("*.*"):
        try:
            if now - path.stat().st_mtime > ttl_seconds:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def get_public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def build_simple_data(body: ChartRequest) -> tuple[list[Any], list[float]]:
    assert body.x is not None
    assert body.y is not None
    rows = body.rows[: body.max_rows]
    labels = [row.get(body.x) for row in rows]
    values = [to_number(row.get(body.y), body.y) for row in rows]
    if body.sort_x:
        combined = sorted(zip(labels, values), key=lambda item: sort_key(item[0]))
        labels = [item[0] for item in combined]
        values = [item[1] for item in combined]
    return labels, values


def build_series_data(body: ChartRequest) -> tuple[list[Any], dict[str, list[float | None]]]:
    assert body.x is not None
    assert body.y is not None
    assert body.series is not None
    rows = body.rows[: body.max_rows]
    x_values: list[Any] = []
    x_seen = set()
    series_values: list[str] = []
    series_seen = set()
    point_map: dict[tuple[Any, str], float] = {}
    for row in rows:
        x_value = row.get(body.x)
        series_value = str(row.get(body.series))
        y_value = to_number(row.get(body.y), body.y)
        if x_value not in x_seen:
            x_seen.add(x_value)
            x_values.append(x_value)
        if series_value not in series_seen:
            series_seen.add(series_value)
            series_values.append(series_value)
        key = (x_value, series_value)
        point_map[key] = point_map.get(key, 0.0) + y_value
    if body.sort_x:
        x_values = sorted(x_values, key=sort_key)
    result: dict[str, list[float | None]] = {}
    for series_value in series_values:
        result[series_value] = [point_map.get((x_value, series_value)) for x_value in x_values]
    return x_values, result


def fmt_compact(value: float | None) -> str:
    if value is None:
        return "-"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.0f}K"
    if abs_value >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}"


def fmt_delta(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{fmt_compact(value)}"


def fmt_pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "-"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.1f}%"


def fmt_int(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}"


def color_positive_negative(
    value: float | None,
    positive: str = "#76b900",
    negative: str = "#e00012",
    neutral: str = "#8c8c8c",
) -> str:
    if value is None or abs(value) < 1e-12:
        return neutral
    return positive if value > 0 else negative


def get_mapped_column(body: ChartRequest, canonical: str, defaults: list[str]) -> str | None:
    if body.columns and body.columns.get(canonical):
        return body.columns[canonical]
    available = set().union(*(row.keys() for row in body.rows))
    if body.total:
        available.update(body.total.keys())
    for candidate in defaults:
        if candidate in available:
            return candidate
    return defaults[0] if defaults else None


def get_performance_mapping(body: ChartRequest) -> dict[str, str | None]:
    return {
        "label": get_mapped_column(body, "label", ["label", "period", "month", "date", "category", "name"]),
        "rank": get_mapped_column(body, "rank", ["rank", "Rank"]),
        "ac": get_mapped_column(body, "ac", ["ac", "AC", "value", "sales", "amount"]),
        "ac_delta": get_mapped_column(body, "ac_delta", ["ac_delta", "delta", "change", "abs_delta", "growth_abs"]),
        "dpy_pct": get_mapped_column(body, "dpy_pct", ["dpy_pct", "dpy", "growth_pct", "yoy_pct", "change_pct"]),
        "ms_pct": get_mapped_column(body, "ms_pct", ["ms_pct", "ms", "share_pct", "market_share_pct"]),
        "ei": get_mapped_column(body, "ei", ["ei", "EI", "index"]),
    }


def validate_performance_table_metrics(body: ChartRequest) -> None:
    if body.chart_type != "performance_table":
        return
    rows = body.rows[: body.max_rows]
    if not rows:
        raise HTTPException(status_code=400, detail="performance_table requires rows")
    required_metrics = ["label", "rank", "ac", "ac_delta", "dpy_pct", "ms_pct", "ei"]
    numeric_metrics = ["ac", "ac_delta", "dpy_pct", "ms_pct", "ei"]
    mapping = get_performance_mapping(body)
    available = set().union(*(row.keys() for row in rows))
    if body.total:
        available.update(body.total.keys())
    missing_metrics = []
    for metric in required_metrics:
        column = mapping.get(metric)
        if not column or column not in available:
            missing_metrics.append(metric)
    if missing_metrics:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "performance_table requires full metric set. "
                    "Rebuild SQL and provide label, rank, ac, ac_delta, dpy_pct, ms_pct and ei. "
                    "Do not call Chart Tool with only current-period sales."
                ),
                "missing_metrics": missing_metrics,
                "available_columns": sorted(available),
                "required_metrics": required_metrics,
                "example_columns_mapping": {
                    "label": "Виробник",
                    "rank": "Ранг",
                    "ac": "Продажі, грн",
                    "ac_delta": "Delta PY",
                    "dpy_pct": "Delta PY%",
                    "ms_pct": "MS%",
                    "ei": "EI",
                },
            },
        )
    empty_metrics = []
    non_numeric_metrics = []
    for metric in numeric_metrics:
        column = mapping[metric]
        values = [row.get(column) for row in rows]
        parsed_values = [try_number(value) for value in values]
        if all(value is None or str(value).strip() == "" for value in values):
            empty_metrics.append(metric)
            continue
        if any(value is not None and str(value).strip() != "" and parsed is None for value, parsed in zip(values, parsed_values)):
            non_numeric_metrics.append(metric)
            continue
        if all(parsed is None for parsed in parsed_values):
            empty_metrics.append(metric)
    if empty_metrics or non_numeric_metrics:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "performance_table metric columns must be numeric and non-empty. "
                    "Rebuild SQL with previous comparable period and total market calculations before calling Chart Tool."
                ),
                "empty_metrics": empty_metrics,
                "non_numeric_metrics": non_numeric_metrics,
                "required_numeric_metrics": numeric_metrics,
            },
        )


def row_text(row: dict[str, Any], column: str | None, fallback: str = "-") -> str:
    if not column:
        return fallback
    value = row.get(column)
    if value is None or str(value).strip() == "":
        return fallback
    return str(value)


def row_number(row: dict[str, Any], column: str | None) -> float | None:
    if not column:
        return None
    return try_number(row.get(column))


def sort_performance_rows(body: ChartRequest, rows: list[dict[str, Any]], mapping: dict[str, str | None]) -> list[dict[str, Any]]:
    if not body.sort_by:
        return rows
    sort_col = mapping.get(body.sort_by) or body.sort_by
    if not sort_col:
        return rows
    reverse = body.sort_direction == "desc"

    def key(row: dict[str, Any]) -> tuple[int, Any]:
        num = try_number(row.get(sort_col))
        if num is not None:
            return (0, num)
        return sort_key(row.get(sort_col))

    return sorted(rows, key=key, reverse=reverse)


def render_performance_table(body: ChartRequest) -> bytes:
    rows = body.rows[: body.max_rows]
    mapping = get_performance_mapping(body)
    if not mapping["label"]:
        raise HTTPException(status_code=400, detail="performance_table requires a label column")
    if not mapping["ac"]:
        raise HTTPException(status_code=400, detail="performance_table requires an AC/value column")

    rows = sort_performance_rows(body, rows, mapping)
    ac_values = [row_number(row, mapping["ac"]) for row in rows]
    delta_values = [row_number(row, mapping["ac_delta"]) for row in rows]
    dpy_values = [row_number(row, mapping["dpy_pct"]) for row in rows]
    if body.total:
        ac_values.append(row_number(body.total, mapping["ac"]))
        delta_values.append(row_number(body.total, mapping["ac_delta"]))
        dpy_values.append(row_number(body.total, mapping["dpy_pct"]))

    max_ac = max([abs(v) for v in ac_values if v is not None] or [1.0])
    max_delta = max([abs(v) for v in delta_values if v is not None] or [1.0])
    max_dpy = max([abs(v) for v in dpy_values if v is not None] or [1.0])
    max_dpy = max(max_dpy, 1.0)

    row_count = len(rows)
    dpi = 100
    dynamic_height = max(360, 170 + row_count * 39 + (45 if body.total else 0))
    figsize = (body.width / dpi, dynamic_height / dpi)
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")

    row_step = 0.62
    total_units = row_count * row_step + 3.25 + (0.95 if body.total else 0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, total_units)

    x_label = 0.02
    label_clip_right = 0.205
    x_rank = 0.235
    ac_left = 0.295
    ac_right = 0.56
    dpy_left = 0.665
    dpy_right = 0.79
    ms_x = 0.88
    ei_x = 0.96

    blue = "#4f8bbd"
    green = "#76b900"
    red = "#e00012"
    dark = "#111111"

    label_clip_width = max(label_clip_right - x_label, 0.05)
    label_max_chars = max(12, int(label_clip_width * body.width / 7.2))

    def shorten_label(value: str) -> str:
        text = str(value)
        if len(text) <= label_max_chars:
            return text
        return text[: max(label_max_chars - 3, 1)].rstrip() + "..."

    def add_label_text(x: float, y: float, text: str, *, is_total: bool = False) -> None:
        clipped_text = shorten_label(text) if not is_total else text
        text_artist = ax.text(
            x,
            y,
            clipped_text,
            ha="left",
            va="center",
            fontsize=10.0,
            fontweight="bold",
            color=dark,
            clip_on=True,
        )
        clip_rect = plt.Rectangle(
            (x_label, y - 0.30),
            label_clip_width,
            0.60,
            transform=ax.transData,
            facecolor="none",
            edgecolor="none",
        )
        ax.add_patch(clip_rect)
        clip_rect.set_visible(False)
        text_artist.set_clip_path(clip_rect)

    y_title = total_units - 0.45
    title = body.title or "Performance table"
    ax.text(x_label, y_title, title, ha="left", va="center", fontsize=13, fontweight="bold", color=dark)
    if body.subtitle:
        ax.text(x_label, y_title - 0.32, body.subtitle, ha="left", va="center", fontsize=9.3, color="#3a3a3a")

    y_header = total_units - 1.20
    ax.text(x_rank, y_header, "Rank", ha="center", va="center", fontsize=10.2, fontweight="bold", color=dark)
    ax.text(ac_left, y_header, "AC ↓", ha="left", va="center", fontsize=10.2, fontweight="bold", color=dark)
    ax.text((dpy_left + dpy_right) / 2, y_header, "DPY%", ha="center", va="center", fontsize=10.2, fontweight="bold", color=dark)
    ax.text(ms_x, y_header, "MS%", ha="center", va="center", fontsize=10.2, fontweight="bold", color=dark)
    ax.text(ei_x, y_header, "EI", ha="center", va="center", fontsize=10.2, fontweight="bold", color=dark)

    zero_x = (dpy_left + dpy_right) / 2
    table_top = y_header - 0.33
    table_bottom = 0.95 if body.total else 0.35
    ax.plot([zero_x, zero_x], [table_bottom, table_top], color="#9e9e9e", linewidth=0.8)

    def draw_one_row(row: dict[str, Any], y: float, index: int, is_total: bool = False) -> None:
        label = row_text(row, mapping["label"], "TOTAL" if is_total else "-")
        rank_value = row_number(row, mapping["rank"])
        ac_value = row_number(row, mapping["ac"])
        delta_value = row_number(row, mapping["ac_delta"])
        dpy_value = row_number(row, mapping["dpy_pct"])
        ms_value = row_number(row, mapping["ms_pct"])
        ei_value = row_number(row, mapping["ei"])

        font_weight = "bold" if is_total else "normal"
        add_label_text(x_label, y, label, is_total=is_total)

        if not is_total:
            rank_display = fmt_int(rank_value if rank_value is not None else float(index + 1))
            ax.text(
                x_rank,
                y,
                rank_display,
                ha="center",
                va="center",
                fontsize=9.3,
                color=dark,
                zorder=10,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0},
            )

        current_width = 0.0
        delta_label_left_guard = 0.0
        if ac_value is not None:
            current_width = (abs(ac_value) / max_ac) * (ac_right - ac_left)
            current_width = max(current_width, 0.006)
            bar_height = 0.38 if not is_total else 0.05
            if not is_total:
                ax.add_patch(
                    plt.Rectangle(
                        (ac_left, y - bar_height / 2),
                        current_width,
                        bar_height,
                        facecolor=blue,
                        edgecolor="none",
                        zorder=2,
                    )
                )
                ac_text = fmt_compact(ac_value)
                if current_width >= 0.12:
                    ac_fontsize = 8.8
                elif current_width >= 0.09:
                    ac_fontsize = 8.0
                elif current_width >= 0.07:
                    ac_fontsize = 7.2
                else:
                    ac_fontsize = 6.6

                ac_text_artist = ax.text(
                    ac_left + current_width / 2,
                    y,
                    ac_text,
                    ha="center",
                    va="center",
                    fontsize=ac_fontsize,
                    color="white",
                    fontweight="bold",
                    zorder=8,
                    clip_on=True,
                )
                ac_clip_rect = plt.Rectangle(
                    (ac_left, y - bar_height / 2),
                    current_width,
                    bar_height,
                    transform=ax.transData,
                    facecolor="none",
                    edgecolor="none",
                )
                ax.add_patch(ac_clip_rect)
                ac_clip_rect.set_visible(False)
                ac_text_artist.set_clip_path(ac_clip_rect)
                delta_label_left_guard = ac_left + current_width + 0.006
            else:
                ax.text(ac_left, y, fmt_compact(ac_value), ha="left", va="center", fontsize=10, color=dark, fontweight="bold")
                delta_label_left_guard = ac_left + 0.075

        if delta_value is not None:
            if not is_total and ac_value is not None:
                delta_bar_height = 0.19
                if delta_value > 0:
                    previous_value = max(ac_value - delta_value, 0)
                    previous_width = (abs(previous_value) / max_ac) * (ac_right - ac_left)
                    previous_width = min(max(previous_width, 0.0), current_width)
                    growth_width = current_width - previous_width
                    if growth_width > 0:
                        growth_width = max(growth_width, 0.006)
                        growth_width = min(growth_width, current_width)
                        growth_x = ac_left + max(current_width - growth_width, 0.0)
                        ax.add_patch(
                            plt.Rectangle(
                                (growth_x, y - delta_bar_height / 2),
                                growth_width,
                                delta_bar_height,
                                facecolor=green,
                                edgecolor="none",
                                zorder=5,
                            )
                        )
                    delta_text_x = max(ac_left + current_width + 0.006, delta_label_left_guard)
                    ax.text(
                        delta_text_x,
                        y,
                        fmt_delta(delta_value),
                        ha="left",
                        va="center",
                        fontsize=8.8,
                        color=dark,
                        bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.4},
                        zorder=9,
                    )
                elif delta_value < 0:
                    delta_width = min(0.065, max(0.006, abs(delta_value) / max_delta * 0.065))
                    delta_x = ac_left + current_width
                    ax.add_patch(
                        plt.Rectangle(
                            (delta_x, y - delta_bar_height / 2),
                            delta_width,
                            delta_bar_height,
                            facecolor=red,
                            edgecolor="none",
                            zorder=5,
                        )
                    )
                    delta_text_x = max(delta_x + delta_width + 0.006, delta_label_left_guard)
                    ax.text(
                        delta_text_x,
                        y,
                        fmt_delta(delta_value),
                        ha="left",
                        va="center",
                        fontsize=8.8,
                        color=dark,
                        bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.4},
                        zorder=9,
                    )
                else:
                    delta_text_x = max(ac_left + current_width + 0.006, delta_label_left_guard)
                    ax.text(
                        delta_text_x,
                        y,
                        fmt_delta(delta_value),
                        ha="left",
                        va="center",
                        fontsize=8.8,
                        color=dark,
                        bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.4},
                        zorder=9,
                    )
            else:
                ax.text(
                    ac_left + 0.075,
                    y,
                    f"|  {fmt_delta(delta_value)}",
                    ha="left",
                    va="center",
                    fontsize=10,
                    color=dark,
                    fontweight="bold",
                )

        if dpy_value is not None:
            half_width = (dpy_right - dpy_left) / 2
            point_x = zero_x + max(-1.0, min(1.0, dpy_value / max_dpy)) * half_width
            dpy_color = color_positive_negative(dpy_value, positive=green, negative=red, neutral="#8c8c8c")
            ax.plot([zero_x, point_x], [y, y], color=dpy_color, linewidth=1.2)
            ax.scatter([point_x], [y], s=30, color=dpy_color, zorder=5)
            if dpy_value >= 0:
                ax.text(point_x + 0.010, y, fmt_pct(dpy_value, signed=True), ha="left", va="center", fontsize=8.8, color=dark, fontweight=font_weight)
            else:
                ax.text(point_x - 0.010, y, fmt_pct(dpy_value, signed=False), ha="right", va="center", fontsize=8.8, color=dark, fontweight=font_weight)

        ax.text(ms_x, y, fmt_pct(ms_value), ha="center", va="center", fontsize=9.3, color=dark, fontweight=font_weight)
        ei_color = green if (ei_value is not None and ei_value >= 100) else red
        ax.text(ei_x, y, fmt_int(ei_value), ha="center", va="center", fontsize=9.8, color=ei_color if not is_total else dark, fontweight="bold")

    start_y = y_header - 0.54
    for index, row in enumerate(rows):
        y = start_y - index * row_step
        draw_one_row(row, y, index)
        if index > 0 and (index + 1) % 5 == 0:
            sep_y = y - 0.30
            ax.plot([0.0, 1.0], [sep_y, sep_y], color="#eeeeee", linewidth=0.8)

    if body.total:
        total_y = 0.45
        ax.plot([0.0, 1.0], [total_y + 0.42, total_y + 0.42], color="#dddddd", linewidth=1.0)
        total_row = {"label": "TOTAL", **body.total}
        draw_one_row(total_row, total_y, row_count, is_total=True)

    fig.subplots_adjust(left=0.01, right=0.99, top=0.98, bottom=0.02)
    buffer = BytesIO()
    fig.savefig(buffer, format=body.image_format, bbox_inches="tight", pad_inches=0.08, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buffer.getvalue()


def render_standard_chart(body: ChartRequest) -> bytes:
    assert body.x is not None
    assert body.y is not None
    check_columns(body.rows, [body.x, body.y or "", body.series or ""])
    dpi = 100
    figsize = (body.width / dpi, body.height / dpi)
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)

    if body.chart_type == "pie":
        labels, values = build_simple_data(body)
        ax.pie(values, labels=[str(label) for label in labels], autopct="%1.1f%%")
        ax.axis("equal")
    elif body.series:
        x_values, series_data = build_series_data(body)
        x_positions = np.arange(len(x_values))
        if body.chart_type == "bar":
            series_names = list(series_data.keys())
            width = min(0.8 / max(len(series_names), 1), 0.35)
            offset_start = -((len(series_names) - 1) * width) / 2
            for index, series_name in enumerate(series_names):
                values = [0 if value is None else value for value in series_data[series_name]]
                ax.bar(x_positions + offset_start + index * width, values, width=width, label=series_name)
            ax.set_xticks(x_positions)
            ax.set_xticklabels([str(value) for value in x_values])
        elif body.chart_type == "horizontal_bar":
            raise HTTPException(status_code=400, detail="horizontal_bar does not support series")
        else:
            for series_name, values in series_data.items():
                y_values = [np.nan if value is None else value for value in values]
                if body.chart_type == "line":
                    ax.plot(x_positions, y_values, marker="o", label=series_name)
                elif body.chart_type == "scatter":
                    ax.scatter(x_positions, y_values, label=series_name)
                elif body.chart_type == "area":
                    ax.plot(x_positions, y_values, marker="o")
                    ax.fill_between(x_positions, y_values, alpha=0.25)
            ax.set_xticks(x_positions)
            ax.set_xticklabels([str(value) for value in x_values])
        ax.legend()
    else:
        labels, values = build_simple_data(body)
        x_positions = np.arange(len(labels))
        if body.chart_type == "line":
            ax.plot(x_positions, values, marker="o")
            ax.set_xticks(x_positions)
            ax.set_xticklabels([str(label) for label in labels])
        elif body.chart_type == "bar":
            ax.bar(x_positions, values)
            ax.set_xticks(x_positions)
            ax.set_xticklabels([str(label) for label in labels])
        elif body.chart_type == "horizontal_bar":
            ax.barh(x_positions, values)
            ax.set_yticks(x_positions)
            ax.set_yticklabels([str(label) for label in labels])
        elif body.chart_type == "scatter":
            ax.scatter(x_positions, values)
            ax.set_xticks(x_positions)
            ax.set_xticklabels([str(label) for label in labels])
        elif body.chart_type == "area":
            ax.plot(x_positions, values, marker="o")
            ax.fill_between(x_positions, values, alpha=0.25)
            ax.set_xticks(x_positions)
            ax.set_xticklabels([str(label) for label in labels])

    if body.title:
        ax.set_title(body.title)
    if body.chart_type != "pie":
        ax.set_xlabel(body.x_label or body.x)
        ax.set_ylabel(body.y_label or body.y or "")
        ax.grid(True, axis="y", alpha=0.25)
        if body.rotate_x_labels:
            plt.setp(ax.get_xticklabels(), rotation=body.rotate_x_labels, ha="right")
    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format=body.image_format, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def render_chart(body: ChartRequest) -> bytes:
    if body.chart_type == "performance_table":
        return render_performance_table(body)
    return render_standard_chart(body)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/charts/render", response_model=ChartResponse, dependencies=[Depends(auth_check)])
def charts_render(body: ChartRequest, request: Request) -> ChartResponse:
    cleanup_old_images()
    validate_performance_table_metrics(body)
    image_bytes = render_chart(body)
    mime_type = SUPPORTED_FORMATS[body.image_format]
    if body.delivery == "base64":
        encoded = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{mime_type};base64,{encoded}"
        return ChartResponse(
            chart_type=body.chart_type,
            row_count=min(len(body.rows), body.max_rows),
            image_format=body.image_format,
            mime_type=mime_type,
            image_base64=encoded,
            markdown=f"![{body.title or 'chart'}]({data_uri})",
        )
    chart_id = uuid.uuid4().hex
    token = uuid.uuid4().hex
    filename = f"{chart_id}_{token}.{body.image_format}"
    file_path = IMAGE_DIR / filename
    file_path.write_bytes(image_bytes)
    base_url = get_public_base_url(request)
    image_url = f"{base_url}/charts/image/{chart_id}?token={token}"
    return ChartResponse(
        chart_type=body.chart_type,
        row_count=min(len(body.rows), body.max_rows),
        image_format=body.image_format,
        mime_type=mime_type,
        image_url=image_url,
        markdown=f"![{body.title or 'chart'}]({image_url})",
    )


@app.get("/charts/image/{chart_id}")
def chart_image(chart_id: str, token: str = Query(..., min_length=32, max_length=32)) -> FileResponse:
    if not SAFE_ID.match(chart_id) or not SAFE_ID.match(token):
        raise HTTPException(status_code=404, detail="Chart image not found")
    matches = glob.glob(str(IMAGE_DIR / f"{chart_id}_{token}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Chart image not found")
    path = Path(matches[0])
    ttl_seconds = int(os.getenv("CHART_IMAGE_TTL_SECONDS", "3600"))
    if time.time() - path.stat().st_mtime > ttl_seconds:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=410, detail="Chart image expired")
    suffix = path.suffix.lower().lstrip(".")
    media_type = SUPPORTED_FORMATS.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=f"chart.{suffix}")
