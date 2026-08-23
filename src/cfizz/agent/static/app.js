const EXPECTED_API_REVISION = 7;
const state = { sessionId: null, session: null, activeJob: null, provider: 'local', planners: [], figureTypes: [], datasetScan: null, activeWorkflow: null, fileOverrides: {}, renderTimer: null, renderStartedAt: null, language: localStorage.getItem('cfizz-language') || 'zh-CN', regionEdited: false, defaultRegionQuery: 'chr1:0-2Mb' };
const $ = (id) => document.getElementById(id);

// The starter range is a hint, not an explicit user constraint.  This lets
// each CFIZZ workflow choose a data-appropriate viewport when the user has
// not entered a gene or genomic range.  Once the field is edited, preserve
// exactly what the user entered and pass it to the backend.
function datasetQuery() {
  const value = $('datasetGene').value.trim();
  return value && (state.regionEdited || value !== state.defaultRegionQuery) ? value : null;
}

const messages = {
  'zh-CN': {
    subtitle:'对话式 Hi-C 可视化', loadDemo:'载入 FOXJ1 示例', undo:'撤销', redo:'重做', chat:'对话', dataApi:'数据与 API',
    dialogueApi:'理解模式', connectAi:'API 配置', memoryOnly:'临时保存', apiKeyPlaceholder:'输入 API Key',
    loadingConfig:'正在读取配置……', keySecretHint:'密钥仅保存在内存，服务重启后清除。',
    show:'显示', hide:'隐藏', connectUse:'连接并使用', disconnect:'断开并清除', startHic:'单个 Hi-C 文件', dataSource:'数据源',
    load:'加载', figureType:'图类型', applyCurrent:'应用到当前图', selectFigure:'选择图类型后生成', scanDataset:'实验目录', scanDirectory:'扫描目录', resolutionChoice:'共同分辨率', resolutionAuto:'自动选择',
    targetGene:'基因或范围（可选，如 FOXJ1、chr1:1-2Mb）', humanHg38:'人类 hg38 / GRCh38', authorizeRetry:'授权目录并重试', buildSelected:'生成图形', buildMultiomics:'生成默认整合图', confirmPairing:'我已确认以上样本对应关系', sampleName:'样本名', detectedRole:'数据角色',
    loadingTypes:'正在读取图类型……', serverAuth:'', datasetHint:'支持单个 .cool/.mcool 或实验目录；范围可写 chr1、chr1:1-2Mb 或 chr17:75,636,332-76,641,245。', dataPathPlaceholder:'/data/sample.mcool 或 /data/case1', datasetPathPlaceholder:'E:\\project\\case1 或 /data/case1', combinedWorkflows:'', combinedWorkflowsHint:'', workflowCatalog:'CFIZZ 工作流（选择各工作流自己的输入）', useWorkflow:'选择文件', missingData:'补充数据', workflowNeeds:'需要', selectAll:'全选', clearAll:'清空', selectRecommended:'推荐选择', workflowInputs:'选择本次工作流使用的文件', buildWorkflow:'生成', addTracks:'添加轨道到当前图', addingTracks:'正在添加轨道', trackAlreadyPresent:'所选轨道已经在当前图中，无需重复添加。', tracksNotIncluded:'所选图形的 CFIZZ 接口没有整合轨道面板；本次只生成 Hi-C 图，已选轨道仍保留在会话中。', uploadSupplement:'上传补充文件', uploading:'正在上传补充文件……', uploadDone:'补充完成，正在重新扫描……', loadFigureFirst:'请先载入 Hi-C 文件或扫描实验目录。', workflowQueued:'已提交工作流请求；系统会复用当前数据，缺少输入时会提示。', workflowBuilding:'正在根据所选数据创建图形……', workflowCreated:'已根据所选数据创建图形，之后可以继续用对话修改。',
    welcomeMessage:'载入示例后，可以试试：“有的标签重叠了”“让下面四个 y 轴保持一致”或“把 ATAC normal 改成绿色”。',
    chatPlaceholder:'告诉我你想怎样修改这张图……', sendDraw:'发送并绘图', enterHint:'Enter 发送 · Shift+Enter 换行', currentFigure:'CURRENT FIGURE', noFigure:'尚未载入图形',
    history:'历史版本', startConversation:'从一次对话开始', emptyHint:'右侧会持续显示当前版本，修改不会覆盖历史图。',
    versionHistory:'版本历史', historyHint:'选择任一版本恢复并重新绘图', region:'区域', resolution:'分辨率', version:'版本', download:'下载',
    checking:'检测中', localRules:'本地规则', unknownStatus:'状态未知', waiting:'等待开始', rendering:'正在生成图形', queued:'等待绘图资源', drawing:'正在渲染图形',
    completed:'绘图完成', failed:'绘图失败', elapsed:(n)=>`已用时 ${n} 秒`, preparing:'正在准备数据与绘图参数……', current:'当前', figureEdit:'图形修改', extraInput:'（需额外输入）', tracksExcluded:'（不含已选轨道）'
  },
  en: {
    subtitle:'Conversational Hi-C visualization', loadDemo:'Load FOXJ1 demo', undo:'Undo', redo:'Redo', chat:'Chat', dataApi:'Data & API',
    dialogueApi:'Interpretation mode', connectAi:'API settings', memoryOnly:'Temporary', apiKeyPlaceholder:'Enter API Key', show:'Show', hide:'Hide',
    loadingConfig:'Loading configuration…', keySecretHint:'The key stays in memory and is cleared on restart.',
    connectUse:'Connect and use', disconnect:'Disconnect and clear', startHic:'Single Hi-C file', dataSource:'Data source', load:'Load', figureType:'Figure type', applyCurrent:'Apply to current figure',
    scanDataset:'Experiment directory', scanDirectory:'Scan directory', resolutionChoice:'Shared resolution', resolutionAuto:'Automatic', targetGene:'Gene or region (optional, e.g. FOXJ1 or chr1:1-2Mb)', humanHg38:'Human hg38 / GRCh38', selectFigure:'Choose a figure type, then build', confirmPairing:'I confirm these sample pairings', sampleName:'Sample name', detectedRole:'Data role',
    authorizeRetry:'Authorize and retry', buildSelected:'Build figure', buildMultiomics:'Build default integrated figure',
    loadingTypes:'Loading figure types…', serverAuth:'', datasetHint:'Accepts one .cool/.mcool file or an experiment directory. Regions can be chr1, chr1:1-2Mb, or chr17:75,636,332-76,641,245.', dataPathPlaceholder:'/data/sample.mcool or /data/case1', datasetPathPlaceholder:'E:\\project\\case1 or /data/case1', combinedWorkflows:'', combinedWorkflowsHint:'', workflowCatalog:'CFIZZ workflows (choose inputs per workflow)', useWorkflow:'Choose files', missingData:'Add data', workflowNeeds:'Needs', selectAll:'Select all', clearAll:'Clear', selectRecommended:'Recommended', workflowInputs:'Choose files for this workflow', buildWorkflow:'Build', addTracks:'Add tracks to current figure', addingTracks:'Adding tracks', trackAlreadyPresent:'The selected tracks are already in the current figure; nothing to add.', tracksNotIncluded:'The selected CFIZZ renderer has no integrated track panel; this build will contain Hi-C only, while the checked tracks remain available in the session.', uploadSupplement:'Upload supporting files', uploading:'Uploading supporting files…', uploadDone:'Uploaded; rescanning…', loadFigureFirst:'Load a Hi-C file or scan a data directory.', workflowQueued:'Workflow request submitted; current inputs will be reused and missing data will be reported.', workflowBuilding:'Building a figure from the selected data…', workflowCreated:'Figure created from the selected data. You can continue editing it in chat.',
    welcomeMessage:'Load the demo, then try: "Some labels overlap", "Use the same y-axis for the last four tracks", or "Change ATAC normal to green".',
    chatPlaceholder:'Describe how you want to change this figure…', sendDraw:'Send & draw', enterHint:'Enter to send · Shift+Enter for a new line', currentFigure:'CURRENT FIGURE', noFigure:'No figure loaded',
    history:'History', startConversation:'Start with a conversation', emptyHint:'The current version stays visible here; edits do not overwrite figure history.',
    versionHistory:'Version history', historyHint:'Select a version to restore and render it again', region:'Region', resolution:'Resolution', version:'Version', download:'Download',
    checking:'Checking', localRules:'Local rules', unknownStatus:'Unknown status', waiting:'Waiting', rendering:'Generating figure', queued:'Waiting for renderer', drawing:'Rendering figure',
    completed:'Figure ready', failed:'Render failed', elapsed:(n)=>`${n}s elapsed`, preparing:'Preparing data and plotting parameters…', current:'Current', figureEdit:'Figure edit', extraInput:' (additional input required)', tracksExcluded:'(checked tracks not included)'
  }
};
function t(key, ...args) { const value = messages[state.language]?.[key] ?? messages['zh-CN'][key] ?? key; return typeof value === 'function' ? value(...args) : value; }
function applyLanguage(language) {
  state.language = language;
  localStorage.setItem('cfizz-language', language);
  document.documentElement.lang = language;
  $('languageSelect').value = language;
  document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(node => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  if (!state.session) $('figureTitle').textContent = t('noFigure');
  if (!state.activeJob) $('statusText').textContent = t(state.session ? 'completed' : 'waiting');
  if (state.planners.length) applyPlannerCatalog({providers:state.planners, default_provider:state.provider}, state.provider);
  else updatePlannerDisplay();
  if (state.figureTypes.length) loadFigureTypes();
  if (state.datasetScan) updateDatasetSelectionSummary();
  if (state.datasetScan && !state.sessionId) {
    $('applyFigureType').disabled = true;
    $('applyFigureType').textContent = t('selectFigure');
  }
  if (state.session) updateHistory(state.session.history || []);
}

function plannerRouteLabel(planner) {
  if (!planner) return '';
  if (planner.startsWith('deepseek:')) return 'AI · DeepSeek';
  if (planner.startsWith('openai:')) return 'AI · OpenAI';
  if (planner.startsWith('rules-fallback:')) return state.language === 'en' ? 'Local fallback' : '本地回退';
  if (planner.startsWith('reference:')) return state.language === 'en' ? 'Reference database' : '参考数据库';
  if (planner.startsWith('data:')) return state.language === 'en' ? 'Local data' : '本地数据';
  if (planner.startsWith('ui:')) return state.language === 'en' ? 'Direct action' : '界面操作';
  if (planner === 'rules') return t('localRules');
  if (planner.startsWith('system:')) return state.language === 'en' ? 'System' : '系统';
  return planner.includes(':') ? 'AI' : '';
}
function addMessage(role, text, planner='') {
  const item = document.createElement('article');
  item.className = `message ${role}`;
  item.innerHTML = `<span>${role === 'user' ? 'You' : 'Agent'}</span><p></p>`;
  const route = plannerRouteLabel(planner);
  if (role !== 'user' && route) item.querySelector('span').textContent = `Agent · ${route}`;
  item.querySelector('p').textContent = text;
  $('messages').appendChild(item);
  $('messages').scrollTop = $('messages').scrollHeight;
}
function switchSidebar(panel) {
  const chatting = panel === 'chat';
  $('chatPanel').hidden = !chatting;
  $('settingsPanel').hidden = chatting;
  $('chatTab').classList.toggle('active', chatting);
  $('settingsTab').classList.toggle('active', !chatting);
  $('chatTab').setAttribute('aria-selected', String(chatting));
  $('settingsTab').setAttribute('aria-selected', String(!chatting));
}
function setStatus(text, kind='', detail='') {
  $('statusText').textContent = text;
  $('statusText').parentElement.className = `status ${kind}`;
  const running = kind === 'running';
  $('renderOverlay').hidden = !running;
  if (running) {
    $('renderTitle').textContent = text;
    $('renderDetail').textContent = detail || t('preparing');
    if (!state.renderStartedAt) state.renderStartedAt = Date.now();
    clearInterval(state.renderTimer);
    const updateElapsed = () => { $('renderElapsed').textContent = t('elapsed', Math.max(0, Math.floor((Date.now()-state.renderStartedAt)/1000))); };
    updateElapsed(); state.renderTimer = setInterval(updateElapsed, 1000);
  } else {
    clearInterval(state.renderTimer); state.renderTimer = null; state.renderStartedAt = null;
  }
}
function formatBp(value) { return value >= 1e6 ? `${(value/1e6).toFixed(2)}M` : value >= 1e3 ? `${(value/1e3).toFixed(0)}k` : String(value); }
function updateSession(payload) {
  state.session = payload;
  const spec = payload.spec;
  $('figureTitle').textContent = spec.title;
  $('regionLabel').textContent = `${spec.viewport.chrom}:${formatBp(spec.viewport.start)}–${formatBp(spec.viewport.end)}`;
  $('resolutionLabel').textContent = `${formatBp(spec.analysis.resolution)} bp`;
  $('versionLabel').textContent = payload.version_id;
  $('undoButton').disabled = !payload.can_undo;
  $('redoButton').disabled = !payload.can_redo;
  if (spec.figure_type) $('figureTypeSelect').value = spec.figure_type;
  $('applyFigureType').disabled = false;
  $('applyFigureType').textContent = t('applyCurrent');
  updateHistory(payload.history || []);
  if (state.datasetScan) updateDatasetSelectionSummary();
  switchSidebar('chat');
}
function formatHistoryTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(state.language, {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function updateHistory(history) {
  $('historyCount').textContent = String(history.length);
  $('historyButton').disabled = !state.sessionId || history.length === 0;
  const list = $('historyList'); list.replaceChildren();
  for (const revision of [...history].reverse()) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `history-item${revision.current ? ' current' : ''}`;
    button.disabled = revision.current;
    button.innerHTML = `<span class="version"></span><span class="summary"></span><span class="time"></span>`;
    button.querySelector('.version').textContent = revision.version_id;
    button.querySelector('.summary').textContent = revision.summary || t('figureEdit');
    button.querySelector('.time').textContent = formatHistoryTime(revision.created_at);
    button.addEventListener('click', () => restoreVersion(revision.version_id));
    list.appendChild(button);
  }
}
function workflowReadiness(item, selectedPaths=null) {
  if (!item) return {ready:false, missing:state.language === 'en' ? 'Choose a figure type' : '请先选择图类型'};
  const files = state.datasetScan?.scan?.files || [];
  if (!state.datasetScan || !files.length) {
    return {ready:false, missing:state.language === 'en' ? 'Scan a data directory first' : '请先扫描数据目录'};
  }
  const chosen = new Set((selectedPaths || selectedDatasetPaths()).map(datasetPathKey));
  const selected = files.filter(file => file.usable && chosen.has(datasetPathKey(file.path)));
  const count = role => selected.filter(file => file.role === role && fileSupportsRole(file, role)).length;
  const hicCount = count('hic');
  const missing = [];
  const contract = item.input_contract || {};
  const roleRules = contract.roles || {};
  const pairing = contract.pairing || {};
  const anchorCount = count(pairing.anchor_role || 'hic');
  for (const [role, rule] of Object.entries(roleRules)) {
    const multiplier = rule.per_anchor ? anchorCount : 1;
    const minimum = Number(rule.min || 0) * multiplier;
    const maximum = rule.max == null ? null : Number(rule.max) * multiplier;
    const actual = count(role);
    if (actual < minimum) {
      missing.push(state.language === 'en'
        ? `${minimum} ${rule.label || role} file${minimum === 1 ? '' : 's'}`
        : `${minimum} 个${rule.label || datasetRoleLabel(role)}文件`);
    } else if (maximum != null && actual > maximum) {
      missing.push(state.language === 'en'
        ? `at most ${maximum} ${rule.label || role} file${maximum === 1 ? '' : 's'}`
        : `最多 ${maximum} 个${rule.label || datasetRoleLabel(role)}文件`);
    }
  }
  for (const choice of contract.any_of || []) {
    const actual = (choice.roles || []).reduce((sum, role) => sum + count(role), 0);
    const minimum = Number(choice.min || 1);
    if (actual < minimum) {
      const labels = (choice.roles || []).map(datasetRoleLabel).join(state.language === 'en' ? ', ' : '、');
      missing.push(state.language === 'en' ? `at least ${minimum} of ${labels}` : `从 ${labels} 中至少选择 ${minimum} 个文件`);
    }
  }
  const sharedResolutions = selectedCommonResolutions(selectedPaths);
  if (hicCount > 1 && sharedResolutions !== null && !sharedResolutions.length) {
    missing.push(state.language === 'en'
      ? 'the checked Hi-C files must share at least one resolution'
      : '已勾选的 Hi-C 必须至少有一个共同分辨率');
  }
  return {ready:missing.length === 0, missing:[...new Set(missing)].join(state.language === 'en' ? ', ' : '、')};
}
function updateWorkflowReadiness() {
  document.querySelectorAll('.workflow-item').forEach(row => {
    const item = row.workflowItem;
    if (!item) return;
    // Catalogue rows open an input chooser. Readiness is evaluated against
    // all discovered files here; the workflow-specific chooser performs the
    // authoritative check for the files the user actually selects.
    const allPaths = (state.datasetScan?.scan?.files || []).filter(file => file.usable).map(file => file.path);
    const readiness = workflowReadiness(item, allPaths);
    row.disabled = !state.datasetScan;
    const requirement = row.querySelector('span');
    const action = row.querySelector('em');
    if (requirement) requirement.textContent = readiness.ready
      ? `${t('workflowNeeds')}: ${(item.requires || []).join(state.language === 'en' ? ', ' : '、')}`
      : `${t('workflowNeeds')}: ${readiness.missing}`;
    if (action) action.textContent = readiness.ready ? t('useWorkflow') : t('missingData');
    const description = state.language === 'en' ? item.description : `${item.description}。点击后复用当前数据。`;
    row.title = readiness.ready ? description : `${description} ${state.language === 'en' ? `Missing: ${readiness.missing}` : `缺少：${readiness.missing}`}`;
  });
  updateDirectFigureReadiness();
}
function updateDirectFigureReadiness() {
  const select = $('figureTypeSelect');
  if (!select) return;
  [...select.options].forEach(option => {
    const item = state.figureTypes.find(value => value.id === option.value);
    if (!item) return;
    const readiness = state.datasetScan ? workflowReadiness(item) : {ready:true, missing:''};
    // Keep the option selectable even when its current inputs are incomplete.
    // The build button remains disabled and shows the missing requirement,
    // while this lets users switch to a two-sample figure type without losing
    // their checked files.
    option.disabled = !item.ready;
    option.title = readiness.ready ? item.description : `${item.description} ${state.language === 'en' ? `Missing: ${readiness.missing}` : `缺少：${readiness.missing}`}`;
  });
}
async function loadFigureTypes() {
  try {
    const catalog = await api('/api/figure-types');
    state.figureTypes = catalog.figure_types;
    const select = $('figureTypeSelect');
    // Conversation workflows (for example tracks_integrated) are rendered by
    // the workflow list below, not by this direct-figure selector.  Do not
    // leave the native select visually blank when such a workflow is current.
    const currentFigureType = state.session?.spec?.figure_type;
    const selectable = new Set(state.figureTypes.filter(value => value.selection_mode === 'direct').map(value => value.id));
    const selectedValue = selectable.has(currentFigureType)
      ? currentFigureType
      : selectable.has(select.value) ? select.value : 'hic_triangle';
    select.replaceChildren();
    const categories = ['basic_hic', 'comparison', 'compartment', 'tad', 'loop', 'pileup'];
    const categoryLabels = state.language === 'en'
      ? {basic_hic:'Basic Hi-C', comparison:'Sample comparison', compartment:'Compartment', tad:'TAD', loop:'Loop', pileup:'Pileup'}
      : {basic_hic:'基础 Hi-C', comparison:'样本对比', compartment:'Compartment', tad:'TAD', loop:'Loop', pileup:'聚合分析'};
    const englishLabels = {hic_triangle:'Triangular Hi-C heatmap',hic_square:'Square Hi-C heatmap',hic_oe:'Observed / Expected',hic_multi:'Two-sample square Hi-C comparison',hic_triangle_multi:'Two-sample triangular Hi-C comparison',compartment:'A/B compartment',compartment_eigenvector:'E1 eigenvector track',compartment_saddle:'Compartment saddle',tad_insulation:'TAD boundary region',tad_insulation_track:'Insulation score track',tad_boundary_square:'Square Hi-C + TAD boundaries',tad_boundary_pileup:'TAD boundary pileup',loop_heatmap:'Loop heatmap',loop_apa:'Loop APA'};
    for (const category of categories) {
      const group = document.createElement('optgroup');
      group.label = categoryLabels[category];
      for (const item of state.figureTypes.filter(value => value.category === category && value.selection_mode === 'direct')) {
        const option = document.createElement('option');
        option.value = item.id;
        const label = state.language === 'en' ? (englishLabels[item.id] || item.label) : item.label;
        option.textContent = item.ready ? label : `${label}${t('extraInput')}`;
        option.disabled = !item.ready;
        option.title = item.ready ? item.description : `${item.description} 需要：${(item.requires || []).join('、')}`;
        group.appendChild(option);
      }
      if (group.children.length) select.appendChild(group);
    }
    select.value = selectable.has(selectedValue) ? selectedValue : 'hic_triangle';
    const workflowList = $('workflowCatalog'); workflowList.replaceChildren();
    const workflowEnglish = {
      hic_multi:'Two-sample Hi-C comparison',compartment_multi:'Multi-sample compartment comparison',compartment_eigenvector:'E1 eigenvector track',compartment_saddle:'Compartment saddle',tad_multi:'Multi-sample TAD comparison',tad_boundary_pileup:'TAD boundary pileup',loop_multi:'Multi-sample loop comparison',loop_apa_multi:'Multi-sample loop APA',tracks_integrated:'Integrated Hi-C multi-omics view',tracks_signal:'BigWig signal tracks',tracks_genes:'Gene annotation tracks',tracks_intervals:'BED interval tracks',tracks_mixed:'Mixed genomic tracks',compartment_diff_scatter:'Differential compartment scatter',tad_diff_stacked:'Differential TAD boundary classes',loop_diff_stacked:'Differential loop classes',compartment_diff_region:'Differential compartment regions',tad_diff_region:'Differential TAD regions',loop_diff_region:'Differential loop regions',tad_diff_pileup:'Differential TAD boundary pileup',loop_diff_apa:'Differential loop APA'
    };
    const workflowCategories = ['comparison','compartment','tad','loop','pileup','tracks','differential'];
    const workflowCategoryLabels = state.language === 'en'
      ? {comparison:'Comparison',compartment:'Compartment',tad:'TAD',loop:'Loop',pileup:'Pileup',tracks:'Tracks',differential:'Differential analysis'}
      : {comparison:'样本对比',compartment:'Compartment',tad:'TAD',loop:'Loop',pileup:'聚合分析',tracks:'基因组轨道',differential:'差异分析'};
    for (const category of workflowCategories) {
      const items = state.figureTypes.filter(value => value.selection_mode === 'conversation' && value.category === category);
      if (!items.length) continue;
      const heading = document.createElement('b'); heading.className = 'workflow-category'; heading.textContent = workflowCategoryLabels[category]; workflowList.appendChild(heading);
      for (const item of items) {
        const row = document.createElement('button'); row.type = 'button'; row.className = 'workflow-item';
        row.workflowItem = item;
        const name = document.createElement('strong'); name.textContent = state.language === 'en' ? (workflowEnglish[item.id] || item.label) : item.label;
        const requirement = document.createElement('span');
        requirement.textContent = `${t('workflowNeeds')}: ${(item.requires || []).join(state.language === 'en' ? ', ' : '、')}`;
        const action = document.createElement('em'); action.textContent = t('useWorkflow');
        row.append(name, requirement, action);
        row.title = state.language === 'en' ? item.description : `${item.description}。点击后复用当前数据。`;
        row.addEventListener('click', () => openWorkflowConfigurator(item, workflowEnglish[item.id] || item.label));
        workflowList.appendChild(row);
      }
    }
    updateFigureTypeHint();
    enforceDatasetSelectionForFigure();
    updateWorkflowReadiness();
  } catch (_) { $('figureTypeHint').textContent = state.language === 'en' ? 'Could not load the figure type catalog.' : '无法读取图类型目录。'; }
}
function workflowRelevantRoles(item) {
  // The backend catalogue is the only source of truth.  In particular, do
  // not infer an Insulation input from the word "TAD": a boundary table and
  // a complete insulation grid are different scientific inputs even though
  // their filenames and columns may look similar.
  return new Set(item?.input_contract?.allowed_roles || []);
}

// A detected scientific role must also use the source format accepted by the
// official CFIZZ layer. In particular, ``intervals`` means a BED annotation;
// an insulation/boundary TSV is not a generic interval track. Keep this
// client-side check in sync with the server so incompatible rows never look
// selectable and the user gets feedback before pressing Build.
function fileSupportsRole(file, role=file?.role) {
  if (!file || !role) return false;
  const allowed = {
    hic: new Set(['cool', 'mcool']),
    signal: new Set(['bigwig']),
    gene_annotation: new Set(['gtf', 'gff']),
    intervals: new Set(['bed']),
    loops: new Set(['bedpe', 'loop_tsv', 'tsv']),
    compartment: new Set(['compartment_tsv', 'tsv']),
    insulation: new Set(['insulation_tsv', 'tsv']),
    boundaries: new Set(['tad_tsv', 'tsv']),
    oe: new Set(['oe_npy']),
  }[role];
  return !allowed || allowed.has(String(file.type || '').toLowerCase());
}

function compatibleRolesForFile(file) {
  return (file?.allowed_roles || (file?.role === 'unknown' ? [] : [file?.role]))
    .filter(role => fileSupportsRole(file, role));
}

function fileOverridesPayload() {
  return Object.fromEntries(Object.entries(state.fileOverrides || {}).filter(([, value]) => value && Object.keys(value).length));
}

function datasetFileByPath(path) {
  const key = datasetPathKey(path);
  return (state.datasetScan?.scan?.files || []).find(file => datasetPathKey(file.path) === key) || null;
}

function compactSample(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function suggestedCompanion(anchor, candidates, usedPaths=new Set()) {
  const anchorKey = compactSample(anchor.sample || anchor.auto_sample || anchor.name);
  const scored = candidates
    .filter(candidate => !usedPaths.has(datasetPathKey(candidate.path)))
    .map(candidate => {
      const candidateKey = compactSample(candidate.sample || candidate.auto_sample || candidate.name);
      const score = anchorKey && candidateKey === anchorKey ? 3
        : anchorKey && candidateKey.includes(anchorKey) ? 2
          : candidateKey && anchorKey.includes(candidateKey) ? 1 : 0;
      return {candidate, score};
    })
    .sort((a, b) => b.score - a.score || a.candidate.name.localeCompare(b.candidate.name));
  if (!scored.length || scored[0].score === 0) return null;
  if (scored.length > 1 && scored[0].score === scored[1].score) return null;
  return scored[0].candidate;
}

function openWorkflowConfigurator(item, displayLabel) {
  const panel = $('workflowConfigurator');
  const files = (state.datasetScan?.scan?.files || []).filter(file => file.usable && file.role !== 'unknown');
  if (!files.length) {
    switchSidebar('settings');
    $('workflowActionHint').textContent = t('loadFigureFirst');
    $('workflowActionHint').hidden = false;
    return;
  }
  const roles = workflowRelevantRoles(item);
  const candidates = files.filter(file => roles.has(file.role) && fileSupportsRole(file, file.role));
  const globallySelected = new Set(selectedDatasetPaths().map(datasetPathKey));
  const contract = item.input_contract || {};
  const roleRules = contract.roles || {};
  const pairing = contract.pairing || null;
  state.activeWorkflow = {item, displayLabel};
  panel.replaceChildren(); panel.hidden = false;
  const header = document.createElement('div'); header.className = 'workflow-config-header';
  const title = document.createElement('strong'); title.textContent = `${t('workflowInputs')}：${displayLabel}`;
  const controls = document.createElement('span');
  const recommended = document.createElement('button'); recommended.type = 'button'; recommended.textContent = t('selectRecommended');
  const all = document.createElement('button'); all.type = 'button'; all.textContent = t('selectAll');
  const clear = document.createElement('button'); clear.type = 'button'; clear.textContent = t('clearAll');
  controls.append(recommended, all, clear); header.append(title, controls); panel.appendChild(header);
  const list = document.createElement('div'); list.className = 'workflow-input-list'; panel.appendChild(list);
  const status = document.createElement('small'); status.className = 'workflow-config-status'; panel.appendChild(status);
  const build = document.createElement('button'); build.type = 'button'; build.className = 'primary workflow-config-build'; panel.appendChild(build);

  if (pairing) {
    const anchorRole = pairing.anchor_role || 'hic';
    const companionRoles = pairing.companion_roles || [];
    const anchors = candidates.filter(file => file.role === anchorRole);
    const anchorRule = roleRules[anchorRole] || {min:1, max:null};
    const rows = [];
    for (const anchor of anchors) {
      const row = document.createElement('div'); row.className = 'workflow-pair-row'; row.dataset.anchorPath = anchor.path;
      const anchorLabel = document.createElement('label'); anchorLabel.className = 'workflow-pair-anchor';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.dataset.workflowAnchor = anchor.path;
      const anchorName = document.createElement('span'); anchorName.textContent = anchor.name;
      anchorLabel.append(checkbox, anchorName); row.appendChild(anchorLabel);
      const sample = document.createElement('input'); sample.type = 'text'; sample.className = 'workflow-sample-name'; sample.value = anchor.sample || anchor.auto_sample || anchor.name.replace(/\.[^.]+$/, ''); sample.placeholder = t('sampleName'); sample.maxLength = 120; row.appendChild(sample);
      const selectors = {};
      for (const role of companionRoles) {
        const select = document.createElement('select'); select.className = 'workflow-companion-select'; select.dataset.companionRole = role;
        const empty = document.createElement('option'); empty.value = ''; empty.textContent = state.language === 'en' ? `Choose ${datasetRoleLabel(role)}` : `选择${datasetRoleLabel(role)}`; select.appendChild(empty);
        for (const file of candidates.filter(value => value.role === role)) {
          const option = document.createElement('option'); option.value = file.path; option.textContent = `${file.name}${file.sample ? ` · ${file.sample}` : ''}`; select.appendChild(option);
        }
        selectors[role] = select;
        row.appendChild(select);
      }
      rows.push({row, anchor, checkbox, sample, selectors});
      list.appendChild(row);
    }
    const confirmation = document.createElement('label'); confirmation.className = 'workflow-pair-confirm';
    const confirmInput = document.createElement('input'); confirmInput.type = 'checkbox';
    confirmation.append(confirmInput, document.createTextNode(t('confirmPairing'))); list.appendChild(confirmation);

    const selectSuggestions = () => {
      const used = new Set();
      for (const entry of rows.filter(value => value.checkbox.checked)) {
        for (const role of companionRoles) {
          const current = entry.selectors[role].value;
          if (current && !used.has(datasetPathKey(current))) {
            used.add(datasetPathKey(current));
            continue;
          }
          const suggestion = suggestedCompanion(entry.anchor, candidates.filter(file => file.role === role), used);
          entry.selectors[role].value = suggestion?.path || '';
          if (suggestion) used.add(datasetPathKey(suggestion.path));
        }
      }
    };
    const pairingData = () => {
      const selectedRows = rows.filter(entry => entry.checkbox.checked);
      const paths = [];
      const bindings = [];
      const usedCompanions = new Set();
      let complete = selectedRows.length >= Number(anchorRule.min || 0) && (anchorRule.max == null || selectedRows.length <= Number(anchorRule.max));
      let duplicate = false;
      for (const entry of selectedRows) {
        paths.push(entry.anchor.path);
        const companions = {};
        for (const role of companionRoles) {
          const companionPath = entry.selectors[role].value;
          if (!companionPath) complete = false;
          else {
            const key = datasetPathKey(companionPath);
            if (usedCompanions.has(key)) duplicate = true;
            usedCompanions.add(key);
            companions[role] = companionPath;
            paths.push(companionPath);
          }
        }
        if (!entry.sample.value.trim()) complete = false;
        bindings.push({sample:entry.sample.value.trim(), anchor_role:anchorRole, anchor_path:entry.anchor.path, companions});
      }
      return {paths:[...new Set(paths)], bindings, complete, duplicate};
    };
    const refresh = () => {
      const data = pairingData();
      const readiness = workflowReadiness(item, data.paths);
      const confirmed = !pairing.requires_confirmation || confirmInput.checked;
      const ready = readiness.ready && data.complete && !data.duplicate && confirmed;
      const problem = data.duplicate
        ? (state.language === 'en' ? 'A companion file cannot be assigned to two samples.' : '同一个配套文件不能分配给两个样本。')
        : !data.complete
          ? (state.language === 'en' ? 'Select every required companion and enter each sample name.' : '请为每个样本选择完整的配套文件并填写样本名。')
          : !confirmed
            ? t('confirmPairing')
            : readiness.missing;
      status.textContent = ready
        ? (state.language === 'en' ? `${data.bindings.length} sample pairings confirmed. Ready to build.` : `已确认 ${data.bindings.length} 组样本对应关系，可以生成。`)
        : (state.language === 'en' ? `Not ready: ${problem}` : `尚不能生成：${problem}`);
      status.classList.toggle('error', !ready);
      build.disabled = !ready;
      build.textContent = `${t('buildWorkflow')}「${displayLabel}」`;
    };
    const setAnchors = mode => {
      const maximum = anchorRule.max == null ? Number.POSITIVE_INFINITY : Number(anchorRule.max);
      const minimum = Number(anchorRule.min || 0);
      let selectedCount = 0;
      for (const entry of rows) {
        const preferred = globallySelected.has(datasetPathKey(entry.anchor.path));
        entry.checkbox.checked = mode === 'all' || mode === 'recommended' && preferred;
        if (entry.checkbox.checked && selectedCount >= maximum) entry.checkbox.checked = false;
        if (entry.checkbox.checked) selectedCount += 1;
      }
      if (mode === 'recommended' && selectedCount < minimum) {
        for (const entry of rows) {
          if (selectedCount >= minimum || selectedCount >= maximum) break;
          if (!entry.checkbox.checked) { entry.checkbox.checked = true; selectedCount += 1; }
        }
      }
      if (mode === 'clear') rows.forEach(entry => { entry.checkbox.checked = false; });
      confirmInput.checked = false;
      selectSuggestions();
      refresh();
    };
    recommended.addEventListener('click', () => setAnchors('recommended'));
    all.addEventListener('click', () => setAnchors('all'));
    clear.addEventListener('click', () => setAnchors('clear'));
    list.addEventListener('change', event => {
      if (event.target !== confirmInput) confirmInput.checked = false;
      if (event.target.matches('input[data-workflow-anchor]')) selectSuggestions();
      refresh();
    });
    list.addEventListener('input', event => {
      if (event.target.matches('.workflow-sample-name')) {
        confirmInput.checked = false;
        const file = datasetFileByPath(event.target.closest('.workflow-pair-row')?.dataset.anchorPath);
        if (file) {
          file.sample = event.target.value.trim() || file.auto_sample || null;
          state.fileOverrides[file.path] = {...(state.fileOverrides[file.path] || {}), sample:file.sample || ''};
        }
        refresh();
      }
    });
    build.addEventListener('click', () => {
      const data = pairingData();
      startWorkflow(item, displayLabel, data.paths, data.bindings, confirmInput.checked);
    });
    setAnchors('recommended');
  } else {
    for (const file of candidates) {
      const label = document.createElement('label'); label.className = 'workflow-input-file'; label.title = file.path;
      const input = document.createElement('input'); input.type = 'checkbox'; input.dataset.workflowPath = file.path;
      const name = document.createElement('span'); name.textContent = file.name;
      const meta = document.createElement('small'); meta.textContent = `${datasetRoleLabel(file.role)}${file.sample ? ` · ${file.sample}` : ''}`;
      label.append(input, name, meta); list.appendChild(label);
    }
    const workflowPaths = () => [...list.querySelectorAll('input[data-workflow-path]:checked')].map(input => input.dataset.workflowPath);
    const refresh = () => {
      const readiness = workflowReadiness(item, workflowPaths());
      status.textContent = readiness.ready
        ? (state.language === 'en' ? `${workflowPaths().length} compatible files selected. Ready to build.` : `已选择 ${workflowPaths().length} 个兼容文件，可以生成。`)
        : (state.language === 'en' ? `Missing: ${readiness.missing}` : `还缺少：${readiness.missing}`);
      status.classList.toggle('error', !readiness.ready);
      build.disabled = !readiness.ready;
      build.textContent = `${t('buildWorkflow')}「${displayLabel}」`;
    };
    const setChecks = mode => {
      const selectedPerRole = {};
      list.querySelectorAll('input[data-workflow-path]').forEach(input => {
        const file = datasetFileByPath(input.dataset.workflowPath);
        const rule = roleRules[file?.role] || {min:0, max:null};
        let checked = mode === 'all' || mode === 'recommended' && globallySelected.has(datasetPathKey(file?.path));
        const roleCount = selectedPerRole[file?.role] || 0;
        if (checked && rule.max != null && roleCount >= Number(rule.max)) checked = false;
        input.checked = checked;
        if (checked) selectedPerRole[file.role] = roleCount + 1;
      });
      if (mode === 'recommended') {
        for (const [role, rule] of Object.entries(roleRules)) {
          let count = list.querySelectorAll(`input[data-workflow-path]:checked`).length
            ? workflowPaths().map(datasetFileByPath).filter(file => file?.role === role).length : 0;
          for (const input of list.querySelectorAll('input[data-workflow-path]')) {
            const file = datasetFileByPath(input.dataset.workflowPath);
            if (file?.role !== role || input.checked || count >= Number(rule.min || 0)) continue;
            input.checked = true; count += 1;
          }
        }
      }
      refresh();
    };
    recommended.addEventListener('click', () => setChecks('recommended'));
    all.addEventListener('click', () => setChecks('all'));
    clear.addEventListener('click', () => setChecks('clear'));
    list.addEventListener('change', refresh);
    build.addEventListener('click', () => startWorkflow(item, displayLabel, workflowPaths()));
    setChecks('recommended');
  }
  panel.scrollIntoView({behavior:'smooth', block:'nearest'});
}
async function startWorkflow(item, displayLabel, workflowPaths=selectedDatasetPaths(), workflowBindings=[], pairingsConfirmed=false) {
  const hint = $('workflowActionHint');
  const readiness = workflowReadiness(item, workflowPaths);
  if (!readiness.ready) {
    const message = state.language === 'en' ? `This workflow is not ready. Missing: ${readiness.missing}.` : `这个工作流还不能生成，缺少：${readiness.missing}。`;
    hint.textContent = message;
    hint.hidden = false;
    setStatus(state.language === 'en' ? 'Workflow needs more data' : '工作流需要补充数据', 'failed');
    addMessage('assistant', message, 'ui:workflow');
    return;
  }
  const selectedHicCount = state.datasetScan?.scan?.files
    ? state.datasetScan.scan.files.filter(file => file.usable && file.role === 'hic' && workflowPaths.map(datasetPathKey).includes(datasetPathKey(file.path))).length
    : (state.session?.spec?.data_sources || []).filter(source => ['cool', 'mcool'].includes(source.type)).length;
  if (item.id === 'hic_multi' && selectedHicCount < 2) {
    const message = state.language === 'en'
      ? 'Multi-sample Hi-C comparison needs at least two checked .cool/.mcool files.'
      : '多样本 Hi-C 对比至少需要勾选两个 .cool/.mcool 文件。';
    hint.textContent = message;
    hint.hidden = false;
    setStatus(state.language === 'en' ? 'Workflow needs more data' : '工作流需要补充数据', 'failed');
    addMessage('assistant', message, 'ui:workflow');
    return;
  }
  if (!state.sessionId && state.datasetScan) {
    try {
      hint.hidden = true;
      setStatus(t('workflowBuilding'), 'running', t('preparing'));
      const sessionId = `workflow_${Date.now()}`;
      const payload = await api('/api/sessions/from-dataset', {
        method:'POST',
        body:JSON.stringify({session_id:sessionId, path:state.datasetScan.path, gene:state.datasetScan.gene, reference_build:$('referenceBuild').value, selected_paths:workflowPaths, figure_type:item.id, window:500000, resolution:selectedResolutionValue(), file_overrides:fileOverridesPayload(), workflow_bindings:workflowBindings, pairings_confirmed:pairingsConfirmed, render:true})
      });
      state.sessionId = payload.session_id;
      updateSession(payload);
      $('chatInput').disabled = false;
      $('chatForm').querySelector('button').disabled = false;
      const sourceNames = (payload.spec?.data_sources || [])
        .map(source => source.label || source.path?.split(/[\\/]/).pop())
        .filter(Boolean);
      const sourceSummary = sourceNames.length ? sourceNames.join(state.language === 'en' ? ', ' : '、') : (state.language === 'en' ? 'the selected compatible inputs' : '已选兼容数据');
      addMessage('assistant', `${t('workflowCreated')} ${displayLabel}。${state.language === 'en' ? `Using: ${sourceSummary}.` : `使用数据：${sourceSummary}。`}`, 'data:workflow');
      watchJob(payload.job);
    } catch (error) {
      hint.textContent = error.message;
      hint.hidden = false;
      const missingInputs = error.status === 422;
      setStatus(
        missingInputs
          ? (state.language === 'en' ? 'Workflow needs more data' : '工作流需要补充数据')
          : (state.language === 'en' ? 'Workflow failed' : '工作流执行失败'),
        'failed',
      );
      addMessage('assistant', error.message, 'ui:workflow');
    }
    return;
  }
  if (!state.sessionId) {
    switchSidebar('settings');
    $('workflowCatalog').closest('details').open = true;
    hint.textContent = t('loadFigureFirst');
    hint.hidden = false;
    const target = $('dataPath');
    target.focus();
    target.scrollIntoView({behavior:'smooth', block:'center'});
    return;
  }
  try {
    setStatus(state.language === 'en' ? 'Starting workflow' : '正在启动工作流', 'running', t('preparing'));
    const payload = await api(`/api/sessions/${state.sessionId}/workflow`, {
      method:'POST',
      body:JSON.stringify({
        figure_type:item.id,
        source_ids:selectedSourceIdsForSession(workflowPaths),
        dataset_path:state.datasetScan?.path || null,
        selected_paths:workflowPaths,
        gene:state.datasetScan?.gene || datasetQuery() || null,
        reference_build:$('referenceBuild').value,
        resolution:selectedResolutionValue(),
        file_overrides:fileOverridesPayload(),
        workflow_bindings:workflowBindings,
        pairings_confirmed:pairingsConfirmed,
        options:{},
        render:true,
      })
    });
    const sourceNames = (payload.spec?.data_sources || []).map(source => source.label || source.path?.split(/[\\/]/).pop()).filter(Boolean);
    const sourceSummary = sourceNames.length ? sourceNames.join(state.language === 'en' ? ', ' : '、') : (state.language === 'en' ? 'the current compatible inputs' : '当前兼容数据');
    addMessage('assistant', `${state.language === 'en' ? 'Started' : '已启动'} ${displayLabel}。${state.language === 'en' ? `Using: ${sourceSummary}.` : `使用数据：${sourceSummary}。`}`, 'ui:workflow');
    updateSession(payload);
    watchJob(payload.job);
  } catch (error) {
    const missingInputs = error.status === 422;
    setStatus(
      missingInputs
        ? (state.language === 'en' ? 'Workflow needs more data' : '工作流需要补充数据')
        : (state.language === 'en' ? 'Workflow failed' : '工作流执行失败'),
      'failed',
    );
    addMessage('assistant', error.message, 'ui:workflow');
  }
}
function updateFigureTypeHint() {
  const item = state.figureTypes.find(value => value.id === $('figureTypeSelect').value);
  if (item) {
    const english = {
      hic_triangle:'Triangular Hi-C contact map for regional chromatin structure.', hic_square:'Square Hi-C contact matrix.',
      hic_oe:'Observed/expected contact enrichment map.', hic_multi:'Side-by-side square comparison of exactly two selected Hi-C samples.', hic_triangle_multi:'FOXJ1-style mirrored triangular comparison of exactly two selected Hi-C samples; tracks are optional.', compartment:'A/B compartment heatmap with the E1 track.',
      compartment_eigenvector:'Standalone E1 compartment eigenvector track.', compartment_saddle:'A/B compartment interaction saddle plot.', tad_insulation:'Hi-C map with TAD/insulation annotations.', tad_insulation_track:'Standalone insulation-score curve and boundary marks.', tad_boundary_square:'Square Hi-C matrix with TAD boundary rows and columns.', tad_boundary_pileup:'Aggregate signal around TAD boundaries.', loop_heatmap:'Hi-C map with loop calls.', loop_apa:'Aggregate peak analysis of loop calls.'
    };
    $('figureTypeHint').textContent = state.language === 'en' ? (english[item.id] || item.description) : item.description;
  }
}
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  let data = {};
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) {
    const versionMismatch = response.status === 404 && path.includes('/workflow');
    const error = new Error(versionMismatch
      ? (state.language === 'en' ? 'The page is newer than the running server. Restart CFIZZ Agent, then refresh the page.' : '当前网页比正在运行的服务端新。请重启 CFIZZ Agent 服务，然后刷新页面。')
      : (data.detail || `请求失败（HTTP ${response.status}）`));
    error.status = response.status;
    error.versionMismatch = versionMismatch;
    throw error;
  }
  return data;
}
async function checkServerCompatibility() {
  try {
    const health = await api('/api/health');
    if ((health.api_revision || 0) < EXPECTED_API_REVISION) {
      const message = state.language === 'en'
        ? 'The server is still running an older build. Restart CFIZZ Agent before creating figures.'
        : '当前服务端仍是旧版本，请先重启 CFIZZ Agent，再生成图形。';
      setStatus(state.language === 'en' ? 'Restart server' : '请重启服务', 'failed');
      const hint = $('workflowActionHint');
      hint.textContent = message;
      hint.hidden = false;
    }
  } catch (_) {}
}
async function loadPlannerStatus() {
  try {
    const catalog = await api('/api/planners');
    applyPlannerCatalog(catalog);
  } catch (_) {
    $('plannerBadge').textContent = t('unknownStatus');
    $('plannerHint').textContent = state.language === 'en' ? 'Could not load the API configuration.' : '无法读取 API 配置。';
  }
}
function applyPlannerCatalog(catalog, preferredProvider=null) {
  state.planners = catalog.providers;
  const select = $('plannerSelect');
  select.replaceChildren();
  for (const planner of state.planners) {
    const option = document.createElement('option');
    option.value = planner.id;
    const label = state.language === 'en' && planner.id === 'local' ? 'Local rules' : planner.label;
    option.textContent = planner.available ? `${label}${planner.model ? ` · ${planner.model}` : ''}` : (state.language === 'en' ? `${label} (not configured)` : `${label}（未配置）`);
    option.disabled = !planner.available;
    option.title = state.language === 'en' && !planner.available ? 'Not configured.' : (planner.reason || '');
    select.appendChild(option);
  }
  const requested = preferredProvider || catalog.default_provider;
  state.provider = state.planners.some(item => item.id === requested && item.available) ? requested : 'local';
  select.value = state.provider;
  updatePlannerDisplay();
  updateApiConfigDisplay();
}
function updatePlannerDisplay() {
  const planner = state.planners.find(item => item.id === state.provider);
  if (!planner) return;
  const badge = $('plannerBadge');
  badge.textContent = planner.mode === 'ai-assisted' ? `AI · ${planner.label}` : t('localRules');
  badge.classList.toggle('ai', planner.mode === 'ai-assisted');
  badge.title = planner.privacy || planner.reason || '';
  $('plannerHint').textContent = planner.mode === 'ai-assisted'
    ? (state.language === 'en' ? `AI interpretation · ${planner.label} ${planner.model}` : `AI 理解 · ${planner.label} ${planner.model}`)
    : (state.language === 'en' ? 'Local rules support common commands only.' : '本地规则仅支持常用指令。');
  $('settingsSummary').textContent = planner.mode === 'ai-assisted' ? `${planner.label} · AI` : (state.language === 'en' ? 'Local rules · limited' : '本地规则 · 有限能力');
}
function updateApiConfigDisplay() {
  const provider = $('apiProvider').value;
  const planner = state.planners.find(item => item.id === provider);
  const defaults = {deepseek:'deepseek-chat', openai:'gpt-5.6-terra'};
  $('apiModel').placeholder = planner?.model || defaults[provider];
  $('removeApiConfig').disabled = !planner?.available;
}
function setApiConfigStatus(text, kind='') {
  const target = $('apiConfigStatus');
  target.textContent = text;
  target.className = kind;
}
async function watchJob(job) {
  if (!job) return;
  state.activeJob = job.job_id;
  for (const id of ['svgDownload','pngDownload','pdfDownload']) {
    $(id).href = '#'; $(id).classList.add('disabled');
  }
  state.renderStartedAt = Date.now();
  setStatus(t('rendering'), 'running', state.language === 'en' ? 'Task submitted; waiting for the CFIZZ renderer…' : '任务已提交，正在等待绘图引擎……');
  while (state.activeJob === job.job_id) {
    const current = await api(`/api/jobs/${job.job_id}`);
    if (current.status === 'queued') setStatus(t('queued'), 'running', state.language === 'en' ? 'The task is queued and will start shortly.' : '任务已进入队列，即将开始。');
    if (current.status === 'running') setStatus(t('drawing'), 'running', state.language === 'en' ? 'Reading region data, laying out tracks, and exporting files…' : '正在读取区域数据、布局轨道并导出图片……');
    if (current.status === 'succeeded') {
      const png = current.artifact_urls.find(path => path.endsWith('.png'));
      const svg = current.artifact_urls.find(path => path.endsWith('.svg'));
      const pdf = current.artifact_urls.find(path => path.endsWith('.pdf'));
      if (png) { $('figureImage').src = `${png}?t=${Date.now()}`; $('figureImage').hidden=false; $('emptyState').hidden=true; $('canvas').classList.remove('empty'); }
      if (svg) { $('svgDownload').href=svg; $('svgDownload').classList.remove('disabled'); }
      if (png) { $('pngDownload').href=png; $('pngDownload').classList.remove('disabled'); }
      if (pdf) { $('pdfDownload').href=pdf; $('pdfDownload').classList.remove('disabled'); }
      setStatus(t('completed'), 'success');
      $('canvas').classList.remove('just-completed'); void $('canvas').offsetWidth; $('canvas').classList.add('just-completed');
      state.activeJob=null; return;
    }
    if (current.status === 'failed') { setStatus(t('failed'), 'failed'); addMessage('assistant', current.error); state.activeJob=null; return; }
    await new Promise(resolve => setTimeout(resolve, 800));
  }
}
$('chatTab').addEventListener('click', () => switchSidebar('chat'));
$('settingsTab').addEventListener('click', () => switchSidebar('settings'));
$('historyButton').addEventListener('click', () => { $('historyPanel').hidden = !$('historyPanel').hidden; });
$('closeHistory').addEventListener('click', () => { $('historyPanel').hidden = true; });
async function restoreVersion(versionId) {
  if (!state.sessionId) return;
  try {
    $('historyPanel').hidden = true;
    setStatus(`正在恢复 ${versionId}`, 'running', '正在载入历史参数并重新生成图形……');
    const payload = await api(`/api/sessions/${state.sessionId}/restore/${versionId}`, {method:'POST'});
    updateSession(payload);
    addMessage('assistant', `已恢复到 ${versionId}：${payload.history.find(item => item.current)?.summary || '历史版本'}。`);
    watchJob(payload.job);
  } catch(error) { setStatus('恢复失败', 'failed'); addMessage('assistant', error.message); }
}
$('demoButton').addEventListener('click', async () => {
  try {
    setStatus('正在载入示例','running','正在读取 FOXJ1 示例数据并建立第一版图形……');
    const payload = await api('/api/sessions/demo', {method:'POST', body:JSON.stringify({session_id:'foxj1_web'})});
    state.sessionId=payload.session_id; updateSession(payload);
    $('chatInput').disabled=false; $('chatForm').querySelector('button').disabled=false;
    addMessage('assistant','已载入 FOXJ1 多组学示例。你可以直接描述想修改的地方。');
    watchJob(payload.job);
  } catch(error) { setStatus('载入失败'); addMessage('assistant', error.message); }
});
$('dataForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const path = $('dataPath').value.trim();
  if (!path) return;
  if (/\.(?:m?cool)$/i.test(path)) {
    try {
      setStatus('正在检查数据','running','正在读取文件元数据并选择合适的分辨率……');
      const sessionId=`hic_${Date.now()}`;
      const payload=await api('/api/sessions/from-hic',{method:'POST',body:JSON.stringify({session_id:sessionId,hic_path:path,region:datasetQuery(),figure_type:$('figureTypeSelect').value})});
      state.sessionId=payload.session_id; updateSession(payload);
      $('chatInput').disabled=false; $('chatForm').querySelector('button').disabled=false;
      const item = currentFigureTypeItem();
      const fileName = path.split(/[\\/]/).pop();
      addMessage('assistant', state.language === 'en'
        ? `Creating: ${item?.label || payload.spec.title}\nHi-C: ${fileName}`
        : `正在生成：${item?.label || payload.spec.title}\n使用 Hi-C：${fileName}`, 'data:file');
      watchJob(payload.job);
    } catch(error) { setStatus('数据不可用'); addMessage('assistant',error.message); }
    return;
  }
  await scanDatasetPath(path);
});
function datasetRoleLabel(role) {
  const labels = state.language === 'en'
    ? {hic:'Hi-C', signal:'BigWig signal', gene_annotation:'Gene annotation', intervals:'BED intervals', loops:'Loops', compartment:'Compartment/E1', insulation:'Insulation', boundaries:'TAD boundaries', oe:'Observed/Expected'}
    : {hic:'Hi-C', signal:'BigWig 信号', gene_annotation:'基因注释', intervals:'BED 区间', loops:'Loop', compartment:'Compartment/E1', insulation:'Insulation', boundaries:'TAD 边界', oe:'Observed/Expected'};
  return labels[role] || role;
}
function selectedDatasetPaths() {
  return [...document.querySelectorAll('#datasetFiles input[data-dataset-path]:checked')].map(input => input.dataset.datasetPath);
}
function currentFigureTypeItem() {
  return state.figureTypes.find(value => value.id === $('figureTypeSelect').value) || null;
}
function hicInputsForDataset() {
  const hicPaths = new Set((state.datasetScan?.scan?.files || []).filter(file => file.usable && file.role === 'hic').map(file => file.path));
  return [...document.querySelectorAll('#datasetFiles input[data-dataset-path]')].filter(input => hicPaths.has(input.dataset.datasetPath));
}
function selectedHicFiles(selectedPaths=null) {
  const selected = new Set((selectedPaths || selectedDatasetPaths()).map(datasetPathKey));
  return (state.datasetScan?.scan?.files || []).filter(file => file.usable && file.role === 'hic' && selected.has(datasetPathKey(file.path)));
}
function selectedCommonResolutions(selectedPaths=null) {
  const files = selectedHicFiles(selectedPaths);
  // Older scan payloads did not expose native grids. Do not block those
  // payloads in the browser; the authoritative server validator still checks
  // them before rendering.
  if (!files.length || files.some(file => !Array.isArray(file.resolutions) || !file.resolutions.length)) return null;
  const sets = files.map(file => new Set(file.resolutions.map(Number)));
  return [...sets.reduce((shared, values) => new Set([...shared].filter(value => values.has(value))))].sort((a, b) => a - b);
}
function updateDatasetResolutionControl() {
  const control = $('datasetResolution');
  const select = $('datasetResolutionSelect');
  const hint = $('datasetResolutionHint');
  if (!control || !select) return;
  const files = selectedHicFiles();
  const shared = selectedCommonResolutions();
  if (!files.length || shared === null) {
    control.hidden = true;
    return;
  }
  control.hidden = false;
  const previous = select.value;
  select.replaceChildren();
  const automatic = document.createElement('option');
  automatic.value = 'auto'; automatic.textContent = t('resolutionAuto'); select.appendChild(automatic);
  for (const value of shared) {
    const option = document.createElement('option');
    option.value = String(value); option.textContent = `${formatBp(value)} bp`;
    select.appendChild(option);
  }
  select.value = previous === 'auto' || shared.includes(Number(previous)) ? previous : 'auto';
  if (!shared.length) {
    hint.textContent = state.language === 'en'
      ? 'The checked Hi-C files have no shared resolution. Reduce the selection or add a matching matrix.'
      : '当前勾选的 Hi-C 没有共同分辨率，请减少样本或补充匹配的矩阵。';
    hint.classList.add('error');
  } else {
    hint.textContent = state.language === 'en'
      ? `Shared by ${files.length} checked Hi-C file${files.length === 1 ? '' : 's'}: ${shared.map(value => `${formatBp(value)} bp`).join(', ')}.`
      : `${files.length} 个已勾选 Hi-C 的共同分辨率：${shared.map(value => `${formatBp(value)} bp`).join('、')}。`;
    hint.classList.remove('error');
  }
}
function selectedResolutionValue() {
  const value = $('datasetResolutionSelect')?.value;
  return value && value !== 'auto' ? Number(value) : null;
}
function hicCardinality(item=currentFigureTypeItem()) {
  return item?.input_cardinality?.hic || null;
}
function cardinalityText(rule) {
  if (!rule) return state.language === 'en' ? 'compatible inputs' : '兼容数据';
  if (rule.max != null && rule.min === rule.max) return state.language === 'en' ? `exactly ${rule.min} Hi-C file${rule.min === 1 ? '' : 's'}` : `${rule.min} 个 Hi-C 文件`;
  if (rule.max == null) return state.language === 'en' ? `at least ${rule.min} Hi-C files` : `至少 ${rule.min} 个 Hi-C 文件`;
  return state.language === 'en' ? `${rule.min}–${rule.max} Hi-C files` : `${rule.min}–${rule.max} 个 Hi-C 文件`;
}
function enforceDatasetSelectionForFigure(preferredInput=null, fillMinimum=false) {
  const item = currentFigureTypeItem();
  const contract = item?.input_contract || {};
  const roleRules = contract.roles || {};
  const allowedRoles = workflowRelevantRoles(item);
  const inputs = [...document.querySelectorAll('#datasetFiles input[data-dataset-path]')];
  const fileFor = input => datasetFileByPath(input.dataset.datasetPath);

  for (const input of inputs) {
    const file = fileFor(input);
    const compatible = Boolean(
      file
      && allowedRoles.has(file.role)
      && fileSupportsRole(file, file.role)
    );
    input.disabled = !compatible;
    if (!compatible) input.checked = false;
  }
  for (const [role, rule] of Object.entries(roleRules)) {
    const roleInputs = inputs.filter(input => !input.disabled && fileFor(input)?.role === role);
    let checked = roleInputs.filter(input => input.checked);
    if (rule.max != null && checked.length > Number(rule.max)) {
      const keep = [];
      if (preferredInput && checked.includes(preferredInput)) keep.push(preferredInput);
      for (const input of checked) {
        if (keep.length >= Number(rule.max)) break;
        if (!keep.includes(input)) keep.push(input);
      }
      const keepSet = new Set(keep);
      checked.forEach(input => { input.checked = keepSet.has(input); });
      checked = keep;
    }
    if (fillMinimum) {
      for (const input of roleInputs) {
        if (checked.length >= Number(rule.min || 0)) break;
        if (!input.checked) { input.checked = true; checked.push(input); }
      }
    }
  }
  if (fillMinimum) {
    for (const choice of contract.any_of || []) {
      const choiceInputs = inputs.filter(input => !input.disabled && (choice.roles || []).includes(fileFor(input)?.role));
      let count = choiceInputs.filter(input => input.checked).length;
      for (const input of choiceInputs) {
        if (count >= Number(choice.min || 1)) break;
        if (!input.checked) { input.checked = true; count += 1; }
      }
    }
  }
  updateDatasetSelectionSummary();
}
function selectedSourceIdsForSession(selectedPaths=null) {
  const normalise = value => String(value || '').replaceAll('\\', '/').toLowerCase();
  const selected = new Set((selectedPaths || selectedDatasetPaths()).map(normalise));
  if (!selected.size) return [];
  return (state.session?.spec?.data_sources || [])
    .filter(source => selected.has(normalise(source.path)))
    .map(source => source.id)
    .filter(Boolean);
}
function datasetPathKey(value) {
  return String(value || '').replaceAll('\\', '/').replace(/\/+$/, '').toLowerCase();
}
function selectedTrackFiles() {
  const scanFiles = state.datasetScan?.scan?.files || [];
  const selected = new Set(selectedDatasetPaths().map(datasetPathKey));
  return scanFiles.filter(file => (
    file.usable
    && ['signal', 'intervals'].includes(file.role)
    && fileSupportsRole(file, file.role)
    && selected.has(datasetPathKey(file.path))
  ));
}
function newSelectedTrackFiles() {
  const existing = new Set((state.session?.spec?.data_sources || []).map(source => datasetPathKey(source.path)));
  return selectedTrackFiles().filter(file => !existing.has(datasetPathKey(file.path)));
}
function updateDatasetSelectionSummary() {
  const picker = $('datasetFiles').querySelector('.dataset-file-picker');
  if (!picker) { updateDatasetResolutionControl(); updateWorkflowReadiness(); return; }
  const item = currentFigureTypeItem();
  const rule = hicCardinality(item);
  const selectedHics = hicInputsForDataset().filter(input => input.checked);
  const selectedNames = selectedHics.map(input => input.closest('.dataset-file')?.querySelector('.dataset-file-name')?.textContent || input.dataset.datasetPath.split(/[\\/]/).pop());
  const selectedTracks = selectedTrackFiles();
  const selectedTrackNames = selectedTracks.map(file => file.name);
  updateDatasetResolutionControl();
  const sharedResolutions = selectedCommonResolutions();
  // Capability comes from the CFIZZ catalogue.  Do not infer it from the
  // word "triangle": TAD integrated views can also accept tracks, while
  // square/OE/compartment APIs intentionally do not.
  const trackFigureSupported = item?.track_mode === 'integrated';
  const count = picker.querySelector('.dataset-selection-count');
  const comparisonHint = selectedHics.length >= 2 && rule?.max === 1
    ? (state.language === 'en'
      ? '\nTip: choose “Two-sample Hi-C comparison” to use both.'
      : '\n提示：要对比两个样本，请选择“双样本 Hi-C 对比”。')
    : '';
  if (count) count.textContent = state.language === 'en'
    ? `Figure: ${item?.label || $('figureTypeSelect').value}\nRequired: ${cardinalityText(rule)}\nHi-C: ${selectedNames.join(', ') || 'none'}\nShared resolution: ${sharedResolutions?.length ? sharedResolutions.map(value => `${formatBp(value)} bp`).join(', ') : sharedResolutions === null ? 'server validation' : 'none'}\nTracks: ${selectedTrackNames.join(', ') || 'none'}${comparisonHint}`
    : `要画：${item?.label || $('figureTypeSelect').value}\n需要：${cardinalityText(rule)}\nHi-C：${selectedNames.join('、') || '无'}\n共同分辨率：${sharedResolutions?.length ? sharedResolutions.map(value => `${formatBp(value)} bp`).join('、') : sharedResolutions === null ? '交由服务端校验' : '无'}\n轨道：${selectedTrackNames.join('、') || '无'}${comparisonHint}`;
  const newTracks = newSelectedTrackFiles();
  const validHicCount = !rule || (selectedHics.length >= rule.min && (rule.max == null || selectedHics.length <= rule.max));
  const figureReady = !state.datasetScan || workflowReadiness(item).ready;
  const tracksUnsupported = selectedTracks.length > 0 && !trackFigureSupported;
  // A track selection must never prevent the selected Hi-C figure from being
  // built.  Unsupported tracks are reported explicitly and remain available
  // for a later switch to an integrated CFIZZ workflow.
  const canBuildFigure = selectedHics.length > 0 && validHicCount && figureReady;
  $('buildDataset').disabled = !canBuildFigure;
  $('addTracksDataset').hidden = !state.sessionId || !selectedTracks.length || !trackFigureSupported;
  $('addTracksDataset').disabled = !newTracks.length || !trackFigureSupported;
  $('addTracksDataset').textContent = newTracks.length
    ? (state.language === 'en' ? `Add ${newTracks.length} new track${newTracks.length === 1 ? '' : 's'} to current figure` : `添加 ${newTracks.length} 条新轨道到当前图`)
    : t('trackAlreadyPresent');
  const selectionMismatch = Boolean(rule && !validHicCount);
  $('buildDataset').textContent = canBuildFigure
      ? (state.language === 'en'
        ? `Build “${item?.label || 'figure'}”${tracksUnsupported ? ` ${t('tracksExcluded')}` : ''}`
        : `生成「${item?.label || '所选图形'}」${tracksUnsupported ? t('tracksExcluded') : ''}`)
      : selectionMismatch
        ? (state.language === 'en' ? `Adjust Hi-C selection (${cardinalityText(rule)})` : `调整 Hi-C 选择（${cardinalityText(rule)}）`)
        : t('missingData');
  if (tracksUnsupported) {
    $('workflowActionHint').textContent = state.language === 'en'
      ? `${item?.label || 'This figure'} uses a CFIZZ API without an integrated track panel. The Hi-C figure can still be built; switch to an integrated CFIZZ workflow to place the checked tracks below it.`
      : `${item?.label || '当前图形'} 使用的 CFIZZ 官方接口没有整合轨道面板。仍可生成 Hi-C 图；如需把已选轨道放在图下方，请切换到支持整合轨道的 CFIZZ 工作流。`;
    $('workflowActionHint').hidden = false;
  }
  updateWorkflowReadiness();
}
function renderDatasetFiles(scan) {
  const target = $('datasetFiles');
  target.replaceChildren();
  const files = scan.files.filter(item => item.usable);
  if (!files.length) {
    target.textContent = state.language === 'en' ? 'No supported files were found.' : '没有发现可选择的支持文件。';
    return;
  }
  const picker = document.createElement('div');
  picker.className = 'dataset-file-picker';
  const toolbar = document.createElement('div');
  toolbar.className = 'dataset-file-toolbar';
  const selectAll = document.createElement('button');
  selectAll.type = 'button'; selectAll.textContent = t('selectAll');
  const clearAll = document.createElement('button');
  clearAll.type = 'button'; clearAll.textContent = t('clearAll');
  const setAll = checked => {
    picker.querySelectorAll('input[data-dataset-path]:not(:disabled)').forEach(input => { input.checked = checked; });
    enforceDatasetSelectionForFigure(null, false);
  };
  selectAll.addEventListener('click', () => setAll(true));
  clearAll.addEventListener('click', () => setAll(false));
  toolbar.append(selectAll, clearAll);
  picker.appendChild(toolbar);
  const heading = document.createElement('div');
  heading.className = 'dataset-selection-count';
  picker.appendChild(heading);
  for (const file of files) {
    const row = document.createElement('div');
    row.className = `dataset-file${file.role === 'unknown' ? ' unknown' : ''}`;
    row.title = `${file.path}\n${(file.role_evidence || []).join('；')}`;
    const label = document.createElement('label'); label.className = 'dataset-file-select';
    const input = document.createElement('input');
    input.type = 'checkbox'; input.checked = false; input.dataset.datasetPath = file.path;
    const compatibleRoles = compatibleRolesForFile(file);
    const sourceCompatible = fileSupportsRole(file, file.role);
    input.disabled = file.role === 'unknown' || !sourceCompatible || !compatibleRoles.length;
    if (input.disabled) input.checked = false;
    input.addEventListener('change', () => enforceDatasetSelectionForFigure(input, false));
    const name = document.createElement('span'); name.className = 'dataset-file-name'; name.textContent = file.name;
    label.append(input, name); row.appendChild(label);
    const resolutionMeta = file.role === 'hic' && Array.isArray(file.resolutions) && file.resolutions.length
      ? ` · ${file.resolutions.map(value => formatBp(Number(value))).join('/')}`
      : '';
    const controls = document.createElement('div'); controls.className = 'dataset-file-controls';
    const allowedRoles = compatibleRoles;
    const roleSelect = document.createElement('select'); roleSelect.className = 'dataset-role-select'; roleSelect.title = t('detectedRole');
    if (!allowedRoles.length) {
      const option = document.createElement('option'); option.value = 'unknown'; option.textContent = state.language === 'en' ? 'Unrecognized' : '未识别'; roleSelect.appendChild(option); roleSelect.disabled = true;
    } else {
      for (const role of allowedRoles) {
        const option = document.createElement('option'); option.value = role; option.textContent = datasetRoleLabel(role); roleSelect.appendChild(option);
      }
      roleSelect.value = allowedRoles.includes(file.role) ? file.role : allowedRoles[0];
      roleSelect.disabled = allowedRoles.length < 2;
    }
    const sampleInput = document.createElement('input'); sampleInput.type = 'text'; sampleInput.className = 'dataset-sample-input'; sampleInput.maxLength = 120; sampleInput.placeholder = t('sampleName'); sampleInput.value = file.sample || file.auto_sample || ''; sampleInput.disabled = file.role === 'unknown';
    controls.append(roleSelect, sampleInput); row.appendChild(controls);
    const confidence = Math.round(Number(file.role_confidence ?? 0) * 100);
    const meta = document.createElement('small'); meta.className = 'dataset-file-meta';
    const updateMeta = () => {
      const incompatibleHint = !sourceCompatible
        ? (state.language === 'en' ? ' · incompatible source format' : ' · 数据格式与该角色不兼容')
        : '';
      meta.textContent = `${datasetRoleLabel(file.role)}${file.sample ? ` · ${file.sample}` : ''}${resolutionMeta}${file.role_evidence?.length ? ` · ${confidence}%` : ''}${incompatibleHint}`;
    };
    updateMeta(); row.appendChild(meta);
    roleSelect.addEventListener('change', () => {
      file.role = roleSelect.value;
      state.fileOverrides[file.path] = {...(state.fileOverrides[file.path] || {}), role:file.role};
      updateMeta();
      enforceDatasetSelectionForFigure(input, true);
      updateWorkflowReadiness();
    });
    sampleInput.addEventListener('change', () => {
      file.sample = sampleInput.value.trim() || file.auto_sample || null;
      state.fileOverrides[file.path] = {...(state.fileOverrides[file.path] || {}), sample:file.sample || ''};
      updateMeta();
    });
    picker.appendChild(row);
  }
  target.appendChild(picker);
  enforceDatasetSelectionForFigure(null, true);
}
async function scanDatasetPath(path) {
  const gene = datasetQuery();
  if (!state.datasetScan || datasetPathKey(state.datasetScan.path) !== datasetPathKey(path)) state.fileOverrides = {};
  const results = $('datasetResults');
  results.hidden = false;
  results.classList.remove('error');
  $('authorizeDataset').hidden = true;
  $('datasetSummary').textContent = '正在扫描目录并识别数据类型……';
  $('datasetFiles').replaceChildren();
  $('datasetResolution').hidden = true;
  $('buildDataset').hidden = true;
  $('addTracksDataset').hidden = true;
  $('datasetResults').querySelectorAll('.dataset-notices').forEach(node => node.remove());
  $('buildDataset').disabled = true;
  try {
    const scan = await api('/api/datasets/scan', {
      method:'POST', body:JSON.stringify({path, gene, reference_build:$('referenceBuild').value, file_overrides:fileOverridesPayload()})
    });
    state.datasetScan = {path, gene, scan};
    $('workflowActionHint').textContent = state.language === 'en'
      ? 'Files detected. Choose a figure type above; workflows stay disabled until their required inputs are present. Upload supporting files below when needed.'
      : '文件已识别。请在上方选择图类型；下方工作流会在依赖齐全后启用，缺少文件时可从下方上传补充。';
    $('workflowActionHint').hidden = false;
    $('datasetSummary').innerHTML = `<strong>${scan.summary}</strong>`;
    renderDatasetFiles(scan);
    $('buildDataset').hidden = false;
    $('uploadDataset').hidden = false;
    const notices = [...(scan.missing || []), ...(scan.warnings || [])];
    if (notices.length) {
      const note = document.createElement('small'); note.className = 'dataset-notices'; note.textContent = notices.join('；'); $('datasetResults').appendChild(note);
    }
    updateDatasetSelectionSummary();
    $('applyFigureType').disabled = !state.sessionId;
    $('applyFigureType').textContent = state.sessionId ? t('applyCurrent') : t('selectFigure');
    setStatus('数据目录已识别', 'success');
  } catch(error) {
    results.classList.add('error');
    $('datasetSummary').textContent = error.message;
    $('datasetFiles').textContent = '请确认目录已授权，并包含至少一个 .cool 或 .mcool 文件。';
    $('buildDataset').hidden = true;
    $('addTracksDataset').hidden = true;
    $('uploadDataset').hidden = true;
    $('authorizeDataset').hidden = !error.message.includes('已授权目录');
  }
}
$('authorizeDataset').addEventListener('click', async () => {
  const path = $('dataPath').value.trim();
  try {
    await api('/api/datasets/authorize', {method:'POST', body:JSON.stringify({path})});
    $('dataForm').requestSubmit();
  } catch(error) { $('datasetSummary').textContent = error.message; }
});
$('uploadDataset').addEventListener('click', () => $('datasetUploadInput').click());
$('datasetUploadInput').addEventListener('change', async event => {
  const input = event.currentTarget;
  const files = [...(input.files || [])];
  const scanned = state.datasetScan;
  if (!files.length || !scanned) return;
  const button = $('uploadDataset');
  try {
    button.disabled = true;
    $('workflowActionHint').textContent = t('uploading');
    $('workflowActionHint').hidden = false;
    setStatus(t('uploading'), 'checking');
    for (const file of files) {
      const response = await fetch('/api/datasets/upload', {
        method:'POST',
        headers:{'x-filename':encodeURIComponent(file.name), 'x-target-path':encodeURIComponent(scanned.path)},
        body:file,
      });
      let payload = null;
      try { payload = await response.json(); } catch (_) { /* handled below */ }
      if (!response.ok) {
        const error = new Error(payload?.detail || `Upload failed (${response.status})`);
        error.status = response.status;
        throw error;
      }
    }
    $('workflowActionHint').textContent = t('uploadDone');
    await scanDatasetPath(scanned.path);
    setStatus(state.language === 'en' ? 'Data ready' : '数据已更新', 'success');
  } catch (error) {
    $('workflowActionHint').textContent = error.message;
    $('workflowActionHint').hidden = false;
    setStatus(state.language === 'en' ? 'Upload failed' : '补充文件上传失败', 'failed');
  } finally {
    input.value = '';
    button.disabled = false;
  }
});
$('buildDataset').addEventListener('click', async () => {
  const selected = state.datasetScan;
  if (!selected) return;
  try {
    $('buildDataset').disabled = true;
    const chosenPaths = selectedDatasetPaths();
    const selectedHicNames = selected.scan.files.filter(item => item.usable && item.role === 'hic' && chosenPaths.includes(item.path)).map(item => item.name);
    const figureItem = currentFigureTypeItem();
    const rule = hicCardinality(figureItem);
    const figureReadiness = workflowReadiness(figureItem);
    if (!figureReadiness.ready) {
      throw new Error(state.language === 'en' ? `Missing data: ${figureReadiness.missing}.` : `缺少数据：${figureReadiness.missing}。请上传补充文件后重新扫描。`);
    }
    if (rule && (selectedHicNames.length < rule.min || (rule.max != null && selectedHicNames.length > rule.max))) {
      throw new Error(state.language === 'en' ? `“${figureItem.label}” requires ${cardinalityText(rule)}; ${selectedHicNames.length} selected.` : `“${figureItem.label}”需要选择${cardinalityText(rule)}；当前选择了 ${selectedHicNames.length} 个。`);
    }
    setStatus(state.language === 'en' ? 'Building selected figure' : '正在生成所选图形', 'running', state.language === 'en' ? 'Using the checked files and the selected CFIZZ figure type…' : '正在按勾选文件和所选 CFIZZ 图类型组织数据……');
    const sessionId = `dataset_${Date.now()}`;
    const payload = await api('/api/sessions/from-dataset', {
      method:'POST', body:JSON.stringify({session_id:sessionId, path:selected.path, gene:selected.gene, reference_build:$('referenceBuild').value, selected_paths:chosenPaths, figure_type:$('figureTypeSelect').value || 'hic_triangle', window:500000, resolution:selectedResolutionValue(), file_overrides:fileOverridesPayload(), render:true})
    });
    state.sessionId = payload.session_id;
    updateSession(payload);
    $('chatInput').disabled = false; $('chatForm').querySelector('button').disabled = false;
    const spec = payload.spec;
    const region = `${spec.viewport.chrom}:${formatBp(spec.viewport.start)}–${formatBp(spec.viewport.end)}`;
    const message = state.language === 'en'
      ? `Created: ${figureItem?.label || spec.title}\nHi-C: ${selectedHicNames.join(', ')}\nRegion: ${region}; resolution: ${formatBp(spec.analysis.resolution)} bp.${spec.metadata?.track_renderer_note ? `\n${spec.metadata.track_renderer_note}` : ''}`
      : `已生成：${figureItem?.label || spec.title}\n使用 Hi-C：${selectedHicNames.join('、')}\n区域：${region}；分辨率：${formatBp(spec.analysis.resolution)} bp。${spec.metadata?.track_renderer_note ? `\n${spec.metadata.track_renderer_note}` : ''}`;
    addMessage('assistant', message, 'data:directory');
    watchJob(payload.job);
  } catch(error) {
    setStatus(state.language === 'en' ? 'Figure creation failed' : '图形生成失败', 'failed');
    addMessage('assistant', error.message, 'data:directory');
  }
  finally { updateDatasetSelectionSummary(); }
});
$('addTracksDataset').addEventListener('click', async () => {
  const selected = state.datasetScan;
  if (!selected || !state.sessionId) return;
  const newTracks = newSelectedTrackFiles();
  if (!newTracks.length) {
    addMessage('assistant', t('trackAlreadyPresent'), 'data:directory');
    return;
  }
  try {
    $('addTracksDataset').disabled = true;
    setStatus(t('addingTracks'), 'running', state.language === 'en' ? 'Checking chromosome compatibility and passing the tracks to the official CFIZZ renderer…' : '正在检查染色体兼容性，并交给 CFIZZ 官方接口组织轨道……');
    const payload = await api(`/api/sessions/${state.sessionId}/tracks/from-dataset`, {
      method:'POST', body:JSON.stringify({path:selected.path, gene:selected.gene, reference_build:$('referenceBuild').value, selected_paths:selectedDatasetPaths(), file_overrides:fileOverridesPayload()})
    });
    updateSession(payload);
    const added = payload.added || {};
    const total = (added.bigwig || 0) + (added.bed || 0);
    const message = payload.already_present || !total
      ? t('trackAlreadyPresent')
      : (state.language === 'en'
        ? `Added ${added.bigwig || 0} BigWig and ${added.bed || 0} BED track${total === 1 ? '' : 's'} below the current figure.`
        : `已将 ${added.bigwig || 0} 条 BigWig 和 ${added.bed || 0} 条 BED 轨道添加到当前图下方。`);
    addMessage('assistant', `${message}${(payload.skipped_incompatible || []).length ? (state.language === 'en' ? ` ${payload.skipped_incompatible.length} incompatible file(s) were skipped.` : `另有 ${payload.skipped_incompatible.length} 个文件与当前染色体不兼容，已跳过。`) : ''}`, 'data:directory');
    if (payload.job) watchJob(payload.job);
    else setStatus(t('completed'), 'success');
  } catch(error) {
    setStatus(state.language === 'en' ? 'Track addition failed' : '轨道添加失败', 'failed');
    addMessage('assistant', error.message, 'data:directory');
  } finally {
    updateDatasetSelectionSummary();
  }
});
$('chatForm').addEventListener('submit', async (event) => {
  event.preventDefault(); const message=$('chatInput').value.trim(); if(!message || !state.sessionId) return;
  $('chatInput').value=''; addMessage('user',message);
  try {
    let payload=await api(`/api/sessions/${state.sessionId}/chat`, {method:'POST',body:JSON.stringify({message,provider:state.provider})});
    if(payload.intent.requires_confirmation && !payload.job) {
      const accepted=window.confirm(`${payload.intent.reply}\n\n这是科学参数修改，是否继续？`);
      if(accepted) payload=await api(`/api/sessions/${state.sessionId}/chat`, {method:'POST',body:JSON.stringify({message,provider:state.provider,confirm_scientific_change:true})});
      else { addMessage('assistant','已取消修改。'); return; }
    }
    addMessage('assistant',payload.intent.reply,payload.intent.planner); updateSession(payload); watchJob(payload.job);
  } catch(error) { addMessage('assistant',error.message); }
});
$('chatInput').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (!$('chatInput').value.trim()) return;
  $('chatForm').requestSubmit();
});
async function historyAction(action) {
  try { const payload=await api(`/api/sessions/${state.sessionId}/${action}`,{method:'POST'}); updateSession(payload); addMessage('assistant',action==='undo'?'已撤销到上一版。':'已恢复下一版。'); watchJob(payload.job); } catch(error){ addMessage('assistant',error.message); }
}
$('undoButton').addEventListener('click',()=>historyAction('undo'));
$('redoButton').addEventListener('click',()=>historyAction('redo'));
$('languageSelect').addEventListener('change', event => applyLanguage(event.target.value));
$('plannerSelect').addEventListener('change',(event)=>{ state.provider=event.target.value; updatePlannerDisplay(); });
$('apiProvider').addEventListener('change', updateApiConfigDisplay);
$('toggleApiKey').addEventListener('click', () => {
  const key = $('apiKey');
  const showing = key.type === 'text';
  key.type = showing ? 'password' : 'text';
  $('toggleApiKey').textContent = showing ? t('show') : t('hide');
});
$('apiConfigForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const provider = $('apiProvider').value;
  const apiKey = $('apiKey').value.trim();
  const model = $('apiModel').value.trim();
  if (!apiKey) { setApiConfigStatus(state.language === 'en' ? 'Enter an API Key.' : '请输入 API Key。', 'error'); return; }
  $('saveApiConfig').disabled = true;
  setApiConfigStatus(state.language === 'en' ? 'Configuring the server connection…' : '正在建立服务端连接配置……');
  try {
    const catalog = await api('/api/planners/configure', {
      method:'POST', body:JSON.stringify({provider, api_key:apiKey, model:model || null})
    });
    $('apiKey').value = '';
    $('apiKey').type = 'password';
    $('toggleApiKey').textContent = t('show');
    applyPlannerCatalog(catalog, provider);
    setApiConfigStatus(state.language === 'en' ? `${provider === 'deepseek' ? 'DeepSeek' : 'OpenAI'} enabled; subsequent messages will use this API.` : `${provider === 'deepseek' ? 'DeepSeek' : 'OpenAI'} 已启用；后续对话将使用该 API。`, 'success');
  } catch(error) { setApiConfigStatus(error.message, 'error'); }
  finally { $('saveApiConfig').disabled = false; }
});
$('removeApiConfig').addEventListener('click', async () => {
  const provider = $('apiProvider').value;
  try {
    const catalog = await api(`/api/planners/${provider}`, {method:'DELETE'});
    applyPlannerCatalog(catalog, 'local');
    setApiConfigStatus(state.language === 'en' ? 'API configuration cleared from server memory.' : 'API 配置已从服务端内存清除。', 'success');
  } catch(error) { setApiConfigStatus(error.message, 'error'); }
});
$('figureTypeSelect').addEventListener('change', () => { updateFigureTypeHint(); enforceDatasetSelectionForFigure(null, true); });
$('datasetResolutionSelect').addEventListener('change', updateDatasetSelectionSummary);
$('datasetGene').addEventListener('input', () => { state.regionEdited = true; });
$('applyFigureType').addEventListener('click', async () => {
  const figureType = $('figureTypeSelect').value;
  const item = state.figureTypes.find(value => value.id === figureType);
  if (!state.sessionId && state.datasetScan) {
    try {
      setStatus(state.language === 'en' ? 'Building selected figure' : '正在生成所选图形', 'running', t('preparing'));
      const sessionId = `dataset_${Date.now()}`;
      const payload = await api('/api/sessions/from-dataset', {
        method:'POST', body:JSON.stringify({session_id:sessionId, path:state.datasetScan.path, gene:state.datasetScan.gene, reference_build:$('referenceBuild').value, selected_paths:selectedDatasetPaths(), figure_type:figureType, window:500000, resolution:selectedResolutionValue(), render:true})
      });
      state.sessionId = payload.session_id;
      updateSession(payload);
      $('chatInput').disabled = false;
      $('chatForm').querySelector('button').disabled = false;
      const chosen = new Set(selectedDatasetPaths());
      const hicNames = state.datasetScan.scan.files.filter(file => file.usable && file.role === 'hic' && chosen.has(file.path)).map(file => file.name);
      addMessage('assistant', state.language === 'en'
        ? `Created: ${item?.label || figureType}\nHi-C: ${hicNames.join(', ')}`
        : `已生成：${item?.label || figureType}\n使用 Hi-C：${hicNames.join('、')}`, 'data:directory');
      watchJob(payload.job);
    } catch (error) {
      setStatus(state.language === 'en' ? 'Figure creation failed' : '图形生成失败', 'failed');
      addMessage('assistant', error.message, 'data:directory');
    }
    return;
  }
  if (!state.sessionId) return;
  try {
    const payload = await api(`/api/sessions/${state.sessionId}/patch`, {
      method:'POST', body:JSON.stringify({patch:{summary:`切换图类型为 ${item.label}`,operations:[
        {op:'update',target_kind:'figure',field:'figure_type',value:figureType},
        {op:'update',target_kind:'figure',field:'workflow_source_ids',value:[]},
        {op:'update',target_kind:'figure',field:'workflow_options',value:{}}
      ]},render:true})
    });
    addMessage('assistant', `已切换为${item.label}，区域、分辨率和数据源保持不变。`);
    updateSession(payload); watchJob(payload.job);
  } catch(error) { addMessage('assistant', error.message); }
});
applyLanguage(state.language);
checkServerCompatibility();
loadPlannerStatus();
loadFigureTypes();
