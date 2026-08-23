# CFIZZ Agent 产品与技术蓝图

## 1. 产品定义

CFIZZ Agent 是一个面向科研用户的对话式基因组可视化工作台。用户通过自然语言提供数据位置、分析区域和期望图形，系统使用 cfizz 完成数据检查、参数选择、绘图、预览、迭代修改和出版级导出。

产品的核心不是“聊天生成代码”，而是“聊天编辑一张有状态、可复现的科学图”。

```text
左侧：对话、数据与版本历史     右侧：图形预览、图层状态与导出
```

## 2. 用户价值

- 用户描述科研目标和视觉效果，不需要先学习 cfizz 的函数与参数。
- 系统自动发现 `.cool/.mcool` 的染色体和分辨率，并检查 BigWig、GTF、BED、TAD、Loop 等文件。
- 每轮对话只修改用户提到的内容，其他图形状态保持不变。
- 科学参数和视觉参数分开管理，避免一次视觉修改意外改变计算口径。
- 每张图都能追溯数据、参数、软件版本和修改历史，并可再次打开继续编辑。

## 3. MVP 目标

第一版优先服务“用户已有处理后的数据，希望快速画图”的场景，不把 FASTQ 预处理放进首个 MVP。

### 3.1 MVP 支持的数据

- Hi-C：`.cool`、`.mcool`
- 信号轨道：`.bw`、`.bigwig`
- 注释轨道：`.gtf`、`.gff`、`.bed`
- 结构结果：TAD/insulation TSV、Loop BEDPE

### 3.2 MVP 支持的图

1. 单样本或多样本 Hi-C heatmap
2. Hi-C + TAD boundary
3. Hi-C + Loop
4. Hi-C + BigWig/GTF/BED 多组学整合图
5. A/B compartment 图
6. 已有结果驱动的 TAD pileup、Loop APA 和 saddle 图

### 3.3 MVP 支持的对话操作

- 创建：指定数据、区域和图形类型后生成第一版图。
- 局部修改：修改颜色、色阶、轨道高度、标签、边距、分辨率或区域。
- 添加/删除：增删样本、信号轨道、TAD、Loop 或基因注释。
- 聚焦：围绕某个坐标、基因、TAD 或 Loop 放大。
- 比较：并排展示样本并锁定区域、分辨率和色阶。
- 历史：撤销、重做、恢复任意已成功版本。
- 导出：SVG、PNG、PDF，以及完整 FigureSpec 和运行记录。

## 4. 面向用户的交互原则

### 4.1 隐藏实现参数

用户可以说“边界明显一点”，Agent 再将其解释为边界颜色、透明度或线宽的调整。只有当不同选择会改变科学结论时，才要求用户确认。

### 4.2 区分科学修改和视觉修改

- 视觉修改：颜色、字体、大小、布局、线宽，可直接预览。
- 科学修改：分辨率、balance、归一化方法、分析窗口，需要在执行前明确提示影响。

### 4.3 先检查，再执行

绘图前检查文件存在性、格式、染色体命名、区域覆盖、分辨率和样本对应关系。错误信息必须转化为用户能行动的说明。

### 4.4 保留上下文

“把第二条轨道放大”“刚才那个 Loop 标红”“回到上一版”等指令都依赖当前 FigureSpec、选中对象和版本历史，不能只依靠最近一条消息。

### 4.5 不静默改变科学口径

如果请求的分辨率不存在，系统不能悄悄换用另一个分辨率。可以推荐最接近的可用值，并说明变化后再执行。

## 5. 关键用户流程

### 5.1 首次绘图

```text
用户提供文件或目录
  → 数据探查
  → Agent 总结发现的样本、染色体、分辨率和轨道
  → 解析绘图意图
  → 生成 FigureSpec
  → 预检
  → 调用 cfizz
  → 右侧显示预览
  → 返回简短结果说明与可继续修改的建议
```

### 5.2 迭代修改

```text
用户修改要求
  → 定位 FigureSpec 中的目标对象
  → 产生最小 patch
  → 判断是否需要重新计算或只需重绘
  → 渲染新版本
  → 展示差异并写入历史
```

### 5.3 模糊指令

系统优先使用当前选中图层、最近修改对象和图形语义消歧。如果仍有多个高概率解释且会造成明显不同结果，再提出一个简短问题。

## 6. 系统架构

```text
Web UI
├── Chat panel
├── Figure preview
├── Layer inspector
└── Version/export panel
        │
Agent Orchestrator
├── Intent parser
├── Conversation and selection state
├── FigureSpec patcher
├── Safety/scientific-change policy
└── User-facing result narrator
        │
Domain Services
├── Data inspector
├── FigureSpec validator
├── cfizz adapter
├── Render job manager
└── Artifact/version store
        │
cfizz + external scientific tools
```

### 6.1 Agent Orchestrator

负责理解自然语言、选择动作、生成 FigureSpec patch、判断是否需要澄清，以及组织工具调用。它不直接拼接任意 Python 代码。

### 6.2 Data Inspector

以只读方式扫描用户授权的数据，返回统一元数据：文件类型、样本名、染色体、分辨率、覆盖范围、轨道值域和潜在兼容问题。

### 6.3 FigureSpec

FigureSpec 是前端、Agent 和执行层之间的唯一事实来源。初稿 JSON Schema 见 `figure-spec.schema.json`。

### 6.4 cfizz Adapter

将稳定的 FigureSpec 转换为 cfizz 调用，屏蔽当前项目中分散的函数签名和示例脚本。适配层应采用允许列表，不接受模型生成的任意代码执行。

建议首批稳定工具：

- `inspect_hic`
- `inspect_track`
- `render_hic_heatmap`
- `render_integrated_tracks`
- `cfizz.api.plot_hic_compartment`
- `render_tad_pileup`
- `cfizz.api.plot_hic_loop_apa`
- `render_saddle`

### 6.5 Job Manager

绘图可能持续数秒到数分钟，需要异步任务、进度事件、取消、超时、日志和失败重试。相同输入和 FigureSpec 应使用内容哈希缓存。

### 6.6 Artifact Store

每次成功渲染至少保存：

- FigureSpec 快照
- SVG/PNG 预览
- 运行日志
- 使用的输入文件指纹
- cfizz 与依赖版本
- 父版本和本轮 patch

## 7. FigureSpec 设计规则

- 使用稳定 ID 引用数据、面板和图层，避免用数组位置表达“第二条轨道”。
- `data_sources` 只描述输入；`panels` 和 `layers` 描述图的组合方式。
- 科学参数放在 `analysis`，纯视觉参数放在 `style`。
- 坐标统一采用明确的 0-based half-open 内部表示；界面负责友好显示。
- 未显式设置的值允许为 `auto`，但执行后必须把最终解析值写入运行记录。
- 密钥和环境变量不能进入 FigureSpec。

## 8. Agent 动作协议

自然语言层不直接生成绘图代码。高频规则或外部模型先选择 `capabilities.json` 中的能力，再用 `TargetSelector` 表达“指定图层”“全部”“前 N 条”“后 N 条”等目标。服务端根据当前 FigureSpec 解析真实 ID、验证参数，并编译为可审计的 FigurePatch。比如“让下面四个 y 轴保持一致”会选择 `tracks.sync_y_axis`，将最后四条 BigWig 轨道写入同一个 `y_scale_group`，渲染时统一计算范围。

Agent 每轮只选择以下动作之一或有序组合：

- `inspect_data`：读取元数据，不画图。
- `create_figure`：创建新的 FigureSpec。
- `patch_figure`：对当前 FigureSpec 做局部修改。
- `validate_figure`：执行数据和科学一致性检查。
- `render_figure`：提交渲染任务。
- `cancel_render`：取消尚未完成的任务。
- `export_figure`：导出指定格式。
- `undo` / `redo`：切换版本。

所有写操作都应返回结构化结果，不以终端输出作为唯一状态。

## 9. MVP 验收场景

以下场景全部通过，才算完成第一版：

1. 用户提供一个 `.mcool`，不手动指定分辨率也能得到合理的区域热图。
2. 用户说“放大 FOXJ1，上下游各留 200 kb”，系统能通过 GTF 定位并重绘。
3. 用户增加一条 BigWig，已有 Hi-C 色阶和其他轨道不发生变化。
4. 用户说“TAD 边界更明显”，只改变边界样式，不重新计算 TAD。
5. 用户改变分辨率时，系统明确提示这是科学参数变化并重新读取矩阵。
6. 两样本比较时默认锁定坐标和色阶，并在无法锁定时说明原因。
7. 用户可以撤销到任意成功版本。
8. 导出的 SVG、FigureSpec 和运行记录能够在新会话中复现。
9. 文件无效、染色体不一致或区域越界时，界面给出可操作的错误信息。
10. 用户无需接触 Python、函数名或底层 cfizz 参数。

## 10. 明确不进入首个 MVP 的内容

- FASTQ 到 mcool 的完整生产级调度
- 任意 Python 代码生成和执行
- 多用户协作编辑
- 云端大规模任务队列
- 自动解读生物学结论
- 自动替用户决定可能改变结论的分析方法

## 11. 实现顺序

1. 修复并稳定 cfizz 的高层绘图入口。
2. 实现 Data Inspector 和 FigureSpec 校验器。
3. 实现单样本 heatmap 的端到端 create/patch/render。
4. 增加图层和多样本支持。
5. 增加版本、撤销、缓存与导出。
6. 接入左聊右图的 Web UI。
7. 最后接入自然语言 Agent，并用验收语料回归测试。

先构建确定性的工具链，再让 Agent 调用，可以把模型的不确定性限制在意图理解和参数建议层，而不是科研计算层。
