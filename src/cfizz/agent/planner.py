"""Safe natural-language planning for conversational figure edits.

The model never receives file paths or raw scientific data and never emits
executable code. Its small, typed edit vocabulary is compiled into the same
FigurePatch protocol used by the deterministic interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import inspect
import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .capabilities import CapabilityCall, CapabilityRegistry
from .intent import IntentResult, SimpleIntentInterpreter
from .parameters import ParameterCatalog, ParameterEdit
from .figure_types import FIGURE_TYPE_BY_ID, figure_type_catalog


Action = Literal["patch", "clarify", "answer", "undo", "redo", "render", "reference", "workflow"]
EditType = Literal[
    "set_viewport",
    "set_resolution",
    "set_normalization",
    "set_balance",
    "set_layer_color",
    "set_layer_colormap",
    "set_layer_visibility",
    "set_layer_label",
    "set_layer_font_size",
    "set_panel_height",
    "set_panel_label",
    "set_layout_width",
    "set_layout_gap",
    "set_layout_left_margin",
    "set_layout_right_margin",
    "set_figure_font_size",
    "set_figure_title",
]


class PlannedEdit(BaseModel):
    """One constrained edit. Unused value slots must be returned as null."""

    model_config = ConfigDict(extra="forbid")

    edit_type: EditType
    target_id: Optional[str] = None
    chrom: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    number_value: Optional[float] = None
    string_value: Optional[str] = None
    bool_value: Optional[bool] = None


class ReferenceRequest(BaseModel):
    """Semantic reference-data request selected by AI, resolved locally."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["locate_gene", "annotate_gene", "annotate_region_genes"]
    gene_symbol: Optional[str] = Field(default=None, max_length=120)


class WorkflowRequest(BaseModel):
    """Select a registered CFIZZ visualization workflow, never executable code."""

    model_config = ConfigDict(extra="forbid")

    figure_type: str = Field(max_length=120)
    source_ids: List[str] = Field(default_factory=list, max_length=100)
    options: Dict[str, Any] = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    """Structured output requested from the model."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    reply: str
    edits: List[PlannedEdit] = Field(default_factory=list)
    parameter_edits: List[ParameterEdit] = Field(default_factory=list)
    calls: List[CapabilityCall] = Field(default_factory=list)
    reference_request: Optional[ReferenceRequest] = None
    workflow_request: Optional[WorkflowRequest] = None


class FigurePlanner(Protocol):
    def interpret(self, message: str, spec: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        ...

    def status(self) -> Dict[str, Any]:
        ...


log = logging.getLogger(__name__)


@dataclass
class RulePlanner:
    interpreter: SimpleIntentInterpreter
    reason: Optional[str] = None

    def interpret(self, message: str, spec: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        return self.interpreter.interpret(message, spec)

    async def interpret_async(self, message: str, spec: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        return self.interpret(message, spec, history=history)

    def status(self) -> Dict[str, Any]:
        return {
            "mode": "rules",
            "provider": "local",
            "model": None,
            "reason": self.reason,
            "privacy": "本地解析；不会发送数据到外部模型。",
        }


class OpenAIPlanner:
    """OpenAI Responses API planner using SDK-native structured parsing."""

    def __init__(
        self,
        client: Any = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: str = "openai",
    ):
        self.provider = provider
        self.model = model or os.environ.get("CFIZZ_AGENT_MODEL", "gpt-5.6-terra")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - exercised through builder
                raise RuntimeError("未安装 openai；请安装 cfizz[agent]。") from exc
            client = OpenAI(api_key=api_key)
        self.client = client

    def interpret(self, message: str, spec: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        response = self._request(message, spec, history=history)
        if inspect.isawaitable(response):
            raise RuntimeError("异步 OpenAI 客户端需要调用 interpret_async。")
        try:
            return self._compile_response(response, spec)
        except ValueError as exc:
            repaired = self._request(message, spec, history=history, validation_feedback=_repair_feedback(exc))
            if inspect.isawaitable(repaired):
                raise RuntimeError("异步 OpenAI 客户端需要调用 interpret_async。")
            return self._compile_response(repaired, spec)

    async def interpret_async(self, message: str, spec: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        response = self._request(message, spec, history=history)
        if inspect.isawaitable(response):
            response = await response
        try:
            return self._compile_response(response, spec)
        except ValueError as exc:
            repaired = self._request(message, spec, history=history, validation_feedback=_repair_feedback(exc))
            if inspect.isawaitable(repaired):
                repaired = await repaired
            return self._compile_response(repaired, spec)

    def _request(
        self,
        message: str,
        spec: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None,
        validation_feedback: Optional[str] = None,
    ):
        payload = {
            "request": public_dialogue_text(message),
            "recent_dialogue": public_dialogue_context(history or []),
            "current_figure": public_figure_context(spec),
            "available_capabilities": CapabilityRegistry().model_catalog(),
            "editable_parameters": ParameterCatalog(spec).model_catalog(),
            "available_visualizations": figure_type_catalog(),
        }
        if validation_feedback:
            payload["previous_plan_error"] = validation_feedback
            payload["repair_instruction"] = (
                "根据 previous_plan_error 修正上一份计划并重新输出；只能使用目录中真实存在的参数和目标。"
                "如果 action=patch，必须提供至少一个具体 edit、parameter_edit 或 capability call，且 reply 必须与实际参数值一致。"
            )
        return self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            text_format=PlannerOutput,
        )

    def _compile_response(self, response: Any, spec: Dict[str, Any]) -> IntentResult:
        output = response.output_parsed
        if output is None:
            raise ValueError("模型没有返回可解析的图形编辑计划。")
        return compile_plan(output, spec, planner=f"{self.provider}:{self.model}")

    def status(self) -> Dict[str, Any]:
        return {
            "mode": "ai-assisted",
            "provider": self.provider,
            "model": self.model,
            "reason": None,
            "privacy": "仅发送脱敏后的图形结构和当前参数，不发送文件路径或原始数据。",
        }


class DeepSeekPlanner(OpenAIPlanner):
    """DeepSeek planner through its OpenAI-compatible Chat Completions API."""

    def __init__(self, client: Any = None, model: Optional[str] = None, api_key: Optional[str] = None):
        model = model or os.environ.get("CFIZZ_DEEPSEEK_MODEL", "deepseek-chat")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - exercised through builder
                raise RuntimeError("未安装 openai；请安装 cfizz[agent]。") from exc
            client = OpenAI(
                api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
                base_url=os.environ.get("CFIZZ_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            )
        super().__init__(client=client, model=model, provider="deepseek")

    def _request(
        self,
        message: str,
        spec: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None,
        validation_feedback: Optional[str] = None,
    ):
        schema = PlannerOutput.model_json_schema()
        system = (
            _SYSTEM_PROMPT
            + "\n只输出一个符合下列 JSON Schema 的 JSON 对象，不要 Markdown：\n"
            + json.dumps(schema, ensure_ascii=False)
        )
        payload = {
            "request": public_dialogue_text(message),
            "recent_dialogue": public_dialogue_context(history or []),
            "current_figure": public_figure_context(spec),
            "available_capabilities": CapabilityRegistry().model_catalog(),
            "editable_parameters": ParameterCatalog(spec).model_catalog(),
            "available_visualizations": figure_type_catalog(),
        }
        if validation_feedback:
            payload["previous_plan_error"] = validation_feedback
            payload["repair_instruction"] = (
                "根据 previous_plan_error 修正上一份计划并重新输出；只能使用目录中真实存在的参数和目标。"
                "如果 action=patch，必须提供至少一个具体 edit、parameter_edit 或 capability call，且 reply 必须与实际参数值一致。"
            )
        return self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            response_format={"type": "json_object"},
        )

    def _compile_response(self, response: Any, spec: Dict[str, Any]) -> IntentResult:
        try:
            content = response.choices[0].message.content
            output = PlannerOutput.model_validate_json(content)
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("DeepSeek 没有返回可解析的图形编辑计划。") from exc
        return compile_plan(output, spec, planner=f"{self.provider}:{self.model}")


class RuleFirstPlanner:
    """Use AI for semantics, retaining deterministic shortcuts and fallback.

    The historical class name is kept for API compatibility.  Once an AI
    provider is connected, ordinary natural-language requests go to the model
    first; only exact transport-like commands stay local.
    """

    def __init__(self, ai_planner: FigurePlanner, rules: Optional[SimpleIntentInterpreter] = None):
        self.ai_planner = ai_planner
        self.rules = rules or SimpleIntentInterpreter()

    def interpret(self, message: str, spec: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        direct = self._direct_answer(message)
        if direct is not None:
            return direct
        local = self.rules.interpret(message, spec)
        if self._is_local_shortcut(message, local):
            return local
        try:
            return self.ai_planner.interpret(message, spec, history=history)
        except Exception as exc:
            return self._fallback(local, exc)

    async def interpret_async(self, message: str, spec: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> IntentResult:
        direct = self._direct_answer(message)
        if direct is not None:
            return direct
        local = self.rules.interpret(message, spec)
        if self._is_local_shortcut(message, local):
            return local
        try:
            method = getattr(self.ai_planner, "interpret_async", None)
            if method is not None:
                return await method(message, spec, history=history)
            return self.ai_planner.interpret(message, spec, history=history)
        except Exception as exc:
            return self._fallback(local, exc)

    def status(self) -> Dict[str, Any]:
        status = dict(self.ai_planner.status())
        status["routing"] = "ai-first"
        return status

    @staticmethod
    def _is_local_shortcut(message: str, local: IntentResult) -> bool:
        compact = re.sub(r"\s+", "", message).lower()
        exact_commands = {
            "撤销", "undo", "退回上一版", "回到上一版",
            "重做", "redo", "恢复下一版",
            "重新画", "重新渲染", "render", "出图",
        }
        assay_axis_request = (
            any(token in compact for token in ("y轴", "纵轴", "y-axis", "yaxis"))
            and any(token in compact for token in ("测序技术", "技术类型", "测序类型", "每种技术", "每类", "各类", "同类型", "同一种"))
            and local.action == "patch"
        )
        track_order_request = (
            local.action == "patch"
            and bool(local.patch)
            and any(operation.get("op") == "move_layer" for operation in local.patch.get("operations", []))
        )
        workflow_palette_request = (
            local.action == "patch"
            and bool(local.patch)
            and any(
                operation.get("target_kind") == "figure"
                and str(operation.get("field", "")).startswith("workflow_options.")
                and str(operation.get("field", "")).endswith("_color")
                for operation in local.patch.get("operations", [])
            )
        )
        deterministic_font_request = (
            local.action == "patch"
            and bool(local.patch)
            and bool(local.patch.get("operations"))
            and all(
                operation.get("op") == "update"
                and operation.get("field") in {
                    "font_size", "workflow_options.font_size", "style.fontsize",
                }
                for operation in local.patch.get("operations", [])
            )
        )
        return (
            (compact in exact_commands and local.action in {"undo", "redo", "render"})
            or assay_axis_request
            or track_order_request
            or workflow_palette_request
            or deterministic_font_request
        )

    def _fallback(self, local: IntentResult, exc: Exception) -> IntentResult:
        status = self.ai_planner.status()
        provider = PlannerRegistry.LABELS.get(str(status.get("provider")), str(status.get("provider") or "AI"))
        reason = _planner_failure_reason(exc)
        log.warning("%s planner fallback (%s): %s", provider, type(exc).__name__, str(exc)[:500])
        if local.action != "clarify":
            reply = f"{local.reply}（{provider}{reason}，本次已由本地能力完成。）"
        else:
            reply = f"{local.reply}（{provider}{reason}；这次没有自动修改图。）"
        return IntentResult(
            local.action,
            reply,
            local.patch,
            requires_confirmation=local.requires_confirmation,
            planner=f"rules-fallback:{status.get('provider', 'ai')}",
        )

    def _direct_answer(self, message: str) -> Optional[IntentResult]:
        """Answer provider identity locally so the model cannot misidentify itself."""
        compact = re.sub(r"\s+", "", message).lower()
        identity_question = any(phrase in compact for phrase in (
            "什么模型", "哪个模型", "哪种模型", "模型是什么", "用的模型", "使用的模型",
            "whoareyou", "whatmodel",
        ))
        if not identity_question:
            return None
        status = self.ai_planner.status()
        provider = PlannerRegistry.LABELS.get(str(status.get("provider")), str(status.get("provider") or "AI"))
        model = status.get("model") or "默认模型"
        return IntentResult(
            "answer",
            f"当前对话理解使用的是 {provider} 的 {model} 模型；自然语言由 AI 优先理解，结果再经过本地能力白名单和参数校验。",
            planner=f"system:{status.get('provider', 'ai')}",
        )


class PlannerRegistry:
    """Configured planner providers exposed to the web workspace."""

    LABELS = {"local": "本地规则", "openai": "OpenAI", "deepseek": "DeepSeek"}

    def __init__(
        self,
        planners: Dict[str, FigurePlanner],
        unavailable: Optional[Dict[str, str]] = None,
        default_provider: str = "local",
    ):
        self.planners = dict(planners)
        self.unavailable = dict(unavailable or {})
        self.default_provider = default_provider if default_provider in self.planners else "local"
        self._lock = threading.RLock()

    @property
    def default(self) -> FigurePlanner:
        return self.planners[self.default_provider]

    def get(self, provider: Optional[str]) -> FigurePlanner:
        with self._lock:
            provider = provider or self.default_provider
            if provider in self.planners:
                return self.planners[provider]
            if provider in self.unavailable:
                raise ValueError(f"{self.LABELS.get(provider, provider)} 不可用：{self.unavailable[provider]}")
            raise ValueError(f"不支持的规划器供应商：{provider!r}。")

    def configure_runtime(self, provider: str, api_key: str, model: Optional[str] = None, client: Any = None) -> None:
        """Install an in-memory AI planner without persisting its credential."""
        if provider not in {"openai", "deepseek"}:
            raise ValueError("网页仅支持配置 OpenAI 或 DeepSeek。")
        key = api_key.strip()
        if len(key) < 8 or len(key) > 4096:
            raise ValueError("API Key 格式无效。")
        if model is not None:
            model = model.strip()
            if not model or len(model) > 120 or re.search(r"[\x00-\x20]", model):
                raise ValueError("模型名称格式无效。")
        if client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise ValueError("未安装 openai 包，无法创建 API 客户端。") from exc
            kwargs = {"api_key": key}
            if provider == "deepseek":
                kwargs["base_url"] = os.environ.get("CFIZZ_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            client = AsyncOpenAI(**kwargs)
        planner = (
            OpenAIPlanner(client=client, model=model)
            if provider == "openai"
            else DeepSeekPlanner(client=client, model=model)
        )
        with self._lock:
            self.planners[provider] = RuleFirstPlanner(planner)
            self.unavailable.pop(provider, None)
            self.default_provider = provider

    def remove_runtime(self, provider: str) -> None:
        if provider not in {"openai", "deepseek"}:
            raise ValueError("只能断开 OpenAI 或 DeepSeek。")
        with self._lock:
            self.planners.pop(provider, None)
            self.unavailable[provider] = "未在网页中配置，也未设置服务端环境变量。"
            if self.default_provider == provider:
                self.default_provider = "local"

    def status(self) -> Dict[str, Any]:
        with self._lock:
            providers = []
            for provider in ("local", "openai", "deepseek"):
                if provider in self.planners:
                    item = dict(self.planners[provider].status())
                    item.update({"id": provider, "label": self.LABELS[provider], "available": True})
                else:
                    item = {
                        "id": provider,
                        "label": self.LABELS[provider],
                        "available": False,
                        "mode": "ai-assisted" if provider != "local" else "rules",
                        "provider": provider,
                        "model": None,
                        "reason": self.unavailable.get(provider, "未配置。"),
                    }
                providers.append(item)
            return {"default_provider": self.default_provider, "providers": providers}


def build_planner_registry_from_env() -> PlannerRegistry:
    """Build every configured provider without exposing credentials to clients."""

    planners: Dict[str, FigurePlanner] = {
        "local": RulePlanner(SimpleIntentInterpreter(), "用户主动选择本地规则模式。")
    }
    unavailable: Dict[str, str] = {}
    sdk_available = importlib.util.find_spec("openai") is not None

    if os.environ.get("OPENAI_API_KEY") and sdk_available:
        from openai import AsyncOpenAI

        planners["openai"] = RuleFirstPlanner(OpenAIPlanner(client=AsyncOpenAI()))
    else:
        unavailable["openai"] = "未设置 OPENAI_API_KEY。" if not os.environ.get("OPENAI_API_KEY") else "未安装 openai 包。"

    if os.environ.get("DEEPSEEK_API_KEY") and sdk_available:
        from openai import AsyncOpenAI

        deepseek_client = AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("CFIZZ_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        planners["deepseek"] = RuleFirstPlanner(DeepSeekPlanner(client=deepseek_client))
    else:
        unavailable["deepseek"] = "未设置 DEEPSEEK_API_KEY。" if not os.environ.get("DEEPSEEK_API_KEY") else "未安装 openai 包。"

    configured_default = os.environ.get("CFIZZ_AGENT_PROVIDER")
    if configured_default in planners:
        default_provider = configured_default
    elif "openai" in planners:
        default_provider = "openai"
    elif "deepseek" in planners:
        default_provider = "deepseek"
    else:
        default_provider = "local"
    return PlannerRegistry(planners, unavailable, default_provider)


def build_planner_from_env() -> FigurePlanner:
    """Backward-compatible helper returning the default configured planner."""

    return build_planner_registry_from_env().default


def public_figure_context(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return the useful model context without source paths or raw data."""

    sources = {
        source.get("id"): {
            "id": source.get("id"),
            "type": source.get("type"),
            "sample": source.get("sample"),
            "label": source.get("label"),
        }
        for source in spec.get("data_sources", [])
        if isinstance(source, dict)
    }
    panels = []
    for panel in spec.get("panels", []):
        layers = []
        for layer in panel.get("layers", []):
            layers.append({
                "id": layer.get("id"),
                "kind": layer.get("kind"),
                "label": layer.get("label"),
                "visible": layer.get("visible", True),
                "style": layer.get("style", {}),
                "source": sources.get(layer.get("source_id")),
            })
        panels.append({
            "id": panel.get("id"),
            "kind": panel.get("kind"),
            "label": panel.get("label"),
            "height_cm": panel.get("height_cm"),
            "layers": layers,
        })
    return {
        "title": spec.get("title"),
        "figure_type": spec.get("figure_type"),
        "viewport": spec.get("viewport", {}),
        "analysis": spec.get("analysis", {}),
        # Workflow-level parameters are part of the model's semantic
        # context.  Without them a model can see that a Loop workflow exists
        # but cannot distinguish marker size from heatmap height or report
        # the value it is changing.
        "workflow_options": spec.get("workflow_options", {}),
        "layout": spec.get("layout", {}),
        "panels": panels,
    }


def public_dialogue_context(history: List[Dict[str, str]], limit: int = 6) -> List[Dict[str, str]]:
    """Return a short, path/key-redacted dialogue window for contextual planning."""
    result = []
    for item in history[-limit:]:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        result.append({"role": role, "content": public_dialogue_text(content)[:1200]})
    return result


def public_dialogue_text(text: str) -> str:
    """Remove credentials and local paths while retaining conversational meaning."""
    value = str(text)
    value = re.sub(r"(?i)\b(?:sk|ds)-[A-Za-z0-9_-]{8,}\b", "[API_KEY]", value)
    value = re.sub(r"(?i)\b[A-Z]:[\\/][^\s，。；,;]+", "[本地路径]", value)
    value = re.sub(r"(?<![\w.])/(?:[^\s，。；,;]+/)+[^\s，。；,;]*", "[本地路径]", value)
    return value


def _planner_failure_reason(exc: Exception) -> str:
    """Return a stable, credential-free explanation for an AI fallback."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "authentication" in name or "permission" in name or "401" in message or "403" in message:
        return "鉴权失败"
    if "ratelimit" in name or "429" in message or "quota" in message:
        return "请求限流或额度不足"
    if "timeout" in name or "connection" in name:
        return "网络连接失败"
    if isinstance(exc, ValueError) and any(token in message for token in ("解析", "json", "schema")):
        return "返回格式未通过解析"
    if isinstance(exc, ValueError):
        return f"编辑计划校验未通过：{_repair_feedback(exc)}"
    return "调用异常"


def _repair_feedback(exc: ValueError) -> str:
    """Return bounded, non-sensitive validation feedback for one model retry."""
    message = public_dialogue_text(str(exc))
    message = re.sub(r"\s+", " ", message).strip()
    return message[:500] or "上一份计划未通过本地参数校验。"


def compile_plan(output: PlannerOutput, spec: Dict[str, Any], planner: str = "ai") -> IntentResult:
    """Compile the model vocabulary into validated, allow-listed patch operations."""

    if output.action == "reference":
        if output.edits or output.parameter_edits or output.calls or output.reference_request is None or output.workflow_request is not None:
            raise ValueError("reference 动作只能包含一个 reference_request。")
        request = output.reference_request
        if request.kind in {"locate_gene", "annotate_gene"} and not request.gene_symbol:
            raise ValueError(f"{request.kind} 必须提供 gene_symbol。")
        if request.kind == "annotate_region_genes" and request.gene_symbol:
            raise ValueError("annotate_region_genes 不应提供 gene_symbol。")
        return IntentResult(
            "reference",
            output.reply,
            {"reference_request": request.model_dump()},
            planner=planner,
        )
    if output.reference_request is not None:
        raise ValueError("只有 reference 动作可以包含 reference_request。")
    if output.action == "workflow":
        if output.edits or output.parameter_edits or output.calls or output.workflow_request is None:
            raise ValueError("workflow 动作只能包含一个 workflow_request。")
        figure_type = output.workflow_request.figure_type
        item = FIGURE_TYPE_BY_ID.get(figure_type)
        if item is None or not item.get("ready"):
            raise ValueError(f"未登记的 CFIZZ 可视化工作流：{figure_type!r}。")
        return IntentResult(
            "workflow", output.reply,
            {"workflow_request": output.workflow_request.model_dump()},
            planner=planner,
        )
    if output.workflow_request is not None:
        raise ValueError("只有 workflow 动作可以包含 workflow_request。")

    if output.action != "patch":
        if output.edits or output.parameter_edits or output.calls:
            raise ValueError("非 patch 动作不能包含 edits、parameter_edits 或 calls。")
        return IntentResult(output.action, output.reply, planner=planner)
    if not output.edits and not output.parameter_edits and not output.calls:
        raise ValueError("patch 动作至少需要一个 edit、parameter_edit 或 capability call。")

    panel_ids = {panel.get("id") for panel in spec.get("panels", [])}
    layers = [layer for panel in spec.get("panels", []) for layer in panel.get("layers", [])]
    layer_ids = {layer.get("id") for layer in layers}
    layer_kinds = {layer.get("id"): layer.get("kind") for layer in layers}
    operations = []
    scientific = False
    adjustment_notes: List[str] = []
    for edit in output.edits:
        operation, is_scientific = _compile_edit(edit, panel_ids, layer_ids, layer_kinds)
        operations.extend(operation)
        scientific = scientific or is_scientific
    parameter_catalog = ParameterCatalog(spec)
    for edit in output.parameter_edits:
        normalized_edit, adjustment = parameter_catalog.normalize_visual_edit(edit)
        operation, is_scientific = parameter_catalog.compile(normalized_edit)
        operations.append(operation)
        scientific = scientific or is_scientific
        if adjustment:
            adjustment_notes.append(adjustment)
    registry = CapabilityRegistry()
    for call in output.calls:
        compiled = registry.compile(call, spec)
        operations.extend(compiled.operations)
        scientific = scientific or compiled.requires_confirmation
    reply = output.reply
    if adjustment_notes:
        reply = "；".join(adjustment_notes) + "。"
    return IntentResult(
        "patch",
        reply,
        {"summary": reply[:160], "operations": operations},
        requires_confirmation=scientific,
        planner=planner,
    )


def _compile_edit(
    edit: PlannedEdit,
    panel_ids: set,
    layer_ids: set,
    layer_kinds: Dict[str, Any],
):
    kind = edit.edit_type
    if kind == "set_viewport":
        if not edit.chrom or edit.start is None or edit.end is None or edit.start < 0 or edit.end <= edit.start:
            raise ValueError("视野编辑需要合法的 chrom、start 和 end。")
        return ([
            _update("viewport", "chrom", edit.chrom),
            _update("viewport", "start", edit.start),
            _update("viewport", "end", edit.end),
        ], False)
    if kind == "set_resolution":
        value = _positive_integer(edit.number_value, "分辨率")
        return ([_update("analysis", "resolution", value)], True)
    if kind == "set_normalization":
        if edit.string_value not in {"raw", "oe", "log2_oe"}:
            raise ValueError("归一化方式只能是 raw、oe 或 log2_oe。")
        return ([_update("analysis", "normalization", edit.string_value)], True)
    if kind == "set_balance":
        if edit.bool_value is None:
            raise ValueError("balance 编辑缺少布尔值。")
        return ([_update("analysis", "balance", edit.bool_value)], True)
    if kind.startswith("set_layer_"):
        _require_id(edit.target_id, layer_ids, "图层")
        if kind == "set_layer_visibility":
            if edit.bool_value is None:
                raise ValueError("图层可见性编辑缺少布尔值。")
            return ([_update("layer", "visible", edit.bool_value, edit.target_id)], False)
        if kind == "set_layer_font_size":
            value = _bounded_number(edit.number_value, "图层字体", 3, 24)
            return ([_update("layer", "style.fontsize", value, edit.target_id)], False)
        value = _safe_text(edit.string_value, "图层文本")
        if kind == "set_layer_label":
            field = "label"
        elif kind == "set_layer_colormap":
            field = "style.cmap"
        else:
            field = "style.cmap" if layer_kinds.get(edit.target_id) == "hic" else "style.color"
        return ([_update("layer", field, value, edit.target_id)], False)
    if kind.startswith("set_panel_"):
        _require_id(edit.target_id, panel_ids, "面板")
        if kind == "set_panel_height":
            value = _bounded_number(edit.number_value, "面板高度", 0.2, 50)
            return ([_update("panel", "height_cm", value, edit.target_id)], False)
        return ([_update("panel", "label", _safe_text(edit.string_value, "面板标题"), edit.target_id)], False)
    if kind == "set_layout_width":
        return ([_update("layout", "width_cm", _bounded_number(edit.number_value, "画布宽度", 3, 100))], False)
    if kind == "set_layout_gap":
        return ([_update("layout", "gap_cm", _bounded_number(edit.number_value, "面板间距", 0, 10))], False)
    if kind == "set_layout_left_margin":
        return ([_update("layout", "left_margin_cm", _bounded_number(edit.number_value, "左边距", 0, 20))], False)
    if kind == "set_layout_right_margin":
        return ([_update("layout", "right_margin_cm", _bounded_number(edit.number_value, "右边距", 0, 20))], False)
    if kind == "set_figure_font_size":
        return ([_update("layout", "font_size", _bounded_number(edit.number_value, "绘图字体", 3, 24))], False)
    if kind == "set_figure_title":
        return ([_update("figure", "title", _safe_text(edit.string_value, "图标题"))], False)
    raise ValueError(f"不支持的模型编辑类型：{kind}。")


def _update(target_kind: str, field: str, value: Any, target_id: Optional[str] = None) -> Dict[str, Any]:
    operation = {"op": "update", "target_kind": target_kind, "field": field, "value": value}
    if target_id is not None:
        operation["target_id"] = target_id
    return operation


def _require_id(value: Optional[str], allowed: set, label: str) -> None:
    if not value or value not in allowed:
        raise ValueError(f"模型给出的{label} ID 不存在：{value!r}。")


def _positive_integer(value: Optional[float], label: str) -> int:
    if value is None or value <= 0 or not float(value).is_integer():
        raise ValueError(f"{label}必须是正整数。")
    return int(value)


def _bounded_number(value: Optional[float], label: str, minimum: float, maximum: float) -> float:
    if value is None or not minimum <= value <= maximum:
        raise ValueError(f"{label}必须在 {minimum:g}–{maximum:g} 之间。")
    return float(value)


def _safe_text(value: Optional[str], label: str) -> str:
    if not value or len(value) > 120 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError(f"{label}无效。")
    return value


_SYSTEM_PROMPT = """你是 CFIZZ 科研图形规划器。把用户请求转换为给定结构允许的最小编辑计划。
规则：
1. 只能引用 current_figure 中真实存在的 panel/layer ID，不能创造 ID。
2. 优先从 available_capabilities 选择能力并放入 calls；arguments_json 必须是符合该能力参数定义的 JSON 对象字符串。
3. target 用当前图真实结构表达目标：明确对象用 ids；“全部”用 all；“上/前 N 个”用 first；“下/后 N 个”用 last，并填写 count。可用 panel_id 和 kinds 缩小范围。
4. 普通调参优先使用 editable_parameters 和 parameter_edits。只能逐字选择目录中存在的 target_kind、target_id 和 parameter；operation 只能是 set/increase/decrease/toggle。increase/decrease 的 value 是正的变化量。旧的简单操作仍可使用 PlannedEdit；不能修改数据源，不生成代码或路径。
5. 不确定用户指的是哪个对象时，action=clarify，edits=[]、calls=[]，reply 用中文提出一个简短问题。
6. undo/redo/render 请求使用对应 action 且 edits=[]、calls=[]。
7. patch 时给出完成请求所需的最少 edits/parameter_edits/calls；PlannedEdit 未使用的值字段填 null。
8. 分辨率、归一化和 balance 会改变科学计算；reply 必须明确说明影响。确认要求由服务端判定。
9. 相对修改（更高、更宽等）根据 current_figure 当前数值计算合理的新绝对值。
10. reply 用简洁中文说明将要做什么，不声称已经完成。
11. 必须结合 figure_type 和当前可见图层理解省略的对象；当当前图只有一个相关图层时，“把红蓝色换掉”“再浅一点”等默认指向该图层，不要追问轨道名称。
12. 用户询问当前图、CFIZZ Agent 的功能、支持的修改方式或一般操作说明时，使用 action=answer，edits=[]、calls=[]，直接友好回答；不要把这类问题误判为无关请求。
13. answer 只用于不修改图的回答。不得声称已执行修改，不得编造 current_figure 和 available_capabilities 中没有的信息。
14. recent_dialogue 是最近几轮脱敏对话。对于“还有呢”“除了这个呢”“换一个”“再浅一点”等省略表达，先结合 recent_dialogue 和 current_figure 恢复指代；只有仍存在多个合理目标时才追问。
15. 区分询问与执行：用户询问“还能做什么/还有哪些/除了这个呢”时用 answer；只有明确要求改变当前图时才用 patch。
16. 先理解整句意图，不要因为句中出现基因名或轨道名就改变区域。比如“MYC 标签重叠、看不清”是当前基因轨道的版式问题，应通过 editable_parameters 调整 genes 图层真实的 layer.height_cm；不要修改不会传给渲染器的注释面板高度。只有用户明确说“定位、跳转、切换到某基因附近”时才修改 viewport。
17. 用户只要求某个轨道或基因标签的字体时，优先使用 editable_parameters 中该真实 layer ID 的 style.fontsize；要求整张图文字变化时，逐字选择目录中真实存在的 font_size 参数。多样本/工作流图通常使用 figure.workflow_options.font_size，不能改成不会传给该渲染器的 layout.font_size。
18. “最右侧标签截断、色标文字显示不全”通常是右侧留白不足，使用 set_layout_right_margin；左侧被裁切则使用 set_layout_left_margin。根据当前 margin 增加合理空间，不要修改数据区域。
19. editable_parameters 含当前值、类型、范围和允许操作。对“再大一点/再小一点/隐藏/显示/换颜色/增加留白”等请求，直接使用通用 parameter_edits；不要因为没有专用 edit_type 而拒绝。
20. 单基因注释轨道的数据源已经按 CFIZZ 示例预先提取为单基因 GTF。“只显示 MYC 基因”不需要隐藏任何图层；若当前基因轨道标签已经是 MYC，直接用 answer 说明已满足。必须保留 Hi-C 图层。
21. y 轴语义必须区分：用户说“所有轨道一致”使用 tracks.sync_y_axis mode=auto；说“按测序技术/每种类型内部一致”使用 mode=by_assay，使 ATAC、CTCF、H3K、RNA 等各自形成独立共享组，绝不能把所有技术设为同一组。
22. 用户要求调整轨道顺序时使用 tracks.reorder：target 选择要移动的真实 layer，放到另一条轨道“上面/前面”时把该轨道 ID 填入 before_layer_id；要求放到“最后/最下面”时省略 before_layer_id。若用户说“放到 Y 下面”，应移动 Y 或把目标关系转换为合法的 before_layer_id，不能修改图层标签冒充排序。
23. 涉及基因时先判断整句语义：只有用户明确要求定位某个基因、绘制/添加某个基因轨道，或绘制当前区域全部基因时，才使用 action=reference，并填写 reference_request。locate_gene 只定位区域；annotate_gene 定位并添加指定基因轨道；annotate_region_genes 显示当前区域全部基因。gene_symbol 只能填写用户实际提到的候选名称，绝不能生成基因坐标。用户询问“为什么基因文字在 Hi-C 图上”“这是什么基因”“某标签是否重叠”等属于问题或版式修改，不是 reference。Hi-C/HIC、ATAC、CTCF、RNA、BigWig、GTF 是数据或轨道类型，不能当成基因名称。基因名称和坐标最终由本地参考注释验证。
24. 用户要求创建 available_visualizations 中 selection_mode=conversation 的图时，使用 action=workflow 并逐字填写该条目的 figure_type；source_ids 只能从 current_figure.data_sources 的真实 ID 中选择，options 只填写用户明确给出的科学参数。不要用普通 patch 假装已经完成。服务端会核对当前数据源并绑定正式 CFIZZ entrypoint，缺少数据时会明确向用户索取。
25. 多样本工作流的正式参数位于 editable_parameters 中的 figure.workflow_options.*。Loop 圈/标记“太大、太小”只能修改 loop_multi/loop_diff_region 的 workflow_options.loop_size（或单样本 loop_heatmap 图层的 style.loop_size），绝不能用 triangle_ratio 代替；triangle_ratio 只表示三角热图高度。Loop 颜色和透明度分别使用 loop_color、loop_alpha。TAD 的 window_size 是 insulation 计算窗口，compartment 的 bar_height_ratio 是 E1 轨道高度；不要把这些参数互相替代。
26. 用户说“缩小 Loop 圈”时，缩小的是 CFIZZ marker 的大小，不是热图、面板或三角形的高度；相对修改必须基于目录中的 current_value 生成合理的新值，并在 reply 中说明实际修改的参数名和前后值。涉及 window、flank、n_bins、contact_type 等科学计算参数时，说明会重新计算并等待服务端确认。
27. action=patch 时至少提供一个 edit、parameter_edit 或 capability call。参数值必须遵守 editable_parameters 的类型与范围；若用户请求的纯视觉数值越界，使用最接近的合法值，并在 reply 中明确“请求值”和“实际应用值”，reply 必须与结构化参数一致。
"""
