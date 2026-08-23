"""Small deterministic interpreter for common conversational figure edits.

This is an MVP fallback and a safe target format for a future LLM planner. It
never executes code; it only emits allow-listed FigurePatch operations/actions.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Optional

from .capabilities import CapabilityCall, CapabilityRegistry, TargetSelector
from .figure_types import FIGURE_TYPE_BY_ID, READY_FIGURE_TYPE_IDS


_COLOR_WORDS = {
    "红色": "#D55E00",
    "蓝色": "#0072B2",
    "绿色": "#009E73",
    "橙色": "#E69F00",
    "紫色": "#CC79A7",
    "黑色": "#222222",
    "灰色": "#777777",
    "red": "#D55E00",
    "blue": "#0072B2",
    "green": "#009E73",
    "orange": "#E69F00",
    "purple": "#CC79A7",
}

_DIVERGING_PALETTES = (
    (("紫绿", "绿紫", "purple green", "purple-green"), "PRGn", "#1B7837", "#762A83", "紫绿"),
    (("蓝橙", "橙蓝", "blue orange", "blue-orange"), "PuOr", "#E66101", "#5E3C99", "蓝橙"),
    (("红蓝", "蓝红", "red blue", "red-blue"), "RdBu_r", "#D73027", "#4575B4", "红蓝"),
    (("棕绿", "绿棕", "brown green", "brown-green"), "BrBG", "#018571", "#A6611A", "棕绿"),
)


@dataclass(frozen=True)
class IntentResult:
    action: str
    reply: str
    patch: Optional[Dict[str, Any]] = None
    requires_confirmation: bool = False
    planner: str = "rules"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reply": self.reply,
            "patch": self.patch,
            "requires_confirmation": self.requires_confirmation,
            "planner": self.planner,
        }


class SimpleIntentInterpreter:
    """Interpret a constrained set of high-frequency Chinese/English edits."""

    def interpret(self, message: str, spec: Dict[str, Any]) -> IntentResult:
        text = message.strip()
        lowered = text.lower()
        if not text:
            return IntentResult("clarify", "请告诉我希望怎样修改当前图。")
        if lowered in {"撤销", "undo", "退回上一版", "回到上一版"}:
            return IntentResult("undo", "已准备撤销到上一版。")
        if lowered in {"重做", "redo", "恢复下一版"}:
            return IntentResult("redo", "已准备恢复下一版。")
        if any(word in lowered for word in ("重新画", "重新渲染", "render", "出图")) and self._parse_figure_type(lowered) is None:
            return IntentResult("render", "将使用当前配置重新绘图。")

        # UI workflow buttons include an explicit catalogue ID.  Resolve that
        # ID before the looser aliases below (e.g. ``loop_apa_multi`` also
        # contains the words "loop apa", which must not downgrade to the
        # single-sample Loop APA figure).
        explicit_workflow = self._parse_explicit_workflow(lowered)
        if explicit_workflow is not None:
            type_id, label = explicit_workflow
            return IntentResult(
                "workflow",
                f"准备执行 CFIZZ 工作流：{label}；将优先复用当前已绑定的数据。",
                {"workflow_request": {"figure_type": type_id, "source_ids": [], "options": {}}},
            )

        figure_type = self._parse_figure_type(lowered)
        if figure_type is not None:
            type_id, label = figure_type
            if type_id not in READY_FIGURE_TYPE_IDS:
                return IntentResult(
                    "clarify",
                    f"{label}尚未接入 CFIZZ 正式绘图 API，因此 Agent 不会使用自建绘图替代。",
                )
            return IntentResult(
                "patch",
                f"将当前数据改画为{label}；如配套结果使用特定分辨率或当前区域没有有效信号，将自动匹配。",
                {
                    "summary": f"切换图类型为{label}",
                    "operations": [{
                        "op": "update", "target_kind": "figure", "field": "figure_type", "value": type_id,
                    }],
                },
            )

        workflow_type = self._parse_workflow_type(lowered)
        if workflow_type is not None:
            type_id, label = workflow_type
            return IntentResult(
                "workflow",
                f"准备执行 CFIZZ 工作流：{label}；将优先复用当前已绑定的数据。",
                {"workflow_request": {"figure_type": type_id, "source_ids": [], "options": {}}},
            )

        shared_y = self._parse_shared_y_axis(text, spec)
        if shared_y is not None:
            return shared_y

        track_order = self._parse_track_order(text, spec)
        if track_order is not None:
            return track_order

        region = self._parse_region(text)
        if region:
            chrom, start, end = region
            return IntentResult(
                "patch",
                f"将视野调整到 {chrom}:{start:,}-{end:,}。",
                {
                    "summary": f"调整区域到 {chrom}:{start}-{end}",
                    "operations": [
                        {"op": "update", "target_kind": "viewport", "field": "chrom", "value": chrom},
                        {"op": "update", "target_kind": "viewport", "field": "start", "value": start},
                        {"op": "update", "target_kind": "viewport", "field": "end", "value": end},
                    ],
                },
            )

        resolution = self._parse_resolution(text)
        if resolution is not None:
            return IntentResult(
                "patch",
                f"分辨率将改为 {resolution:,} bp；这会重新读取 Hi-C 矩阵。",
                {
                    "summary": f"修改分辨率为 {resolution} bp",
                    "operations": [{
                        "op": "update",
                        "target_kind": "analysis",
                        "field": "resolution",
                        "value": resolution,
                    }],
                },
                requires_confirmation=True,
            )

        loop_size_request = self._parse_loop_size(text, spec)
        if loop_size_request is not None:
            return loop_size_request

        font_request = any(word in lowered for word in ("字体", "字号", "图中文字", "图里的文字", "font size", "fontsize"))
        matched_font_layer = self._match_layer(text, spec) if font_request else None
        layer_font_request = matched_font_layer is not None and any(
            word in lowered for word in ("标签", "标志", "轨道", "基因", "label")
        )
        if layer_font_request:
            current = float(matched_font_layer.get("style", {}).get("fontsize", spec.get("layout", {}).get("font_size", 5)))
            explicit = re.search(r"(?:标签|标志|轨道|基因).*?(?:字体|字号).*?(?:调到|改成|设为|到|=|为)?\s*(\d+(?:\.\d+)?)", text, re.I)
            if explicit:
                new_size = float(explicit.group(1))
            elif any(word in lowered for word in ("大一点", "调大", "增大", "放大", "太小", "小了")):
                new_size = round(max(current + 1, current * 1.3), 1)
            elif any(word in lowered for word in ("小一点", "调小", "减小", "缩小", "太大", "大了")):
                new_size = round(current / 1.25, 1)
            else:
                return IntentResult("clarify", f"请说明希望把“{matched_font_layer.get('label') or matched_font_layer['id']}”标签字体调大、调小，或指定字号。")
            if not 3 <= new_size <= 24:
                return IntentResult("clarify", "轨道标签字体支持 3–24 pt，请在这个范围内选择。")
            return IntentResult(
                "patch",
                f"将“{matched_font_layer.get('label') or matched_font_layer['id']}”的标签字体从 {current:g} pt 调整为 {new_size:g} pt。",
                {
                    "summary": f"修改 {matched_font_layer['id']} 标签字体为 {new_size:g} pt",
                    "operations": [{
                        "op": "update", "target_kind": "layer", "target_id": matched_font_layer["id"],
                        "field": "style.fontsize", "value": new_size,
                    }],
                },
            )

        if font_request:
            current = float(spec.get("layout", {}).get("font_size", 5))
            explicit = re.search(r"(?:图中(?:的)?文字|图里的文字|字体|字号|font\s*size|fontsize)\s*(?:调到|改成|设为|到|=|为)?\s*(\d+(?:\.\d+)?)", text, re.I)
            if explicit:
                new_size = float(explicit.group(1))
            elif any(word in lowered for word in ("大一点", "调大", "增大", "放大")):
                new_size = round(current * 1.25, 1)
            elif any(word in lowered for word in ("小一点", "调小", "减小", "缩小")):
                new_size = round(current / 1.25, 1)
            else:
                return IntentResult("clarify", "请说明希望把图内字体调大、调小，或者指定字号，例如“图中文字调到 7 pt”。")
            if not 3 <= new_size <= 24:
                return IntentResult("clarify", "绘图字体支持 3–24 pt，请在这个范围内选择。")
            return IntentResult(
                "patch",
                f"将生成图中的基础字体从 {current:g} pt 调整为 {new_size:g} pt。",
                {
                    "summary": f"修改绘图字体为 {new_size:g} pt",
                    "operations": [{
                        "op": "update",
                        "target_kind": "layout",
                        "field": "font_size",
                        "value": new_size,
                    }],
                },
            )

        palette_request = any(word in lowered for word in ("配色", "色图", "颜色换", "颜色改", "换掉", "换个颜色", "换一套"))
        palette = self._parse_diverging_palette(lowered)
        if palette and palette[3] == "红蓝" and "换掉" in lowered and not any(word in lowered for word in ("换成", "改成", "改为", "用")):
            palette = ("PRGn", "#1B7837", "#762A83", "紫绿")
        if spec.get("figure_type") in {"compartment", "hic_oe"} and (palette_request or palette):
            hic_layers = [layer for layer in self._all_layers(spec) if layer.get("kind") == "hic" and layer.get("visible", True)]
            if len(hic_layers) == 1:
                if palette is None:
                    palette = ("PRGn", "#1B7837", "#762A83", "紫绿")
                cmap, positive, negative, label = palette
                layer = hic_layers[0]
                return IntentResult(
                    "patch",
                    f"将当前图的红蓝发散配色改为{label}，并同步 E1 正负颜色。",
                    {
                        "summary": f"修改发散配色为{label}",
                        "operations": [
                            {"op": "update", "target_kind": "layer", "target_id": layer["id"], "field": "style.cmap", "value": cmap},
                            {"op": "update", "target_kind": "layer", "target_id": layer["id"], "field": "style.positive_color", "value": positive},
                            {"op": "update", "target_kind": "layer", "target_id": layer["id"], "field": "style.negative_color", "value": negative},
                        ],
                    },
                )

        color = next((value for word, value in _COLOR_WORDS.items() if word in lowered), None)
        if color:
            layer = self._match_layer(text, spec)
            if layer is None:
                return IntentResult("clarify", "我识别到了颜色修改，但不确定要修改哪条轨道。请说出右侧显示的轨道名称。")
            return IntentResult(
                "patch",
                f"将“{layer.get('label') or layer['id']}”改为 {color}。",
                {
                    "summary": f"修改 {layer['id']} 颜色",
                    "operations": [{
                        "op": "update",
                        "target_kind": "layer",
                        "target_id": layer["id"],
                        "field": "style.color" if layer.get("kind") != "hic" else "style.cmap",
                        "value": color if layer.get("kind") != "hic" else self._color_to_cmap(color),
                    }],
                },
            )

        clipped = any(word in lowered for word in ("截断", "被截", "裁切", "被裁", "显示不全", "展示不全", "超出", "切掉"))
        if clipped and any(word in lowered for word in ("最右", "右边", "右侧", "色标", "colorbar", "legend")):
            current = float(spec.get("layout", {}).get("right_margin_cm", 2))
            new_margin = round(min(20, max(current + 1.0, current * 1.6)), 2)
            return IntentResult(
                "patch",
                f"右侧标签被裁切通常是右边距不足；将右侧留白从 {current:g} cm 增加到 {new_margin:g} cm，保留数据区域和字体设置不变。",
                {
                    "summary": f"增加右边距到 {new_margin:g} cm 以显示完整标签",
                    "operations": [{
                        "op": "update", "target_kind": "layout", "field": "right_margin_cm", "value": new_margin,
                    }],
                },
            )
        if clipped and any(word in lowered for word in ("最左", "左边", "左侧")):
            current = float(spec.get("layout", {}).get("left_margin_cm", 1.5))
            new_margin = round(min(20, max(current + 1.0, current * 1.6)), 2)
            return IntentResult(
                "patch",
                f"左侧标签被裁切通常是左边距不足；将左侧留白从 {current:g} cm 增加到 {new_margin:g} cm。",
                {
                    "summary": f"增加左边距到 {new_margin:g} cm 以显示完整标签",
                    "operations": [{
                        "op": "update", "target_kind": "layout", "field": "left_margin_cm", "value": new_margin,
                    }],
                },
            )

        if any(word in lowered for word in ("标签重叠", "标签挤", "文字重叠", "文字挤", "label overlap", "labels overlap")):
            panel = self._match_panel(text, spec)
            if panel is None:
                annotation_panels = [
                    item for item in spec.get("panels", [])
                    if any(layer.get("kind") in {"genes", "intervals"} for layer in item.get("layers", []))
                ]
                panel = annotation_panels[0] if len(annotation_panels) == 1 else None
            if panel is None:
                return IntentResult("clarify", "我看出你想解决标签重叠。请说明是基因、增强子，还是右侧轨道名称发生重叠。")
            current = panel.get("height_cm", 1.0)
            current = 1.0 if current == "auto" else float(current)
            new_height = round(max(current + 0.8, current * 1.6), 2)
            return IntentResult(
                "patch",
                f"检测到这更可能是“{panel.get('label') or panel['id']}”垂直空间不足；先把面板从 {current:g} cm 增高到 {new_height:g} cm，让标签错开。若仍有局部重叠，可以继续指定是哪类标签。",
                {
                    "summary": f"增高 {panel['id']} 以减少标签重叠",
                    "operations": [{
                        "op": "update",
                        "target_kind": "panel",
                        "target_id": panel["id"],
                        "field": "height_cm",
                        "value": new_height,
                    }],
                },
            )

        if any(word in text for word in ("高一点", "拉高", "增高", "放大轨道")):
            panel = self._match_panel(text, spec)
            if panel is None:
                return IntentResult("clarify", "请说明要拉高哪个面板或轨道，例如“把信号轨道拉高一点”。")
            current = panel.get("height_cm", 1.0)
            current = 1.0 if current == "auto" else float(current)
            new_height = round(current * 1.25, 2)
            return IntentResult(
                "patch",
                f"将“{panel.get('label') or panel['id']}”高度从 {current:g} cm 调整为 {new_height:g} cm。",
                {
                    "summary": f"增高 {panel['id']}",
                    "operations": [{
                        "op": "update",
                        "target_kind": "panel",
                        "target_id": panel["id"],
                        "field": "height_cm",
                        "value": new_height,
                    }],
                },
            )

        return IntentResult(
            "clarify",
            "我识别到你想修改当前图，但本地规则无法可靠地把这句话映射到具体参数。你可以补充“修改哪个面板/轨道”和“希望怎样变化”；或者在“API 与数据设置”中选择已配置的 OpenAI/DeepSeek 来理解更多自然语言表达。",
        )

    def _parse_shared_y_axis(self, text: str, spec: Dict[str, Any]) -> Optional[IntentResult]:
        lowered = text.lower()
        mentions_axis = any(token in lowered for token in ("y轴", "y 轴", "纵轴", "y-axis", "y axis"))
        wants_shared = any(token in lowered for token in ("一致", "统一", "相同", "共享", "共用", "同一个"))
        wants_independent = any(token in lowered for token in ("取消", "独立", "分别", "各自"))
        if not mentions_axis or not (wants_shared or wants_independent):
            return None

        count = self._parse_count(text)
        panel = self._match_panel(text, spec)
        if panel is None and any(token in lowered for token in ("下面", "下方", "底部", "最后")):
            numeric_panels = [
                item for item in spec.get("panels", [])
                if sum(layer.get("kind") == "bigwig" for layer in item.get("layers", [])) >= (count or 2)
            ]
            panel = numeric_panels[-1] if numeric_panels else None
        if panel is None:
            numeric_panels = [
                item for item in spec.get("panels", [])
                if sum(layer.get("kind") == "bigwig" for layer in item.get("layers", [])) >= 2
            ]
            if len(numeric_panels) == 1:
                panel = numeric_panels[0]
        if panel is None:
            return IntentResult("clarify", "请说明要统一哪个面板中的 y 轴，或说出轨道名称。")

        numeric_layers = [layer for layer in panel.get("layers", []) if layer.get("kind") == "bigwig"]
        if count is not None and len(numeric_layers) < count:
            return IntentResult("clarify", f"“{panel.get('label') or panel['id']}”里只有 {len(numeric_layers)} 条数值轨道，找不到 {count} 条。")
        selected = numeric_layers[-count:] if count else numeric_layers
        if len(selected) < 2:
            return IntentResult("clarify", "共享 y 轴至少需要两条数值轨道。")

        wants_by_assay = any(token in lowered for token in (
            "测序技术", "技术类型", "测序类型", "每种技术", "每类", "各类", "同类型", "同一种",
        ))
        mode = "independent" if wants_independent else "by_assay" if wants_by_assay else "auto"
        call = CapabilityCall(
            capability_id="tracks.sync_y_axis",
            target=TargetSelector(
                scope="layer",
                ids=[layer["id"] for layer in selected],
                position="explicit",
                panel_id=panel["id"],
                kinds=["bigwig"],
            ),
            arguments_json=f'{{"mode":"{mode}"}}',
        )
        compiled = CapabilityRegistry().compile(call, spec)
        labels = "、".join(str(layer.get("label") or layer["id"]) for layer in selected)
        action = "恢复各自独立缩放" if wants_independent else "按测序技术分别统一 y 轴范围" if wants_by_assay else "统一 y 轴范围"
        return IntentResult(
            "patch",
            f"将为 {labels} {action}。",
            {"summary": f"{action}：{labels}", "operations": compiled.operations},
        )

    def _parse_track_order(self, text: str, spec: Dict[str, Any]) -> Optional[IntentResult]:
        """Compile conversational track-order requests into ``move_layer`` ops.

        Ordering is a layout edit, not a new plot.  The operation keeps the
        real layer IDs and is later rendered by CFIZZ's official integrated
        entrypoint.  The parser intentionally asks for clarification when a
        short assay name matches multiple tracks (for example two ATAC
        samples) instead of guessing.
        """

        lowered = text.lower()
        order_words = (
            "顺序", "排序", "排列", "调换", "交换", "互换", "对调",
            "上面", "上方", "前面", "之前", "下面", "下方", "后面", "之后",
            "最上", "顶部", "顶端", "最下", "底部", "最后", "top", "bottom",
            "above", "below", "before", "after",
        )
        if not any(word in lowered for word in order_words):
            return None

        track_layers = self._track_layers(spec)
        available = "、".join(self._layer_label(layer) for layer in track_layers)
        if len(track_layers) < 2:
            if "轨道" in lowered or any(token in lowered for token in ("track", "顺序", "排序", "排列")):
                return IntentResult("clarify", "当前没有至少两条可调整顺序的轨道。")
            return None

        mentions = self._mentioned_track_layers(text, spec)
        unique_mentions = []
        seen = set()
        for layer in mentions:
            if layer["id"] not in seen:
                unique_mentions.append(layer)
                seen.add(layer["id"])

        # “按 A、B、C 顺序排列” is compiled as a short chain of moves.  The
        # patch engine applies them transactionally in the listed order.
        if any(token in lowered for token in ("按", "依次", "顺序排列", "排序为")) and len(unique_mentions) >= 2:
            operations = [
                self._move_layer_operation(left, right, spec, before=True)
                for left, right in zip(unique_mentions, unique_mentions[1:])
            ]
            labels = "、".join(self._layer_label(layer) for layer in unique_mentions)
            return IntentResult(
                "patch",
                f"将按“{labels}”的顺序重新排列轨道。",
                {"summary": f"调整轨道顺序：{labels}", "operations": operations},
            )

        if any(token in lowered for token in ("调换", "交换", "互换", "对调")):
            if len(unique_mentions) != 2:
                return IntentResult("clarify", f"请明确要交换哪两条轨道。当前可选：{available}。")
            first, second = unique_mentions
            operation = self._move_layer_operation(second, first, spec, before=True)
            return IntentResult(
                "patch",
                f"将交换“{self._layer_label(first)}”和“{self._layer_label(second)}”的显示顺序。",
                {"summary": f"交换轨道顺序：{self._layer_label(first)}、{self._layer_label(second)}", "operations": [operation]},
            )

        # Split a “把 X 放到 Y 上/下” sentence at its movement verb.  This
        # handles labels containing spaces, underscores and Chinese text.
        verb = re.search(r"放到|放在|移到|移至|调到|调整到|排到|置于|移动到|move\s+to|place\s+after|place\s+before", lowered)
        if verb is None:
            if "轨道" in lowered or len(unique_mentions) >= 2:
                return IntentResult("clarify", f"请说明哪条轨道放到哪条轨道的前面或后面。当前可选：{available}。")
            return None

        source_expr = re.sub(r"^(?:请|把|将|让|帮我)\s*", "", text[: verb.start()], flags=re.I).strip(" ：:，,\t")
        tail = text[verb.end():].strip(" ：:，,\t")
        relation = None
        suffixes = (
            "最上面", "最上方", "顶部", "顶端", "第一条", "最下面", "最下方", "底部", "最后一条",
            "上面", "上方", "前面", "之前", "下面", "下方", "后面", "之后", "最后",
            "top", "bottom", "above", "below", "before", "after",
        )
        tail_clean = tail.rstrip("。！？!?；;")
        tail_lower = tail_clean.lower()
        for suffix in suffixes:
            if tail_lower.endswith(suffix):
                relation = suffix
                tail_clean = tail_clean[: -len(suffix)].rstrip(" 的的")
                break

        source = self._resolve_track_expression(source_expr, spec)
        if source is None:
            # Positional source expressions (“最后一条”) are resolved above;
            # a missing named source is usually an ambiguous assay label.
            return IntentResult("clarify", f"没有唯一匹配“{source_expr or '源轨道'}”的轨道。当前可选：{available}。")

        top_words = {"最上面", "最上方", "顶部", "顶端", "第一条", "top"}
        bottom_words = {"最下面", "最下方", "底部", "最后一条", "最后", "bottom"}
        if relation in top_words:
            panel = self._panel_for_layer(spec, source["id"])
            panel_layers = [layer for layer in panel.get("layers", []) if layer.get("kind") in {"bigwig", "genes", "intervals"} and layer.get("visible", True) and layer.get("id") != source["id"]] if panel else []
            target = panel_layers[0] if panel_layers else None
            operation = self._move_layer_operation(source, target, spec, before=True)
            destination = "最上面"
        elif relation in bottom_words:
            operation = self._move_layer_operation(source, None, spec, before=False)
            destination = "最后"
        else:
            target = self._resolve_track_expression(tail_clean, spec)
            if target is None:
                return IntentResult("clarify", f"没有唯一匹配“{tail_clean or '目标轨道'}”的轨道。当前可选：{available}。")
            if target["id"] == source["id"]:
                return IntentResult("clarify", "源轨道和目标轨道相同，请指定另一条轨道。")
            below = relation in {"下面", "下方", "后面", "之后", "after", "below"}
            operation = self._move_layer_operation(source, target, spec, before=not below)
            destination = f"“{self._layer_label(target)}”{'下面' if below else '上面'}"

        return IntentResult(
            "patch",
            f"将“{self._layer_label(source)}”移动到{destination}。",
            {"summary": f"调整轨道顺序：{self._layer_label(source)} → {destination}", "operations": [operation]},
        )

    @staticmethod
    def _track_layers(spec: Dict[str, Any]):
        return [
            layer
            for panel in spec.get("panels", [])
            for layer in panel.get("layers", [])
            if layer.get("kind") in {"bigwig", "genes", "intervals"} and layer.get("visible", True)
        ]

    @staticmethod
    def _layer_label(layer: Dict[str, Any]) -> str:
        return str(layer.get("label") or layer.get("id") or "未命名轨道")

    @staticmethod
    def _panel_for_layer(spec: Dict[str, Any], layer_id: str) -> Optional[Dict[str, Any]]:
        return next((panel for panel in spec.get("panels", []) if any(layer.get("id") == layer_id for layer in panel.get("layers", []))), None)

    def _mentioned_track_layers(self, text: str, spec: Dict[str, Any]):
        compact = re.sub(r"[\s_\-]+", "", text.casefold())
        matches = []
        for layer in self._track_layers(spec):
            aliases = [self._layer_label(layer), str(layer.get("id") or "")]
            positions = []
            for alias in aliases:
                normalized = re.sub(r"[\s_\-]+", "", alias.casefold())
                if len(normalized) >= 2:
                    position = compact.find(normalized)
                    if position >= 0:
                        positions.append((position, -len(normalized)))
            if positions:
                matches.append((min(positions), layer))

        # Short assay names are useful only when they identify one layer (e.g.
        # a single MYC or ATAC track); two ATAC samples must be named fully.
        for token in ("atac", "ctcf", "h3k27ac", "h3k", "rna", "myc", "gene", "enhancer", "基因", "增强子"):
            candidates = [layer for layer in self._track_layers(spec) if token in re.sub(r"[\s_\-]+", "", self._layer_label(layer).casefold())]
            if len(candidates) == 1 and not any(layer.get("id") == candidates[0].get("id") for _, layer in matches):
                position = compact.find(token)
                if position >= 0:
                    matches.append(((position, -len(token)), candidates[0]))
        for token, kind in (("基因", "genes"), ("gene", "genes"), ("增强子", "intervals"), ("enhancer", "intervals")):
            candidates = [layer for layer in self._track_layers(spec) if layer.get("kind") == kind]
            if len(candidates) == 1 and token in compact and not any(layer.get("id") == candidates[0].get("id") for _, layer in matches):
                matches.append(((compact.find(token), -len(token)), candidates[0]))
        # Resolve common sample qualifiers such as “ATAC normal” and “CTCF
        # variant” when an assay has two samples.  The qualifier is matched
        # against the real layer label/id; no file name or data is guessed.
        for assay in ("atac", "ctcf", "h3k27ac", "h3k", "rna"):
            assay_layers = [layer for layer in self._track_layers(spec) if assay in re.sub(r"[\s_\-]+", "", self._layer_label(layer).casefold())]
            if len(assay_layers) < 2:
                continue
            for qualifiers in (("normal", "nor", "control"), ("variant", "var", "treatment")):
                qualified = [
                    layer for layer in assay_layers
                    if any(token in re.sub(r"[\s_\-]+", "", self._layer_label(layer).casefold()) for token in qualifiers)
                ]
                if len(qualified) == 1 and assay in compact and any(token in compact for token in qualifiers):
                    if not any(layer.get("id") == qualified[0].get("id") for _, layer in matches):
                        matches.append(((compact.find(assay), -len(assay)), qualified[0]))
        matches.sort(key=lambda item: item[0])
        return [layer for _, layer in matches]

    def _resolve_track_expression(self, expression: str, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        lowered = expression.casefold().strip(" ：:，,。！？!?\t")
        layers = self._track_layers(spec)
        if not lowered:
            return None
        if any(token in lowered for token in ("最下面", "最后", "底部", "末尾", "last", "bottom")):
            return layers[-1]
        if any(token in lowered for token in ("最上面", "最上方", "顶部", "顶端", "第一条", "top")):
            return layers[0]
        ordinal = re.search(r"第\s*(\d+|一|二|两|三|四|五|六|七|八|九)\s*(?:条|个)?", lowered)
        if ordinal:
            index = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}.get(ordinal.group(1), int(ordinal.group(1)) if ordinal.group(1).isdigit() else 0)
            return layers[index - 1] if 1 <= index <= len(layers) else None
        normalized = re.sub(r"[\s_\-]+", "", lowered)
        for assay in ("atac", "ctcf", "h3k27ac", "h3k", "rna"):
            assay_layers = [layer for layer in layers if assay in re.sub(r"[\s_\-]+", "", self._layer_label(layer).casefold())]
            if len(assay_layers) < 2 or assay not in normalized:
                continue
            for qualifiers in (("normal", "nor", "control"), ("variant", "var", "treatment")):
                qualified = [
                    layer for layer in assay_layers
                    if any(token in re.sub(r"[\s_\-]+", "", self._layer_label(layer).casefold()) for token in qualifiers)
                ]
                if len(qualified) == 1 and any(token in normalized for token in qualifiers):
                    return qualified[0]
        mentioned = self._mentioned_track_layers(expression, spec)
        return mentioned[0] if len(mentioned) == 1 else None

    def _move_layer_operation(self, source: Dict[str, Any], target: Optional[Dict[str, Any]], spec: Dict[str, Any], *, before: bool) -> Dict[str, Any]:
        destination_panel = self._panel_for_layer(spec, target["id"]) if target else self._panel_for_layer(spec, source["id"])
        operation = {
            "op": "move_layer",
            "layer_id": source["id"],
            "panel_id": destination_panel.get("id") if destination_panel else None,
        }
        if target and before:
            operation["before_layer_id"] = target["id"]
        elif target and not before:
            layers = [layer for layer in (destination_panel or {}).get("layers", []) if layer.get("kind") in {"bigwig", "genes", "intervals"} and layer.get("visible", True) and layer.get("id") != source["id"]]
            index = next((index for index, layer in enumerate(layers) if layer.get("id") == target.get("id")), None)
            if index is not None and index + 1 < len(layers):
                operation["before_layer_id"] = layers[index + 1]["id"]
        return operation

    @staticmethod
    def _parse_count(text: str) -> Optional[int]:
        match = re.search(r"(?:下面|下方|底部|最后|前面|上面|前|后)?\s*([2-9]|二|两|三|四|五|六|七|八|九)\s*(?:个|条)?", text)
        if not match:
            return None
        return {"二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}.get(match.group(1), int(match.group(1)) if match.group(1).isdigit() else None)

    @staticmethod
    def _parse_figure_type(text: str):
        requests = (
            ("loop_apa", "Loop APA", ("loop apa", "loops apa", "环apa", "环 apa", "聚合峰值")),
            ("loop_heatmap", "Loop 标注热图", ("loop热图", "loop 热图", "标注loop", "标注 loop", "染色质环热图")),
            ("compartment", "A/B Compartment 图", ("a/b compartment", "ab compartment", "a/b区室", "ab区室", "compartment图", "compartment 图")),
            ("tad_boundary_square", "方形 Hi-C + TAD 边界", ("方形tad", "方形 tad", "方形边界热图", "tad方形热图")),
            ("tad_insulation_track", "Insulation score 轨道", ("insulation轨道", "insulation score轨道", "绝缘分数轨道", "单独画绝缘分数")),
            ("tad_insulation", "TAD / Insulation 图", ("insulation", "绝缘分数", "tad图", "tad 图")),
            ("hic_oe", "O/E 热图", ("o/e", "oe热图", "oe 热图", "observed/expected")),
            ("hic_square", "方形 Hi-C 热图", ("方形热图", "方形 hic", "方形hi-c", "矩阵热图")),
            ("hic_triangle", "三角 Hi-C 热图", ("三角热图", "三角 hic", "三角hi-c")),
        )
        for type_id, label, aliases in requests:
            if any(alias in text for alias in aliases):
                return type_id, label
        return None

    @staticmethod
    def _parse_workflow_type(text: str):
        """Recognize explicit catalogue workflow IDs or labels in UI requests."""
        for item in FIGURE_TYPE_BY_ID.values():
            if item.get("selection_mode") != "conversation":
                continue
            figure_id = str(item["id"]).lower()
            label = str(item.get("label") or "").lower()
            if figure_id in text or (label and label in text):
                return item["id"], item["label"]
        return None

    @staticmethod
    def _parse_explicit_workflow(text: str):
        match = re.search(r"figure_type\s*=\s*([a-z0-9_]+)", text, re.I)
        if not match:
            return None
        item = FIGURE_TYPE_BY_ID.get(match.group(1).lower())
        if item and item.get("selection_mode") == "conversation":
            return item["id"], item["label"]
        return None

    @staticmethod
    def _parse_region(text: str):
        match = re.search(
            r"(chr[\w.-]+|\b\d{1,2}\b|[XYM])\s*[:：]\s*([\d,.]+)\s*([kKmM]?)(?:bp|b)?\s*[-–—到]\s*([\d,.]+)\s*([kKmM]?)(?:bp|b)?",
            text,
        )
        if not match:
            return None
        chrom = match.group(1)
        if not chrom.lower().startswith("chr"):
            chrom = f"chr{chrom}"
        start = SimpleIntentInterpreter._scaled_number(match.group(2), match.group(3))
        end = SimpleIntentInterpreter._scaled_number(match.group(4), match.group(5))
        if end <= start:
            return None
        return chrom, start, end

    @staticmethod
    def _parse_resolution(text: str) -> Optional[int]:
        match = re.search(r"(?:分辨率|resolution)\s*(?:改成|设为|到|=|为)?\s*([\d,.]+)\s*([kKmM]?)", text, re.I)
        if not match:
            return None
        return SimpleIntentInterpreter._scaled_number(match.group(1), match.group(2))

    def _parse_loop_size(self, text: str, spec: Dict[str, Any]) -> Optional[IntentResult]:
        """Handle the unambiguous, high-frequency "Loop circle" edit locally.

        CFIZZ exposes two similarly named dimensions: ``triangle_ratio`` is
        the heatmap height, while ``loop_size`` is the marker size passed to
        the Loop scatter layer.  Keeping this small fallback here prevents a
        temporary AI outage (or an ambiguous model plan) from changing the
        wrong dimension.
        """
        lowered = text.lower()
        figure_type = str(spec.get("figure_type") or "")
        loop_context = any(token in lowered for token in ("loop", "loops", "环", "圈", "marker")) or figure_type in {
            "loop_heatmap", "loop_multi", "loop_diff_region",
        }
        size_context = any(token in lowered for token in ("大小", "尺寸", "标记", "marker", "太大", "太小", "大了", "小了", "变大", "变小", "缩小", "放大"))
        if not loop_context or not size_context:
            return None

        is_multi = figure_type in {"loop_multi", "loop_diff_region"}
        if is_multi:
            current_value = spec.get("workflow_options", {}).get("loop_size", 50)
        else:
            hic_layers = [layer for layer in self._all_layers(spec) if layer.get("kind") == "hic" and layer.get("visible", True)]
            if not hic_layers:
                return IntentResult("clarify", "当前图中没有可见 Hi-C 图层，无法调整 Loop 圈大小。")
            current_value = hic_layers[0].get("style", {}).get("loop_size", 2)
        try:
            current = float(current_value)
        except (TypeError, ValueError):
            current = 50.0 if is_multi else 2.0

        explicit = re.search(
            r"(?:loop|loops|环|圈|marker).*?(?:调到|改成|设为|设置为|到|=|为)\s*([\d,.]+)",
            text, re.I,
        )
        if explicit:
            new_value = float(explicit.group(1).replace(",", ""))
        elif any(token in lowered for token in ("一半", "半大", "减半")):
            new_value = current * 0.5
        elif any(token in lowered for token in ("太大", "大了", "变小", "缩小", "小一点", "调小", "减小")):
            new_value = current * 0.7
        elif any(token in lowered for token in ("太小", "小了", "变大", "放大", "大一点", "调大", "增大")):
            new_value = current * 1.3
        else:
            return None

        new_value = round(max(0.1, min(1000.0, new_value)), 3)
        if is_multi:
            operation = {
                "op": "update", "target_kind": "figure",
                "field": "workflow_options.loop_size", "value": new_value,
            }
            target_label = "多样本工作流的 workflow_options.loop_size"
        else:
            operations = []
            for layer in [item for item in self._all_layers(spec) if item.get("kind") == "hic" and item.get("visible", True)]:
                operations.append({
                    "op": "update", "target_kind": "layer", "target_id": layer["id"],
                    "field": "style.loop_size", "value": new_value,
                })
            operation = operations
            target_label = "可见 Hi-C 图层的 style.loop_size"
        return IntentResult(
            "patch",
            f"将 {target_label} 从 {current:g} 调整为 {new_value:g}；只改变 Loop 圈/marker 大小，不改变三角热图高度。",
            {
                "summary": f"修改 Loop 圈大小为 {new_value:g}",
                "operations": operation if isinstance(operation, list) else [operation],
            },
        )

    @staticmethod
    def _scaled_number(raw: str, suffix: str) -> int:
        value = float(raw.replace(",", ""))
        scale = {"": 1, "k": 1_000, "m": 1_000_000}[suffix.lower()]
        return int(value * scale)

    @staticmethod
    def _all_layers(spec: Dict[str, Any]):
        return [layer for panel in spec.get("panels", []) for layer in panel.get("layers", [])]

    def _match_layer(self, text: str, spec: Dict[str, Any]):
        lowered = text.lower()
        candidates = []
        for layer in self._all_layers(spec):
            names = [str(layer.get("label", "")), str(layer.get("id", ""))]
            normalized_text = re.sub(r"[\s_-]+", "", lowered).replace("normal", "nor").replace("variant", "var")
            normalized_names = []
            for name in names:
                normalized = re.sub(r"[\s_-]+", "", name.lower()).replace("normal", "nor").replace("variant", "var")
                normalized_names.extend({normalized, normalized.removesuffix("layer"), normalized.removeprefix("hipsc")})
            score = max((len(name) for name in normalized_names if name and name in normalized_text), default=0)
            if score:
                candidates.append((score, layer))
        if not candidates:
            aliases = {"atac": "atac", "rna": "rna", "基因": "gene", "enhancer": "enhancer", "增强子": "enhancer"}
            for word, token in aliases.items():
                if word in lowered:
                    matching = [layer for layer in self._all_layers(spec) if token in (str(layer.get("id", "")) + str(layer.get("label", ""))).lower()]
                    if len(matching) == 1:
                        return matching[0]
                    return None
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _match_panel(text: str, spec: Dict[str, Any]):
        lowered = text.lower()
        matches = []
        for panel in spec.get("panels", []):
            names = [str(panel.get("label", "")), str(panel.get("id", ""))]
            if any(name and name.lower() in lowered for name in names):
                matches.append(panel)
        if len(matches) == 1:
            return matches[0]
        if any(word in lowered for word in ("信号", "track", "轨道")):
            signal_panels = [panel for panel in spec.get("panels", []) if panel.get("kind") == "signal_tracks"]
            if len(signal_panels) == 1:
                return signal_panels[0]
            labelled = [panel for panel in signal_panels if "signal" in str(panel.get("id", "")).lower()]
            if len(labelled) == 1:
                return labelled[0]
        return None

    @staticmethod
    def _color_to_cmap(color: str) -> str:
        return {
            "#D55E00": "Reds",
            "#0072B2": "Blues",
            "#009E73": "Greens",
            "#CC79A7": "Purples",
            "#777777": "Greys",
            "#222222": "Greys",
        }.get(color, "Reds")

    @staticmethod
    def _parse_diverging_palette(text: str):
        for aliases, cmap, positive, negative, label in _DIVERGING_PALETTES:
            if any(alias in text for alias in aliases):
                return cmap, positive, negative, label
        return None
