"""Stable public forwarding API for integrated plotting."""

from typing import Any


def quick_plot_integrated(*args: Any, **kwargs: Any) -> None:
    """Forward to :func:`cfizz.api.integrated.quick_plot_integrated`.

    The import is intentionally delayed so lightweight Agent validation does not
    load the complete scientific plotting stack.
    """
    from cfizz.api.integrated.quick_plot import quick_plot_integrated as implementation

    implementation(*args, **kwargs)
