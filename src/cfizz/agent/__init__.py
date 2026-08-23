"""Deterministic backend primitives for the conversational CFIZZ Agent."""

from .adapter import CfizzRenderAdapter, RenderRequest, RenderResult
from .capabilities import CapabilityCall, CapabilityRegistry, FigureTargetResolver, TargetSelector
from .figure_spec import FigureSpecValidator, ValidationIssue, ValidationResult
from .inspection import DataInspector, InspectionResult
from .intent import IntentResult, SimpleIntentInterpreter
from .jobs import RenderJob, RenderJobManager
from .patching import AppliedChange, FigurePatchEngine, FigurePatchError, PatchResult
from .session import FigureRevision, FigureSession, FileFigureSessionStore

__all__ = [
    "CfizzRenderAdapter",
    "CapabilityCall",
    "CapabilityRegistry",
    "DataInspector",
    "AppliedChange",
    "FigurePatchEngine",
    "FigurePatchError",
    "FigureRevision",
    "FigureSession",
    "FigureTargetResolver",
    "FigureSpecValidator",
    "FileFigureSessionStore",
    "InspectionResult",
    "IntentResult",
    "PatchResult",
    "RenderRequest",
    "RenderResult",
    "RenderJob",
    "RenderJobManager",
    "SimpleIntentInterpreter",
    "TargetSelector",
    "ValidationIssue",
    "ValidationResult",
]
