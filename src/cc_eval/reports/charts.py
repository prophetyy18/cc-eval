"""Inline SVG charts for the HTML report.

The charts are drawn server side and inlined into the page: the report stays a
single, self-contained file that works offline, with no chart library and no
CDN.

Two rules shape the design:

* **One axis per chart.**  Metrics live on different scales (recall 0-1,
  cost 0.001, token counts in the thousands), so every metric gets its own
  small multiple instead of sharing one axis.
* **Colour follows the entity.**  "baseline" is always the blue slot and
  "candidate" always the orange slot, in every chart of the report.
"""

from __future__ import annotations

from html import escape

from .model import metric_label

#: Metrics that live on a 0..1 scale.  They are drawn against a fixed
#: maximum of 1.0 so that a change from 0.48 to 1.00 is not exaggerated.
_RATE_SUFFIXES = ("recall", "precision", "f1", "score", "rate", "accuracy")
_RATE_METRICS = {"valid_output"}

#: Colours are CSS custom properties so that the light/dark themes swap in
#: one place and the SVG inherits them.
SERIES_BASELINE = "var(--series-1)"
SERIES_CANDIDATE = "var(--series-2)"
STATUS_GOOD = "var(--status-good)"
STATUS_CRITICAL = "var(--status-critical)"
STATUS_NEUTRAL = "var(--muted)"

_SURFACE = "var(--surface-1)"


def is_rate_metric(metric: str) -> bool:
    """True when the metric is a 0..1 rate."""
    if metric in _RATE_METRICS:
        return True
    return metric.endswith(_RATE_SUFFIXES)


def metric_scale_max(metric: str, *values: float | None) -> float:
    """The upper bound of the axis for a metric."""
    known = [v for v in values if v is not None]
    if is_rate_metric(metric):
        return 1.0
    if not known:
        return 1.0
    biggest = max(known)
    if biggest <= 0:
        return 1.0
    # Round up to a clean number so the axis reads well.
    magnitude = 10 ** max(0, len(str(int(biggest))) - 1)
    return max(magnitude, biggest * 1.15)


def _format_value(value: float | None, metric: str = "", digits: int = 4) -> str:
    """Format a value for a chart label.

    Chart labels are short on purpose: two labels sit next to each other, and
    the precise value is in the metric table below the chart.
    """
    if value is None:
        return "n/a"
    if is_rate_metric(metric):
        return f"{value:.2f}"
    if abs(value) >= 100:
        return f"{value:,.0f}"
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _column_path(x: float, y: float, width: float, height: float, radius: float = 4.0) -> str:
    """A column with rounded top corners and a square baseline."""
    if height <= 0:
        return ""
    radius = min(radius, width / 2, height)
    return (
        f"M{x:.2f},{y + height:.2f} "
        f"L{x:.2f},{y + radius:.2f} "
        f"Q{x:.2f},{y:.2f} {x + radius:.2f},{y:.2f} "
        f"L{x + width - radius:.2f},{y:.2f} "
        f"Q{x + width:.2f},{y:.2f} {x + width:.2f},{y + radius:.2f} "
        f"L{x + width:.2f},{y + height:.2f} Z"
    )


def metric_mini_chart(
    metric: str,
    baseline: float | None,
    candidate: float | None,
    *,
    direction: str = "higher_is_better",
) -> str:
    """One small multiple: the baseline and the candidate value of a metric.

    The two sides are identified by colour; the legend of the section names
    them.  Each bar carries a ``<title>`` so hovering shows the exact value.
    """
    width, height = 240.0, 132.0
    plot_top, plot_bottom = 30.0, 106.0
    bar_width = 26.0
    gap = 12.0
    centre = width / 2
    scale_max = metric_scale_max(metric, baseline, candidate)

    parts: list[str] = [
        f'<svg class="mini-chart" viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        f'aria-label="{escape(metric_label(metric))}: baseline and candidate">'
    ]

    # -- baseline axis ----------------------------------------------------- #
    parts.append(
        f'<line class="axis" x1="6" y1="{plot_bottom:.2f}" x2="{width - 6:.2f}" '
        f'y2="{plot_bottom:.2f}"/>'
    )

    if baseline is None and candidate is None:
        parts.append(
            f'<text class="chart-note" x="{centre:.2f}" y="{(plot_top + plot_bottom) / 2:.2f}" '
            f'text-anchor="middle">no data</text>'
        )
        parts.append("</svg>")
        return "".join(parts)

    # -- the two columns ---------------------------------------------------- #
    for value, x, colour, label in (
        (baseline, centre - bar_width - gap / 2, SERIES_BASELINE, "baseline"),
        (candidate, centre + gap / 2, SERIES_CANDIDATE, "candidate"),
    ):
        if value is None:
            parts.append(
                f'<text class="chart-note" x="{x + bar_width / 2:.2f}" '
                f'y="{plot_bottom - 6:.2f}" text-anchor="middle">n/a</text>'
            )
            continue
        bar_height = max(0.0, (plot_bottom - plot_top) * (abs(value) / scale_max))
        y = plot_bottom - bar_height
        path = _column_path(x, y, bar_width, bar_height)
        if path:
            parts.append(f'<path class="bar" d="{path}" fill="{colour}">')
            parts.append(
                f"<title>{escape(label)}: {escape(_format_value(value, metric, digits=4))}</title>"
            )
            parts.append("</path>")
        parts.append(
            f'<text class="bar-value" x="{x + bar_width / 2:.2f}" '
            f'y="{max(y - 6, 12):.2f}" text-anchor="middle">'
            f"{escape(_format_value(value, metric))}</text>"
        )

    parts.append("</svg>")
    return "".join(parts)


def delta_bar(delta: float | None, max_abs: float, status: str) -> str:
    """A small diverging bar for the per-case delta column."""
    width, height = 120.0, 14.0
    centre = width / 2
    parts = [
        f'<svg class="delta-bar" viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        f'aria-label="delta">'
    ]
    parts.append(
        f'<line class="axis" x1="{centre:.2f}" y1="1" x2="{centre:.2f}" y2="{height - 1:.2f}"/>'
    )
    if delta is None or max_abs <= 0:
        parts.append("</svg>")
        return "".join(parts)
    length = (abs(delta) / max_abs) * (centre - 4)
    if length <= 0:
        parts.append("</svg>")
        return "".join(parts)
    colour = {
        "improved": STATUS_GOOD,
        "regressed": STATUS_CRITICAL,
    }.get(status, STATUS_NEUTRAL)
    x = centre if delta > 0 else centre - length
    parts.append(
        f'<rect class="delta-fill" x="{x:.2f}" y="3" width="{length:.2f}" height="{height - 6:.2f}" '
        f'rx="3" fill="{colour}"/>'
    )
    parts.append("</svg>")
    return "".join(parts)
