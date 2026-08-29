const EXPECTED_API_REVISION = 12;
const storedFigureZoom = Number.parseInt(localStorage.getItem('cfizz-figure-zoom') || '', 10);
const state = { sessionId: null, session: null, chatSessionPromise: null, activeJob: null, provider: 'local', planners: [], figureTypes: [], referenceBuilds: [], datasetScan: null, datasetSources: [], activeDatasetKey: null, pendingDatasetPath: null, pendingDatasetOptions: null, datasetSourceRestoring: false, activeWorkflow: null, fileOverrides: {}, renderTimer: null, renderStartedAt: null, language: localStorage.getItem('cfizz-language') || 'zh-CN', regionEdited: false, figureZoom: Number.isFinite(storedFigureZoom) && storedFigureZoom >= 40 && storedFigureZoom <= 200 ? storedFigureZoom : 80 };
const $ = (id) => document.getElementById(id);

function openDialog(id, focusId='') {
  const dialog = $(id);
  if (!dialog || dialog.open) return;
  dialog.showModal();
  document.body.classList.add('dialog-open');
  requestAnimationFrame(() => {
    const target = focusId ? $(focusId) : dialog.querySelector('button, input, select, textarea');
    target?.focus({preventScroll:true});
  });
}
function closeDialog(id) {
  const dialog = $(id);
  if (dialog?.open) dialog.close();
}

function applyFigureZoom() {
  const image = $('figureImage');
  const canvas = $('canvas');
  const range = $('figureZoom');
  const output = $('figureZoomValue');
  const zoom = Math.min(200, Math.max(40, Number(state.figureZoom) || 80));
  range.value = String(zoom);
  output.value = `${zoom}%`;
  output.textContent = `${zoom}%`;
  if (image.hidden || !image.complete || !image.naturalWidth || !image.naturalHeight) return;
  const canvasStyle = window.getComputedStyle(canvas);
  const horizontalPadding = parseFloat(canvasStyle.paddingLeft) + parseFloat(canvasStyle.paddingRight);
  const verticalPadding = parseFloat(canvasStyle.paddingTop) + parseFloat(canvasStyle.paddingBottom);
  const availableWidth = Math.max(1, canvas.clientWidth - horizontalPadding);
  const availableHeight = Math.max(1, canvas.clientHeight - verticalPadding);
  const fitScale = Math.min(availableWidth / image.naturalWidth, availableHeight / image.naturalHeight);
  const displayScale = fitScale * (zoom / 100);
  image.style.width = `${Math.max(1, Math.round(image.naturalWidth * displayScale))}px`;
  image.style.height = `${Math.max(1, Math.round(image.naturalHeight * displayScale))}px`;
  canvas.scrollLeft = Math.max(0, (canvas.scrollWidth - canvas.clientWidth) / 2);
  canvas.scrollTop = Math.max(0, (canvas.scrollHeight - canvas.clientHeight) / 2);
}
function setFigureZoom(value) {
  state.figureZoom = Math.min(200, Math.max(40, Number(value) || 80));
  localStorage.setItem('cfizz-figure-zoom', String(state.figureZoom));
  applyFigureZoom();
}
let figureZoomFrame = null;
function scheduleFigureZoom() {
  if (figureZoomFrame) cancelAnimationFrame(figureZoomFrame);
  figureZoomFrame = requestAnimationFrame(() => {
    figureZoomFrame = null;
    applyFigureZoom();
  });
}

function datasetRegionMode() {
  return $('datasetRegionMode')?.value === 'manual' ? 'manual' : 'auto';
}
function datasetQuery() {
  return datasetRegionMode() === 'manual' ? ($('datasetGene').value.trim() || null) : null;
}
function parseRegionInput(value) {
  const text = String(value || '').trim().replaceAll('，', ',');
  const match = text.match(/^(chr[0-9xywm]+|[0-9xywm]+)\s*(?::\s*([\d,.]+)\s*([kmg]?)(?:bp|b)?\s*[-–—到]\s*([\d,.]+)\s*([kmg]?)(?:bp|b)?)?$/i);
  if (!match) return null;
  const scale = (raw, suffix) => Number.parseFloat(String(raw).replaceAll(',', '')) * ({k:1e3,m:1e6,g:1e9}[String(suffix || '').toLowerCase()] || 1);
  if (!match[2]) return {chrom:match[1], start:0, end:2_000_000};
  const start = scale(match[2], match[3]);
  const end = scale(match[4], match[5]);
  return Number.isFinite(start) && Number.isFinite(end) && end > start ? {chrom:match[1], start, end} : null;
}
function datasetRegionError() {
  if (datasetRegionMode() !== 'manual') return '';
  const value = $('datasetGene').value.trim();
  if (!value) return t('regionEmpty');
  const chromosomeLike = /^(?:chr)?(?:[0-9]+|[xywm])(?::|$)/i.test(value);
  return chromosomeLike && !parseRegionInput(value) ? t('regionInvalid') : '';
}
function setDatasetRegionStatus(text, tone='') {
  const status = $('datasetRegionStatus');
  if (!status) return;
  status.textContent = text;
  status.className = `dataset-region-status${tone ? ` ${tone}` : ''}`;
}
function formatViewport(viewport) {
  return `${viewport.chrom}:${formatBp(Number(viewport.start))}–${formatBp(Number(viewport.end))}`;
}
let datasetRegionPreviewTimer = null;
let datasetRegionPreviewRequest = 0;
function scheduleDatasetRegionPreview(delay=350) {
  clearTimeout(datasetRegionPreviewTimer);
  const requestId = ++datasetRegionPreviewRequest;
  if (!state.datasetScan) return;
  const error = datasetRegionError();
  if (error) { setDatasetRegionStatus(error, 'error'); return; }
  setDatasetRegionStatus(t('regionPreviewing'), 'checking');
  datasetRegionPreviewTimer = setTimeout(() => { void previewDatasetRegion(requestId); }, delay);
}
async function previewDatasetRegion(requestId) {
  if (requestId !== datasetRegionPreviewRequest || !state.datasetScan) return;
  const item = currentFigureTypeItem();
  const readiness = workflowReadiness(item);
  if (!item || !readiness.ready) {
    setDatasetRegionStatus(t('regionNeedsInputs', readiness.missing || t('requiredInput')));
    return;
  }
  try {
    const response = await api('/api/sessions/from-dataset', {
      method:'POST',
      body:JSON.stringify({
        session_id:`viewport_preview_${Date.now()}`,
        preview_only:true,
        path:state.datasetScan.path,
        source_paths:state.datasetScan.sourcePaths || [state.datasetScan.path],
        gene:datasetQuery(),
        reference_build:selectedReferenceBuild(),
        selected_paths:selectedDatasetPaths(),
        figure_type:$('figureTypeSelect').value || 'hic_triangle',
        window:500000,
        resolution:selectedResolutionValue(),
        file_overrides:fileOverridesPayload(),
        render:false,
      }),
    });
    if (requestId !== datasetRegionPreviewRequest) return;
    const viewport = response.spec?.viewport;
    if (!viewport) throw new Error(t('regionPreviewUnavailable'));
    const label = formatViewport(viewport);
    if (datasetRegionMode() === 'manual') {
      setDatasetRegionStatus(t('regionManualExpected', $('datasetGene').value.trim(), label), 'ready');
    } else {
      const reason = state.language === 'zh-CN' ? response.spec?.metadata?.viewport_reason : '';
      setDatasetRegionStatus(t('regionAutoExpected', label, reason), 'ready');
    }
  } catch (error) {
    if (requestId !== datasetRegionPreviewRequest) return;
    setDatasetRegionStatus(error.message || t('regionPreviewUnavailable'), datasetRegionMode() === 'manual' ? 'error' : '');
  }
}
function updateDatasetRegionControl() {
  const input = $('datasetGene');
  if (!input) return;
  const manual = datasetRegionMode() === 'manual';
  input.disabled = !manual;
  input.placeholder = t(manual ? 'regionManualPlaceholder' : 'regionAutoPlaceholder');
  state.regionEdited = manual;
  const error = datasetRegionError();
  if (error) { setDatasetRegionStatus(error, 'error'); return; }
  if (!state.datasetScan) {
    setDatasetRegionStatus(t(manual ? 'regionManualWaiting' : 'regionAutoWaiting'));
    return;
  }
  scheduleDatasetRegionPreview(100);
}
function requireValidDatasetRegion() {
  const error = datasetRegionError();
  if (!error) return true;
  setDatasetRegionStatus(error, 'error');
  $('datasetGene').scrollIntoView({behavior:'smooth', block:'center'});
  $('datasetGene').focus({preventScroll:true});
  return false;
}

const messages = {
  'zh-CN': {
    subtitle:'对话式 Hi-C 可视化', loadDemo:'载入 FOXJ1 示例', undo:'撤销', redo:'重做', chat:'对话', dataApi:'数据与 API',
    addDataStep:'添加数据源', regionSettingsStep:'设置绘图范围',
    figureReady:'可生成', figureNeedsData:'需补数据', figureWaitingData:'待导入数据', figureUnavailable:'暂不可用', figureAutoSelect:'将自动匹配已导入文件', figureMissingDetail:(value)=>`缺少：${value}`,
    speciesHuman:'人类', speciesMouse:'小鼠', builtInReference:'内置', userReference:'用户注释', referenceComplete:(release,count)=>`${release}${count ? ` · ${Number(count).toLocaleString('zh-CN')} 个基因` : ''}`, referenceUserAnnotation:(name)=>`已选择 ${name} · 请确认坐标版本一致`, referenceCoordinatesOnly:'坐标绘图可用 · 使用基因名时需导入匹配版本的 GTF/GFF', referenceCatalogUnavailable:'暂时无法读取参考注释目录。',
    dialogueApi:'理解模式', connectAi:'API 配置', memoryOnly:'临时保存', apiKeyPlaceholder:'输入 API Key',
    loadingConfig:'正在读取配置……', keySecretHint:'密钥仅保存在内存，服务重启后清除。', backToWorkflows:'返回工作流',
    show:'显示', hide:'隐藏', connectUse:'连接并使用', disconnect:'断开并清除', startHic:'单个 Hi-C 文件', dataSource:'数据源',
    load:'导入数据源', importedSources:'已导入数据源', sessionOnlySources:'勾选本次要合并使用的数据来源。', activeSource:'当前', switchSource:'切换', includedSource:'已加入', excludedSource:'未加入', refreshSource:'刷新', removeSource:'移除', sourceCount:(n)=>`${n} 个`, sourceSelectionCount:(selected,total)=>`${selected}/${total} 已加入`, sourceMeta:(hic, files)=>`${hic} 个 Hi-C · ${files} 个文件`, sourceSwitched:(name)=>`已切换到 ${name}`, sourceIncluded:(name,n)=>`已加入 ${name}，当前合并 ${n} 个数据源`, sourceExcluded:(name)=>`已暂停使用 ${name}`, sourceImported:(name)=>`已导入 ${name}`, sourceRefreshed:(name)=>`已刷新 ${name}`, sourceRemoved:(name)=>`已从当前页面移除 ${name}（磁盘文件未删除）`, noSourceSelected:'请在“导入 / 管理数据”中至少加入一个数据源。', combinedSourceSummary:(sources,hic,tracks,files)=>`已合并 ${sources} 个数据源 · ${hic} 个 Hi-C · ${tracks} 条轨道/注释 · 共 ${files} 个文件`, workspaceStatus:'当前工作区', dataWorkspaceEmpty:'尚未导入数据', dataWorkspaceEmptyHint:'导入 .cool/.mcool 文件或实验目录后开始绘图。', dataWorkspaceReady:(included,total)=>included === total ? `${included} 个数据源已就绪` : `${included}/${total} 个数据源已加入`, dataWorkspaceReadyHint:(hic,tracks,files)=>`${hic} 个 Hi-C · ${tracks} 条轨道/注释 · ${files} 个文件`, manageData:'导入 / 管理数据', dialogueSettings:'对话设置', dialogueSettingsTitle:'对话理解设置', dialogueSettingsHint:'选择理解模式，或连接 OpenAI / DeepSeek。', importDataTitle:'导入与管理数据', importDataHint:'添加来源、设置绘图区域，并管理本次使用的数据。', close:'关闭', done:'完成', figureType:'图类型', figureGenerate:'图形与生成', figureGenerateHint:'先选择要生成的图；下方文件用于微调输入。', figureGenerateCompactHint:'选择图形，确认输入后直接生成。', selectedFigure:'当前图形', selectFigureType:'选择图类型', figureSelection:'选择图形', figureSelectionHint:'先选择基础图形；复杂分析可从下方工作流进入。', directFigures:'基础图形', figureRequires:(value)=>`需要 ${value}`, inputDetails:'绘图输入与分辨率', inputDetailsHint:'需要时展开调整文件和共同分辨率。', advanced:'高级', applyCurrent:'应用到当前图', selectFigure:'选择图类型后生成', vectorPreview:'SVG 高清预览', rasterPreview:'PNG 预览', scanDataset:'实验目录', scanDirectory:'扫描目录', resolutionChoice:'共同分辨率', resolutionAuto:'自动选择',
    targetGene:'基因或范围（如 FOXJ1、chr1:1-2Mb）', drawingRegion:'绘图区域', regionAuto:'自动推荐', regionManual:'指定区域', referenceGenome:'参考注释', regionAutoPlaceholder:'由系统根据图类型与所选文件推荐', regionManualPlaceholder:'输入基因名或范围，如 MYC、chr1:25-45Mb', regionAutoWaiting:'导入数据并选择图类型后，将在这里显示预计范围。', regionManualWaiting:'导入数据后将验证并显示实际绘图范围。', regionEmpty:'请输入基因名或染色体范围。', regionInvalid:'区域格式不正确，请输入如 chr1:25-45Mb。', regionPreviewing:'正在计算预计绘图范围……', regionNeedsInputs:(missing)=>`补齐当前图所需输入后，将显示预计范围（缺少：${missing}）。`, requiredInput:'所需输入', regionManualExpected:(input,region)=>`已识别“${input}”，生成时将使用 ${region}。`, regionAutoExpected:(region,reason)=>`预计使用 ${region}${reason ? ` · ${reason}` : ' · 根据当前图类型与所选文件自动推荐。'}`, regionPreviewUnavailable:'暂时无法计算预计范围。', specifyRegion:'请指定绘图区域', humanHg38:'内置 · hg38 / GRCh38', authorizationTitle:'需要授权新目录', authorizationMessage:(path)=>`新数据源“${path}”尚未授权。已导入的数据会继续保留，授权后将自动扫描这个目录。`, authorizationFailed:(message)=>`授权未完成：${message}`, authorizationDismiss:'暂不处理', authorizeRetry:'授权并重新扫描', authorizing:'正在授权…', buildSelected:'生成图形', buildMultiomics:'生成默认整合图', confirmPairing:'我已确认以上样本对应关系', sampleName:'样本名', detectedRole:'数据角色',
    loadingTypes:'正在读取图类型……', serverAuth:'', datasetHint:'支持 .cool/.mcool 文件或实验目录；重复路径会刷新，新的路径会保留并合并。', dataPathPlaceholder:'/data/sample.mcool 或 /data/case1', datasetPathPlaceholder:'E:\\project\\case1 或 /data/case1', combinedWorkflows:'', combinedWorkflowsHint:'', workflowCatalog:'更多 CFIZZ 工作流', useWorkflow:'选择文件', missingData:'补充数据', workflowNeeds:'需要', selectAll:'全选', clearAll:'清空', selectRecommended:'推荐选择', workflowInputs:'选择本次工作流使用的文件', buildWorkflow:'生成', addTracks:'添加轨道到当前图', addingTracks:'正在添加轨道', trackAlreadyPresent:'所选轨道已经在当前图中，无需重复添加。', tracksNotIncluded:'所选图形的 CFIZZ 接口没有整合轨道面板；本次只生成 Hi-C 图，已选轨道仍保留在会话中。', uploadSupplement:'上传补充文件', uploading:'正在上传补充文件……', uploadDone:'补充完成，正在重新扫描……', loadFigureFirst:'请先载入 Hi-C 文件或扫描实验目录。', workflowQueued:'已提交工作流请求；系统会复用当前数据，缺少输入时会提示。', workflowBuilding:'正在根据所选数据创建图形……', workflowCreated:'已根据所选数据创建图形，之后可以继续用对话修改。',
    welcomeMessage:'欢迎使用 CFIZZ Agent。请先导入数据或载入示例开始绘图；生成图形后，可在此调整区域、轨道与样式。',
    chatPlaceholder:'描述要生成的图，或说明需要调整的区域、轨道和样式……', sendDraw:'发送', enterHint:'Enter 发送 · Shift+Enter 换行', currentFigure:'CURRENT FIGURE', noFigure:'尚未载入图形',
    history:'历史版本', startConversation:'尚未生成图形', emptyHint:'导入数据或载入示例后，当前图和历史版本将在这里显示。', previewSize:'预览大小', fitWindow:'适应窗口',
    versionHistory:'版本历史', historyHint:'选择任一版本恢复并重新绘图', region:'区域', resolution:'分辨率', version:'版本', download:'下载',
    checking:'检测中', localRules:'本地规则', unknownStatus:'状态未知', waiting:'等待开始', rendering:'正在生成图形', queued:'等待绘图资源', drawing:'正在渲染图形',
    completed:'绘图完成', failed:'绘图失败', elapsed:(n)=>`已用时 ${n} 秒`, preparing:'正在准备数据与绘图参数……', current:'当前', figureEdit:'图形修改', extraInput:'（需额外输入）', tracksExcluded:'（不含已选轨道）'
  },
  en: {
    subtitle:'Conversational Hi-C visualization', loadDemo:'Load FOXJ1 demo', undo:'Undo', redo:'Redo', chat:'Chat', dataApi:'Data & API',
    addDataStep:'Add a data source', regionSettingsStep:'Set the figure region',
    figureReady:'Ready', figureNeedsData:'Needs data', figureWaitingData:'Import data first', figureUnavailable:'Unavailable', figureAutoSelect:'Matching imported files will be selected', figureMissingDetail:(value)=>`Missing: ${value}`,
    speciesHuman:'Human', speciesMouse:'Mouse', builtInReference:'Built in', userReference:'User annotation', referenceComplete:(release,count)=>`${release}${count ? ` · ${Number(count).toLocaleString('en')} genes` : ''}`, referenceUserAnnotation:(name)=>`Selected ${name} · confirm that its assembly matches`, referenceCoordinatesOnly:'Coordinate plotting is available · gene-name lookup requires a matching GTF/GFF', referenceCatalogUnavailable:'Could not load the reference annotation catalog.',
    dialogueApi:'Interpretation mode', connectAi:'API settings', memoryOnly:'Temporary', apiKeyPlaceholder:'Enter API Key', show:'Show', hide:'Hide',
    loadingConfig:'Loading configuration…', keySecretHint:'The key stays in memory and is cleared on restart.', backToWorkflows:'Back to workflows',
    connectUse:'Connect and use', disconnect:'Disconnect and clear', startHic:'Single Hi-C file', dataSource:'Data source', load:'Import source', importedSources:'Imported data sources', sessionOnlySources:'Select the sources to combine for this build.', activeSource:'Current', switchSource:'Switch', includedSource:'Included', excludedSource:'Not included', refreshSource:'Refresh', removeSource:'Remove', sourceCount:(n)=>`${n} source${n === 1 ? '' : 's'}`, sourceSelectionCount:(selected,total)=>`${selected}/${total} included`, sourceMeta:(hic, files)=>`${hic} Hi-C · ${files} file${files === 1 ? '' : 's'}`, sourceSwitched:(name)=>`Switched to ${name}`, sourceIncluded:(name,n)=>`Included ${name}; ${n} sources are now combined`, sourceExcluded:(name)=>`Paused ${name}`, sourceImported:(name)=>`Imported ${name}`, sourceRefreshed:(name)=>`Refreshed ${name}`, sourceRemoved:(name)=>`Removed ${name} from this page (files on disk were not deleted)`, noSourceSelected:'Include at least one source in Import / manage data.', combinedSourceSummary:(sources,hic,tracks,files)=>`Combined ${sources} source${sources === 1 ? '' : 's'} · ${hic} Hi-C · ${tracks} track/annotation file${tracks === 1 ? '' : 's'} · ${files} files total`, workspaceStatus:'Current workspace', dataWorkspaceEmpty:'No data imported', dataWorkspaceEmptyHint:'Import a .cool/.mcool file or experiment directory to begin.', dataWorkspaceReady:(included,total)=>included === total ? `${included} data source${included === 1 ? '' : 's'} ready` : `${included}/${total} sources included`, dataWorkspaceReadyHint:(hic,tracks,files)=>`${hic} Hi-C · ${tracks} track/annotation · ${files} files`, manageData:'Import / manage data', dialogueSettings:'Chat settings', dialogueSettingsTitle:'Chat interpretation', dialogueSettingsHint:'Choose an interpretation mode or connect OpenAI / DeepSeek.', importDataTitle:'Import and manage data', importDataHint:'Add sources, set the figure region, and manage inputs for this workspace.', close:'Close', done:'Done', figureType:'Figure type', figureGenerate:'Figure & build', figureGenerateHint:'Choose the figure first; use the files below to fine-tune its inputs.', figureGenerateCompactHint:'Choose a figure, confirm its inputs, and build.', selectedFigure:'Current figure', selectFigureType:'Choose a figure type', figureSelection:'Choose a figure', figureSelectionHint:'Choose a basic figure or open an advanced CFIZZ workflow below.', directFigures:'Basic figures', figureRequires:(value)=>`Requires ${value}`, inputDetails:'Figure inputs & resolution', inputDetailsHint:'Expand only when you need to adjust files or shared resolution.', advanced:'Advanced', applyCurrent:'Apply to current figure', vectorPreview:'Crisp SVG preview', rasterPreview:'PNG preview',
    scanDataset:'Experiment directory', scanDirectory:'Scan directory', resolutionChoice:'Shared resolution', resolutionAuto:'Automatic', targetGene:'Gene or region (e.g. FOXJ1 or chr1:1-2Mb)', drawingRegion:'Figure region', regionAuto:'Auto recommend', regionManual:'Specify region', referenceGenome:'Reference annotation', regionAutoPlaceholder:'Recommended from the figure type and selected files', regionManualPlaceholder:'Enter a gene or range, e.g. MYC or chr1:25-45Mb', regionAutoWaiting:'Import data and choose a figure to see the expected region.', regionManualWaiting:'The region will be validated after data is imported.', regionEmpty:'Enter a gene or genomic range.', regionInvalid:'Invalid range. Use a format such as chr1:25-45Mb.', regionPreviewing:'Calculating the expected figure region…', regionNeedsInputs:(missing)=>`The expected region will appear when the current figure has its required inputs (missing: ${missing}).`, requiredInput:'required input', regionManualExpected:(input,region)=>`“${input}” resolves to ${region}; this region will be used.`, regionAutoExpected:(region)=>`Expected region: ${region} · Recommended from the current figure and selected files.`, regionPreviewUnavailable:'The expected region is not available yet.', specifyRegion:'Specify figure region', humanHg38:'Built in · hg38 / GRCh38', selectFigure:'Choose a figure type, then build', confirmPairing:'I confirm these sample pairings', sampleName:'Sample name', detectedRole:'Data role',
    authorizationTitle:'New directory permission required', authorizationMessage:(path)=>`“${path}” is not authorized yet. Existing imports remain available; after permission is granted, this directory will be scanned automatically.`, authorizationFailed:(message)=>`Authorization did not complete: ${message}`, authorizationDismiss:'Not now', authorizeRetry:'Authorize and rescan', authorizing:'Authorizing…', buildSelected:'Build figure', buildMultiomics:'Build default integrated figure',
    loadingTypes:'Loading figure types…', serverAuth:'', datasetHint:'Accepts .cool/.mcool files or experiment directories. Existing sources are preserved; importing the same path refreshes it.', dataPathPlaceholder:'/data/sample.mcool or /data/case1', datasetPathPlaceholder:'E:\\project\\case1 or /data/case1', combinedWorkflows:'', combinedWorkflowsHint:'', workflowCatalog:'More CFIZZ workflows', useWorkflow:'Choose files', missingData:'Add data', workflowNeeds:'Needs', selectAll:'Select all', clearAll:'Clear', selectRecommended:'Recommended', workflowInputs:'Choose files for this workflow', buildWorkflow:'Build', addTracks:'Add tracks to current figure', addingTracks:'Adding tracks', trackAlreadyPresent:'The selected tracks are already in the current figure; nothing to add.', tracksNotIncluded:'The selected CFIZZ renderer has no integrated track panel; this build will contain Hi-C only, while the checked tracks remain available in the session.', uploadSupplement:'Upload supporting files', uploading:'Uploading supporting files…', uploadDone:'Uploaded; rescanning…', loadFigureFirst:'Load a Hi-C file or scan a data directory.', workflowQueued:'Workflow request submitted; current inputs will be reused and missing data will be reported.', workflowBuilding:'Building a figure from the selected data…', workflowCreated:'Figure created from the selected data. You can continue editing it in chat.',
    welcomeMessage:'Welcome to CFIZZ Agent. Import data or load the demo to begin; after a figure is created, use this chat to refine its region, tracks, and styling.',
    chatPlaceholder:'Describe a figure to create, or a region, track, or style to adjust…', sendDraw:'Send', enterHint:'Enter to send · Shift+Enter for a new line', currentFigure:'CURRENT FIGURE', noFigure:'No figure loaded',
    history:'History', startConversation:'No figure generated', emptyHint:'Import data or load the demo; the current figure and version history will appear here.', previewSize:'Preview size', fitWindow:'Fit window',
    versionHistory:'Version history', historyHint:'Select a version to restore and render it again', region:'Region', resolution:'Resolution', version:'Version', download:'Download',
    checking:'Checking', localRules:'Local rules', unknownStatus:'Unknown status', waiting:'Waiting', rendering:'Generating figure', queued:'Waiting for renderer', drawing:'Rendering figure',
    completed:'Figure ready', failed:'Render failed', elapsed:(n)=>`${n}s elapsed`, preparing:'Preparing data and plotting parameters…', current:'Current', figureEdit:'Figure edit', extraInput:' (additional input required)', tracksExcluded:'(checked tracks not included)'
  }
};
function t(key, ...args) { const value = messages[state.language]?.[key] ?? messages['zh-CN'][key] ?? key; return typeof value === 'function' ? value(...args) : value; }
function referenceBuildLabel(reference) {
  return `${t('builtInReference')} · ${reference.id} / ${reference.assembly}`;
}
function userReferenceAnnotations() {
  return (state.datasetScan?.scan?.files || []).filter(file => file.usable && file.role === 'gene_annotation');
}
function selectedReferenceOption() {
  return $('referenceBuild')?.selectedOptions?.[0] || null;
}
function selectedReferenceBuild() {
  const option = selectedReferenceOption();
  return option?.dataset.referenceBuild || (String(option?.value || '').replace(/^builtin:/, '') || 'hg38');
}
function selectedReferenceAnnotationPath() {
  return selectedReferenceOption()?.dataset.annotationPath || '';
}
function checkedReferenceAnnotationPath() {
  const selectedPaths = new Set(selectedDatasetPaths().map(datasetPathKey));
  return userReferenceAnnotations().find(file => selectedPaths.has(datasetPathKey(file.path)))?.path || '';
}
function updateReferenceBuildStatus() {
  const build = selectedReferenceBuild();
  const selected = state.referenceBuilds.find(item => item.id === build);
  const status = $('referenceBuildStatus');
  if (!status) return;
  const annotationPath = selectedReferenceAnnotationPath();
  const userAnnotation = userReferenceAnnotations().find(file => datasetPathKey(file.path) === datasetPathKey(annotationPath));
  const control = status.closest('.reference-build-control');
  control?.classList.toggle('using-user-annotation', Boolean(userAnnotation));
  if (userAnnotation) {
    status.textContent = t('referenceUserAnnotation', userAnnotation.name || userAnnotation.path);
    status.className = 'reference-build-status complete';
    return;
  }
  if (!selected) {
    status.textContent = state.referenceBuilds.length ? '' : t('referenceCatalogUnavailable');
    status.className = 'reference-build-status limited';
    return;
  }
  status.textContent = selected.complete
    ? t('referenceComplete', selected.annotation_release || selected.annotation || selected.assembly, selected.gene_count)
    : t('referenceCoordinatesOnly');
  status.className = `reference-build-status ${selected.complete ? 'complete' : 'limited'}`;
}
function renderReferenceBuilds(preferredAnnotationPath='') {
  const select = $('referenceBuild');
  if (!select || !state.referenceBuilds.length) { updateReferenceBuildStatus(); return; }
  const previousBuild = selectedReferenceBuild();
  const previousAnnotation = selectedReferenceAnnotationPath();
  const hasPicker = Boolean($('datasetFiles')?.querySelector('.dataset-file-picker'));
  const checkedAnnotation = checkedReferenceAnnotationPath();
  select.replaceChildren();
  for (const reference of state.referenceBuilds) {
    const option = document.createElement('option');
    option.value = `builtin:${reference.id}`;
    option.dataset.referenceBuild = reference.id;
    option.textContent = referenceBuildLabel(reference);
    option.title = reference.annotation || '';
    select.appendChild(option);
  }
  for (const [index, annotation] of userReferenceAnnotations().entries()) {
    const option = document.createElement('option');
    option.value = `user:${index}`;
    option.dataset.referenceBuild = state.referenceBuilds.some(item => item.id === previousBuild) ? previousBuild : state.referenceBuilds[0].id;
    option.dataset.annotationPath = annotation.path;
    option.textContent = `${t('userReference')} · ${annotation.name}`;
    option.title = annotation.path;
    select.appendChild(option);
  }
  const wantedAnnotation = preferredAnnotationPath || checkedAnnotation || (!hasPicker ? previousAnnotation : '');
  const annotationOption = [...select.options].find(option => (
    wantedAnnotation && datasetPathKey(option.dataset.annotationPath) === datasetPathKey(wantedAnnotation)
  ));
  const buildOption = [...select.options].find(option => !option.dataset.annotationPath && option.dataset.referenceBuild === previousBuild);
  select.value = (annotationOption || buildOption || select.options[0]).value;
  updateReferenceBuildStatus();
}
function syncReferenceChoiceFromFiles() {
  const select = $('referenceBuild');
  if (!select) return;
  const checkedAnnotation = checkedReferenceAnnotationPath();
  const option = [...select.options].find(item => (
    checkedAnnotation && datasetPathKey(item.dataset.annotationPath) === datasetPathKey(checkedAnnotation)
  ));
  if (option) select.value = option.value;
  else if (!checkedAnnotation && selectedReferenceAnnotationPath()) {
    const builtIn = [...select.options].find(item => !item.dataset.annotationPath && item.dataset.referenceBuild === selectedReferenceBuild());
    if (builtIn) select.value = builtIn.value;
  }
  updateReferenceBuildStatus();
}
function applyReferenceAnnotationChoice(annotationPath='') {
  const wanted = datasetPathKey(annotationPath);
  for (const input of document.querySelectorAll('#datasetFiles input[data-dataset-path]')) {
    const file = datasetFileByPath(input.dataset.datasetPath);
    if (file?.role === 'gene_annotation') input.checked = Boolean(wanted && datasetPathKey(file.path) === wanted);
  }
  updateDatasetSelectionSummary();
}
async function loadReferenceBuilds() {
  try {
    const payload = await api('/api/references');
    state.referenceBuilds = Array.isArray(payload.references) ? payload.references : [];
    renderReferenceBuilds();
  } catch (_) {
    updateReferenceBuildStatus();
  }
}
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
  if (state.referenceBuilds.length) renderReferenceBuilds();
  if (state.datasetSources.length) renderCombinedDatasetWorkspace(null, false);
  else {
    renderDatasetSourceShelf();
    if (state.datasetScan) updateDatasetSelectionSummary();
  }
  if (state.datasetScan && (!state.sessionId || isDraftSession())) {
    $('applyFigureType').disabled = true;
    $('applyFigureType').hidden = true;
    $('applyFigureType').textContent = t('selectFigure');
  }
  if (!$('previewQuality').hidden) $('previewQuality').textContent = t($('figureImage').dataset.previewFormat === 'svg' ? 'vectorPreview' : 'rasterPreview');
  if (state.session) updateHistory(state.session.history || []);
  updateDatasetRegionControl();
  updateDatasetWorkspaceSummary();
  syncFigureTypeTrigger();
}

function updateDatasetWorkspaceSummary() {
  const title = $('datasetWorkspaceTitle');
  const meta = $('datasetWorkspaceMeta');
  if (!title || !meta) return;
  const sources = includedDatasetSources();
  if (!state.datasetSources.length) {
    title.textContent = t('dataWorkspaceEmpty');
    meta.textContent = t('dataWorkspaceEmptyHint');
    return;
  }
  if (!sources.length) {
    title.textContent = t('dataWorkspaceReady', 0, state.datasetSources.length);
    meta.textContent = t('noSourceSelected');
    return;
  }
  const usable = sources.flatMap(source => (source.scan?.files || []).filter(file => file.usable));
  const hic = usable.filter(file => file.role === 'hic').length;
  const tracks = usable.filter(file => ['signal', 'gene_annotation', 'intervals'].includes(file.role)).length;
  title.textContent = t('dataWorkspaceReady', sources.length, state.datasetSources.length);
  meta.textContent = t('dataWorkspaceReadyHint', hic, tracks, usable.length);
}

function syncFigureTypeTrigger() {
  const select = $('figureTypeSelect');
  const label = $('figureTypeButtonLabel');
  const meta = $('figureTypeButtonMeta');
  if (!select || !label || !meta) return;
  const item = currentFigureTypeItem();
  label.textContent = select.selectedOptions[0]?.textContent || t('selectFigureType');
  meta.textContent = item ? t('figureRequires', figureRequirementText(item)) : t('loadingTypes');
  syncFigureTypeChoices();
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
function isDraftSession() {
  return Boolean(state.session?.spec?.metadata?.draft);
}
function setChatEnabled(enabled=true) {
  $('chatInput').disabled = !enabled;
  $('chatForm').querySelector('button').disabled = !enabled;
}
function formatBp(value) { return value >= 1e6 ? `${(value/1e6).toFixed(2)}M` : value >= 1e3 ? `${(value/1e3).toFixed(0)}k` : String(value); }
function updateSession(payload) {
  state.session = payload;
  const spec = payload.spec;
  $('figureTitle').textContent = spec.title;
  $('regionLabel').textContent = `${spec.viewport.chrom}:${formatBp(spec.viewport.start)}–${formatBp(spec.viewport.end)}`;
  const resolution = spec.analysis?.resolution;
  $('resolutionLabel').textContent = resolution === 'auto'
    ? (state.language === 'en' ? 'auto' : '自动')
    : `${formatBp(resolution)} bp`;
  $('versionLabel').textContent = payload.version_id;
  $('undoButton').disabled = !payload.can_undo;
  $('redoButton').disabled = !payload.can_redo;
  if (spec.figure_type) $('figureTypeSelect').value = spec.figure_type;
  updateFigureTypeHint();
  const draft = isDraftSession();
  $('applyFigureType').disabled = draft;
  $('applyFigureType').hidden = draft;
  $('applyFigureType').textContent = draft ? t('selectFigure') : t('applyCurrent');
  setChatEnabled(true);
  updateHistory(payload.history || []);
  if (state.datasetScan) updateDatasetSelectionSummary();
  const managingData = Boolean($('dataDialog')?.open);
  closeDialog('figureTypeDialog');
  if (!managingData) switchSidebar('chat');
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
  if (!item) return {ready:false, missing:state.language === 'en' ? 'Choose a figure type' : '请先选择图类型', issues:[{kind:'figure_type'}]};
  const files = state.datasetScan?.scan?.files || [];
  if (!state.datasetScan || !files.length) {
    return {ready:false, missing:state.language === 'en' ? 'Scan a data directory first' : '请先扫描数据目录', issues:[{kind:'dataset'}]};
  }
  const chosen = new Set((selectedPaths || selectedDatasetPaths()).map(datasetPathKey));
  const selected = files.filter(file => file.usable && chosen.has(datasetPathKey(file.path)));
  const count = role => selected.filter(file => file.role === role && fileSupportsRole(file, role)).length;
  const hicCount = count('hic');
  const missing = [];
  const issues = [];
  const contract = item.input_contract || {};
  const roleRules = contract.roles || {};
  const pairing = contract.pairing || {};
  const anchorCount = count(pairing.anchor_role || 'hic');
  for (const [role, rule] of Object.entries(roleRules)) {
    const multiplier = rule.per_anchor ? anchorCount : 1;
    const minimum = Number(rule.min || 0) * multiplier;
    const maximum = rule.max == null ? null : Number(rule.max) * multiplier;
    const actual = count(role);
    const label = state.language === 'en' ? datasetRoleLabel(role) : (rule.label || datasetRoleLabel(role));
    if (actual < minimum) {
      const deficit = minimum - actual;
      missing.push(state.language === 'en'
        ? `${deficit} ${label} file${deficit === 1 ? '' : 's'}`
        : `${deficit} 个 ${label} 文件`);
      issues.push({kind:'missing_role', role, label, required:minimum, actual, deficit});
    } else if (maximum != null && actual > maximum) {
      missing.push(state.language === 'en'
        ? `at most ${maximum} ${label} file${maximum === 1 ? '' : 's'}`
        : `最多 ${maximum} 个 ${label} 文件`);
      issues.push({kind:'too_many_role', role, label, maximum, actual});
    }
  }
  for (const choice of contract.any_of || []) {
    const actual = (choice.roles || []).reduce((sum, role) => sum + count(role), 0);
    const minimum = Number(choice.min || 1);
    if (actual < minimum) {
      const labels = (choice.roles || []).map(datasetRoleLabel).join(state.language === 'en' ? ', ' : '、');
      missing.push(state.language === 'en' ? `at least ${minimum} of ${labels}` : `从 ${labels} 中至少选择 ${minimum} 个文件`);
      issues.push({kind:'any_of', roles:[...(choice.roles || [])], labels, required:minimum, actual, deficit:minimum - actual});
    }
  }
  const sharedResolutions = selectedCommonResolutions(selectedPaths);
  if (hicCount > 1 && sharedResolutions !== null && !sharedResolutions.length) {
    missing.push(state.language === 'en'
      ? 'the checked Hi-C files must share at least one resolution'
      : '已勾选的 Hi-C 必须至少有一个共同分辨率');
    issues.push({kind:'resolution'});
  }
  return {ready:missing.length === 0, missing:[...new Set(missing)].join(state.language === 'en' ? ', ' : '、'), issues};
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
    row.classList.toggle('ready', Boolean(state.datasetScan && readiness.ready));
    row.classList.toggle('needs-input', Boolean(state.datasetScan && !readiness.ready));
    row.classList.toggle('no-data', !state.datasetScan);
    const requirement = row.querySelector('.workflow-item-requirement');
    const action = row.querySelector('.workflow-item-action');
    if (requirement) requirement.textContent = readiness.ready
      ? `${t('workflowNeeds')}: ${(item.requires || []).join(state.language === 'en' ? ', ' : '、')}`
      : `${t('workflowNeeds')}: ${readiness.missing}`;
    if (action) action.textContent = readiness.ready ? t('useWorkflow') : t('missingData');
    const description = state.language === 'en' ? item.description : `${item.description}。点击后复用当前数据。`;
    row.title = readiness.ready ? description : `${description} ${state.language === 'en' ? `Missing: ${readiness.missing}` : `缺少：${readiness.missing}`}`;
    row.setAttribute('aria-label', `${row.querySelector('strong')?.textContent || item.label}。${requirement?.textContent || ''}。${action?.textContent || ''}`);
  });
  updateDirectFigureReadiness();
}
function updateDirectFigureReadiness() {
  const select = $('figureTypeSelect');
  if (!select) return;
  [...select.options].forEach(option => {
    const item = state.figureTypes.find(value => value.id === option.value);
    if (!item) return;
    const readiness = state.datasetScan ? workflowReadiness(item) : {ready:false, missing:''};
    const projectedReadiness = state.datasetScan ? workflowReadiness(item, projectedDatasetPathsForFigure(item)) : readiness;
    let availability = 'waiting';
    let availabilityLabel = t('figureWaitingData');
    let availabilityDetail = '';
    if (!item.ready) {
      availability = 'unavailable';
      availabilityLabel = t('figureUnavailable');
    } else if (state.datasetScan && readiness.ready) {
      availability = 'ready';
      availabilityLabel = t('figureReady');
    } else if (state.datasetScan && projectedReadiness.ready) {
      availability = 'ready';
      availabilityLabel = t('figureReady');
      availabilityDetail = t('figureAutoSelect');
    } else if (state.datasetScan) {
      availability = 'missing';
      availabilityLabel = t('figureNeedsData');
      availabilityDetail = t('figureMissingDetail', projectedReadiness.missing);
    }
    option.dataset.availability = availability;
    option.dataset.availabilityLabel = availabilityLabel;
    option.dataset.availabilityDetail = availabilityDetail;
    // Keep the option selectable even when its current inputs are incomplete.
    // The build button remains disabled and shows the missing requirement,
    // while this lets users switch to a two-sample figure type without losing
    // their checked files.
    option.disabled = !item.ready;
    if (availability === 'ready' && !availabilityDetail) {
      option.title = item.description;
    } else if (availability === 'ready') {
      option.title = state.language === 'en'
        ? `${item.description} Requires ${figureRequirementText(item)}; matching imported files will be selected automatically.`
        : `${item.description} 需要：${figureRequirementText(item)}；选择后将自动补齐已导入的匹配文件。`;
    } else if (availability === 'missing') {
      option.title = `${item.description} ${state.language === 'en' ? `Missing: ${projectedReadiness.missing}` : `缺少：${projectedReadiness.missing}`}`;
    } else {
      option.title = `${item.description} ${availabilityLabel}`;
    }
  });
  select.title = select.selectedOptions[0]?.title || '';
  syncFigureTypeChoices();
}
function syncFigureTypeChoices() {
  const selected = $('figureTypeSelect')?.value || '';
  document.querySelectorAll('#figureTypeChoices [data-figure-type]').forEach(button => {
    const option = [...$('figureTypeSelect').options].find(item => item.value === button.dataset.figureType);
    const current = button.dataset.figureType === selected;
    const availability = option?.dataset.availability || 'waiting';
    const availabilityLabel = option?.dataset.availabilityLabel || t('figureWaitingData');
    const availabilityDetail = option?.dataset.availabilityDetail || '';
    button.classList.toggle('current', current);
    for (const kind of ['ready', 'missing', 'waiting', 'unavailable']) button.classList.toggle(`is-${kind}`, availability === kind);
    button.setAttribute('aria-pressed', String(current));
    button.disabled = Boolean(option?.disabled);
    button.title = option?.title || '';
    const status = button.querySelector('.figure-choice-state');
    const detail = button.querySelector('.figure-choice-detail');
    if (status) status.textContent = availabilityLabel;
    if (detail) {
      detail.textContent = availabilityDetail;
      detail.hidden = !availabilityDetail;
    }
    const name = button.querySelector('.figure-choice-name')?.textContent || '';
    button.setAttribute('aria-label', [name, availabilityLabel, availabilityDetail].filter(Boolean).join('。'));
  });
}
function renderFigureTypeChoices() {
  const select = $('figureTypeSelect');
  const target = $('figureTypeChoices');
  if (!select || !target) return;
  target.replaceChildren();
  for (const optionGroup of select.querySelectorAll('optgroup')) {
    const section = document.createElement('section');
    section.className = 'figure-choice-group';
    section.setAttribute('aria-label', optionGroup.label);
    const heading = document.createElement('strong');
    heading.className = 'figure-choice-group-label';
    heading.textContent = optionGroup.label;
    const options = document.createElement('div');
    options.className = 'figure-choice-options';
    for (const option of optionGroup.querySelectorAll('option')) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.figureType = option.value;
      const main = document.createElement('span');
      main.className = 'figure-choice-main';
      const name = document.createElement('strong');
      name.className = 'figure-choice-name';
      name.textContent = option.textContent;
      const status = document.createElement('span');
      status.className = 'figure-choice-state';
      const detail = document.createElement('small');
      detail.className = 'figure-choice-detail';
      detail.hidden = true;
      main.append(name, status);
      button.append(main, detail);
      button.addEventListener('click', () => {
        select.value = option.value;
        select.dispatchEvent(new Event('change', {bubbles:true}));
      });
      options.appendChild(button);
    }
    section.append(heading, options);
    target.appendChild(section);
  }
  syncFigureTypeChoices();
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
    renderFigureTypeChoices();
    closeWorkflowConfigurator();
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
      const group = document.createElement('section'); group.className = 'workflow-group';
      const heading = document.createElement('div'); heading.className = 'workflow-category';
      const categoryName = document.createElement('strong'); categoryName.textContent = workflowCategoryLabels[category];
      const categoryCount = document.createElement('span'); categoryCount.textContent = String(items.length);
      heading.append(categoryName, categoryCount);
      const groupItems = document.createElement('div'); groupItems.className = 'workflow-group-items';
      for (const item of items) {
        const row = document.createElement('button'); row.type = 'button'; row.className = 'workflow-item';
        row.workflowItem = item;
        const main = document.createElement('span'); main.className = 'workflow-item-main';
        const name = document.createElement('strong'); name.textContent = state.language === 'en' ? (workflowEnglish[item.id] || item.label) : item.label;
        const requirement = document.createElement('span'); requirement.className = 'workflow-item-requirement';
        requirement.textContent = `${t('workflowNeeds')}: ${(item.requires || []).join(state.language === 'en' ? ', ' : '、')}`;
        main.append(name, requirement);
        const action = document.createElement('em'); action.className = 'workflow-item-action'; action.textContent = t('useWorkflow');
        row.append(main, action);
        row.title = state.language === 'en' ? item.description : `${item.description}。点击后复用当前数据。`;
        row.addEventListener('click', () => openWorkflowConfigurator(item, workflowEnglish[item.id] || item.label));
        groupItems.appendChild(row);
      }
      group.append(heading, groupItems); workflowList.appendChild(group);
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

function closeWorkflowConfigurator() {
  const panel = $('workflowConfigurator');
  const catalog = panel?.closest('.workflow-catalog');
  if (!panel || !catalog) return;
  panel.hidden = true;
  panel.replaceChildren();
  catalog.classList.remove('configuring');
  state.activeWorkflow = null;
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
  const catalog = panel.closest('.workflow-catalog');
  catalog.open = true;
  catalog.classList.add('configuring');
  panel.replaceChildren(); panel.hidden = false;
  const header = document.createElement('div'); header.className = 'workflow-config-header';
  const title = document.createElement('strong'); title.textContent = `${t('workflowInputs')}：${displayLabel}`;
  const controls = document.createElement('span');
  const back = document.createElement('button'); back.type = 'button'; back.className = 'workflow-config-back'; back.textContent = t('backToWorkflows');
  const recommended = document.createElement('button'); recommended.type = 'button'; recommended.textContent = t('selectRecommended');
  const all = document.createElement('button'); all.type = 'button'; all.textContent = t('selectAll');
  const clear = document.createElement('button'); clear.type = 'button'; clear.textContent = t('clearAll');
  controls.append(back, recommended, all, clear); header.append(title, controls); panel.appendChild(header);
  back.addEventListener('click', () => {
    closeWorkflowConfigurator();
    const body = catalog.closest('.figure-dialog-body');
    if (body) body.scrollTop = Math.max(0, catalog.offsetTop - 8);
  });
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
  requestAnimationFrame(() => {
    const body = catalog.closest('.figure-dialog-body');
    if (body) body.scrollTop = Math.max(0, catalog.offsetTop - 8);
  });
}
async function startWorkflow(item, displayLabel, workflowPaths=selectedDatasetPaths(), workflowBindings=[], pairingsConfirmed=false) {
  const hint = $('workflowActionHint');
  if (!requireValidDatasetRegion()) {
    hint.textContent = datasetRegionError();
    hint.hidden = false;
    return;
  }
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
  if ((!state.sessionId || isDraftSession()) && state.datasetScan) {
    try {
      hint.hidden = true;
      setStatus(t('workflowBuilding'), 'running', t('preparing'));
      const sessionId = `workflow_${Date.now()}`;
      const payload = await api('/api/sessions/from-dataset', {
        method:'POST',
        body:JSON.stringify({session_id:sessionId, path:state.datasetScan.path, source_paths:state.datasetScan.sourcePaths || [state.datasetScan.path], gene:datasetQuery(), reference_build:selectedReferenceBuild(), selected_paths:workflowPaths, figure_type:item.id, window:500000, resolution:selectedResolutionValue(), file_overrides:fileOverridesPayload(), workflow_bindings:workflowBindings, pairings_confirmed:pairingsConfirmed, render:true})
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
        source_paths:state.datasetScan?.sourcePaths || [],
        selected_paths:workflowPaths,
        gene:datasetQuery(),
        reference_build:selectedReferenceBuild(),
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
  syncFigureTypeTrigger();
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
async function ensureChatSession() {
  if (state.sessionId) return state.session;
  if (!state.chatSessionPromise) {
    const sessionId = `chat_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    state.chatSessionPromise = api('/api/sessions/blank', {
      method: 'POST',
      body: JSON.stringify({session_id: sessionId}),
    }).then(payload => {
      // A user may click “Load” before the background draft request returns.
      // Never let that late response replace a real data-backed session.
      if (!state.sessionId) {
        state.sessionId = payload.session_id;
        updateSession(payload);
        setChatEnabled(true);
        return payload;
      }
      return state.session;
    }).catch(error => {
      state.chatSessionPromise = null;
      setChatEnabled(false);
      throw error;
    });
  }
  return state.chatSessionPromise;
}
async function initializeChatSession() {
  try {
    await ensureChatSession();
  } catch (error) {
    setStatus(state.language === 'en' ? 'Chat unavailable' : '对话初始化失败', 'failed');
    addMessage('assistant', error.message, 'system:chat');
  }
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
  $('previewQuality').hidden = true;
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
      const preview = svg || png;
      if (preview) {
        const isVector = Boolean(svg);
        $('figureImage').src = `${preview}?t=${Date.now()}`;
        $('figureImage').classList.toggle('vector-preview', isVector);
        $('figureImage').dataset.previewFormat = isVector ? 'svg' : 'png';
        $('figureImage').hidden = false;
        $('figurePreviewFrame').hidden = false;
        $('figureZoomControl').hidden = false;
        $('emptyState').hidden = true;
        $('canvas').classList.remove('empty');
        $('previewQuality').textContent = t(isVector ? 'vectorPreview' : 'rasterPreview');
        $('previewQuality').hidden = false;
      }
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
$('openDataDialog').addEventListener('click', () => {
  openDialog('dataDialog', 'dataPath');
  updateDatasetRegionControl();
});
$('openApiDialog').addEventListener('click', () => openDialog('apiDialog', 'plannerSelect'));
$('openFigureTypeDialog').addEventListener('click', () => {
  updateWorkflowReadiness();
  openDialog('figureTypeDialog');
  requestAnimationFrame(() => {
    const body = $('figureTypeDialog').querySelector('.figure-dialog-body');
    const current = $('figureTypeChoices').querySelector('.current:not(:disabled)');
    if (body && current) body.scrollTop = Math.max(0, current.offsetTop - body.offsetTop - 18);
    current?.focus({preventScroll:true});
  });
});
document.querySelectorAll('[data-dialog-close]').forEach(button => {
  button.addEventListener('click', () => closeDialog(button.dataset.dialogClose));
});
document.querySelectorAll('dialog.settings-dialog').forEach(dialog => {
  dialog.addEventListener('click', event => {
    if (event.target === dialog) closeDialog(dialog.id);
  });
  dialog.addEventListener('close', () => {
    if (dialog.id === 'figureTypeDialog') closeWorkflowConfigurator();
    if (!document.querySelector('dialog[open]')) document.body.classList.remove('dialog-open');
  });
});
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
    addMessage('assistant', state.language === 'en'
      ? 'The FOXJ1 multi-omics example is ready. You can now adjust its region, tracks, or visual style in this chat.'
      : 'FOXJ1 多组学示例已载入。现在可以在对话中调整区域、轨道或视觉样式。');
    watchJob(payload.job);
  } catch(error) { setStatus('载入失败'); addMessage('assistant', error.message); }
});
$('dataForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const path = $('dataPath').value.trim();
  if (!path) return;
  if (/\.(?:m?cool)$/i.test(path)) {
    if (!requireValidDatasetRegion()) return;
    try {
      setStatus('正在检查数据','running','正在读取文件元数据并选择合适的分辨率……');
      const sessionId=`hic_${Date.now()}`;
      const payload=await api('/api/sessions/from-hic',{method:'POST',body:JSON.stringify({session_id:sessionId,hic_path:path,region:datasetQuery(),reference_build:selectedReferenceBuild(),figure_type:$('figureTypeSelect').value})});
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
function projectedDatasetPathsForFigure(item) {
  const files = (state.datasetScan?.scan?.files || []).filter(file => (
    file.usable
    && workflowRelevantRoles(item).has(file.role)
    && fileSupportsRole(file, file.role)
  ));
  const selected = new Set(selectedDatasetPaths().map(datasetPathKey));
  let projected = files.filter(file => selected.has(datasetPathKey(file.path)));
  const contract = item?.input_contract || {};
  const anchorRole = contract.pairing?.anchor_role || 'hic';
  const rules = Object.entries(contract.roles || {}).sort(([left], [right]) => (
    left === anchorRole ? -1 : right === anchorRole ? 1 : 0
  ));
  const filesForRole = role => files.filter(file => file.role === role);
  const projectedForRole = role => projected.filter(file => file.role === role);

  for (const [role, rule] of rules) {
    const multiplier = rule.per_anchor ? projectedForRole(anchorRole).length : 1;
    const minimum = Number(rule.min || 0) * multiplier;
    const maximum = rule.max == null ? null : Number(rule.max) * multiplier;
    let chosen = projectedForRole(role);
    if (maximum != null && chosen.length > maximum) {
      const keep = new Set(chosen.slice(0, maximum).map(file => datasetPathKey(file.path)));
      projected = projected.filter(file => file.role !== role || keep.has(datasetPathKey(file.path)));
      chosen = projectedForRole(role);
    }
    for (const file of filesForRole(role)) {
      if (chosen.length >= minimum) break;
      if (!projected.includes(file)) {
        projected.push(file);
        chosen.push(file);
      }
    }
  }
  for (const choice of contract.any_of || []) {
    const roles = new Set(choice.roles || []);
    let count = projected.filter(file => roles.has(file.role)).length;
    for (const file of files.filter(value => roles.has(value.role))) {
      if (count >= Number(choice.min || 1)) break;
      if (!projected.includes(file)) {
        projected.push(file);
        count += 1;
      }
    }
  }
  return projected.map(file => file.path);
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
function figureRequirementText(item) {
  const contract = item?.input_contract || {};
  const parts = [];
  for (const [role, rule] of Object.entries(contract.roles || {})) {
    const minimum = Number(rule.min || 0);
    if (!minimum) continue;
    const label = state.language === 'en' ? datasetRoleLabel(role) : (rule.label || datasetRoleLabel(role));
    if (rule.per_anchor) {
      parts.push(state.language === 'en'
        ? `${minimum} ${label} per Hi-C`
        : `每个 Hi-C 配 ${minimum} 个 ${label} 文件`);
    } else {
      parts.push(state.language === 'en'
        ? `${minimum} ${label} file${minimum === 1 ? '' : 's'}`
        : `${minimum} 个 ${label} 文件`);
    }
  }
  for (const choice of contract.any_of || []) {
    const minimum = Number(choice.min || 1);
    const labels = (choice.roles || []).map(datasetRoleLabel).join(state.language === 'en' ? ' / ' : ' / ');
    parts.push(state.language === 'en' ? `${minimum} ${labels} file${minimum === 1 ? '' : 's'}` : `${minimum} 个 ${labels} 文件`);
  }
  return parts.join(state.language === 'en' ? ' + ' : ' + ') || cardinalityText(hicCardinality(item));
}
function readinessStatusText(readiness, selectionMismatch=false) {
  if (selectionMismatch) return state.language === 'en' ? 'Hi-C count mismatch' : 'Hi-C 数量不符';
  const issues = readiness?.issues || [];
  const missingLabels = [...new Set(issues.filter(issue => issue.kind === 'missing_role').map(issue => issue.label))];
  if (missingLabels.length) return state.language === 'en'
    ? `Missing ${missingLabels[0]}${missingLabels.length > 1 ? ` +${missingLabels.length - 1}` : ''}`
    : `缺少 ${missingLabels[0]}${missingLabels.length > 1 ? ` 等 ${missingLabels.length} 项` : ''}`;
  if (issues.some(issue => issue.kind === 'resolution')) return state.language === 'en' ? 'Resolution mismatch' : '分辨率不匹配';
  const excessive = issues.find(issue => issue.kind === 'too_many_role');
  if (excessive) return state.language === 'en' ? `Too many ${excessive.label}` : `${excessive.label} 选择过多`;
  return state.language === 'en' ? 'Needs attention' : '需要调整';
}
function hideFigureReadinessHint() {
  const hint = $('figureReadinessHint');
  if (hint) hint.hidden = true;
}
function updateFigureReadinessHint(readiness, selectionMismatch=false, rule=null) {
  const hint = $('figureReadinessHint');
  const message = $('figureReadinessMessage');
  const action = $('figureReadinessAction');
  if (!hint || !message || !action) return;
  if (readiness?.ready && !selectionMismatch) {
    hint.hidden = true;
    action.hidden = true;
    return;
  }
  const issues = readiness?.issues || [];
  const firstIssue = issues[0] || null;
  const selectionIssue = selectionMismatch && rule
    ? (selectedHicFiles().length < Number(rule.min || 0) ? 'missing_role' : 'too_many_role')
    : firstIssue?.kind || '';
  let text = selectionMismatch
    ? (state.language === 'en'
      ? `The Hi-C selection does not match this figure. Select ${cardinalityText(rule)} below.`
      : `Hi-C 选择数量不符合要求：需要 ${cardinalityText(rule)}，请在下方重新选择。`)
    : (state.language === 'en' ? `Missing: ${readiness?.missing || 'required input'}.` : `缺少：${readiness?.missing || '所需输入'}。`);
  let mode = 'files';
  let roles = [];
  if (!selectionMismatch && firstIssue?.kind === 'resolution') {
    mode = 'resolution';
    text += state.language === 'en' ? ' Choose Hi-C files with a shared resolution.' : '请选择具有共同分辨率的 Hi-C 文件。';
  } else {
    roles = firstIssue?.kind === 'any_of' ? firstIssue.roles : firstIssue?.role ? [firstIssue.role] : selectionMismatch ? ['hic'] : [];
    const selected = new Set(selectedDatasetPaths().map(datasetPathKey));
    const available = (state.datasetScan?.scan?.files || []).filter(file => (
      file.usable
      && (!roles.length || roles.includes(file.role))
      && fileSupportsRole(file, file.role)
      && !selected.has(datasetPathKey(file.path))
    ));
    const missingSelection = selectionMismatch && selectionIssue === 'missing_role';
    const missingContractInput = !selectionMismatch && ['missing_role','any_of','dataset'].includes(firstIssue?.kind);
    if ((missingSelection || missingContractInput) && !available.length) {
      mode = 'upload';
      text += state.language === 'en'
        ? ' No matching file was found in the imported sources; upload a supporting file.'
        : '当前导入的数据源中未识别到对应文件，请上传补充文件。';
    } else if (selectionIssue === 'too_many_role') {
      text += state.language === 'en' ? ' Remove the extra selection below.' : '请在下方取消多余文件。';
    } else {
      text += state.language === 'en' ? ' Select the matching file below.' : '请在下方选择对应文件。';
    }
  }
  message.textContent = text;
  action.dataset.mode = mode;
  action.dataset.roles = roles.join(',');
  action.dataset.issue = selectionIssue;
  action.textContent = mode === 'upload'
    ? t('uploadSupplement')
    : mode === 'resolution'
      ? (state.language === 'en' ? 'Review resolution' : '检查分辨率')
      : (state.language === 'en' ? 'Review file selection' : '查看文件选择');
  action.hidden = false;
  hint.hidden = false;
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
      && (file.role === 'gene_annotation' || allowedRoles.has(file.role))
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
function cloneFileOverrides(overrides={}) {
  return Object.fromEntries(Object.entries(overrides || {}).map(([path, value]) => [path, {...value}]));
}
function activeDatasetSource() {
  return state.datasetSources.find(source => source.key === state.activeDatasetKey) || null;
}
function includedDatasetSources() {
  return state.datasetSources.filter(source => source.included !== false);
}
function datasetSourceLabel(source) {
  const compact = String(source?.path || '').replace(/[\\/]+$/, '');
  return compact.split(/[\\/]/).pop() || compact || (state.language === 'en' ? 'Data source' : '数据源');
}
function focusDatasetSourceFields(source) {
  if (!source) return;
  state.activeDatasetKey = source.key;
  $('dataPath').value = source.path;
  const referenceOption = [...$('referenceBuild').options].find(option => (
    !option.dataset.annotationPath && option.dataset.referenceBuild === source.referenceBuild
  ));
  if (referenceOption) $('referenceBuild').value = referenceOption.value;
  if (source.figureType && [...$('figureTypeSelect').options].some(option => option.value === source.figureType)) $('figureTypeSelect').value = source.figureType;
  updateReferenceBuildStatus();
  updateFigureTypeHint();
}
function combinedDatasetScan(sources) {
  const files = [];
  const seen = new Set();
  for (const source of sources) {
    for (const file of source.scan?.files || []) {
      const key = datasetPathKey(file.path);
      if (seen.has(key)) continue;
      seen.add(key);
      file.dataset_source_key = source.key;
      file.dataset_source_label = datasetSourceLabel(source);
      files.push(file);
    }
  }
  const usable = files.filter(file => file.usable);
  const roles = new Set(usable.map(file => file.role));
  let missing = [...new Set(sources.flatMap(source => source.scan?.missing || []))];
  let warnings = [...new Set(sources.flatMap(source => source.scan?.warnings || []))];
  if (roles.has('hic')) missing = missing.filter(message => !/m?cool/i.test(message));
  if (roles.has('signal')) warnings = warnings.filter(message => !message.includes('未发现 BigWig'));
  if (roles.has('gene_annotation')) warnings = warnings.filter(message => !message.includes('未发现 GTF') && !message.includes('均未发现基因注释'));
  const hicCount = usable.filter(file => file.role === 'hic').length;
  const trackCount = usable.filter(file => ['signal', 'gene_annotation', 'intervals'].includes(file.role)).length;
  const capabilities = [];
  const capabilityIds = new Set();
  for (const source of sources) {
    for (const capability of source.scan?.capabilities || []) {
      if (capabilityIds.has(capability.id)) continue;
      capabilityIds.add(capability.id);
      capabilities.push(capability);
    }
  }
  return {
    root:sources.map(source => source.scan?.root || source.path).join(' | '),
    files,
    reference:sources.find(source => source.scan?.reference?.user_annotation)?.scan.reference || sources[0]?.scan?.reference || {},
    gene:sources.find(source => source.scan?.gene)?.scan.gene || null,
    chromosomes:sources.find(source => (source.scan?.chromosomes || []).length)?.scan.chromosomes || [],
    resolutions:sources.find(source => (source.scan?.resolutions || []).length)?.scan.resolutions || [],
    capabilities,
    missing,
    warnings,
    summary:t('combinedSourceSummary', sources.length, hicCount, trackCount, usable.length),
  };
}
function saveActiveDatasetSourceView() {
  if (state.datasetSourceRestoring) return;
  const sources = includedDatasetSources();
  if (!sources.length) return;
  const selected = new Set(selectedDatasetPaths().map(datasetPathKey));
  const hasPicker = Boolean($('datasetFiles').querySelector('.dataset-file-picker'));
  for (const source of sources) {
    const sourceKeys = new Set((source.scan?.files || []).map(file => datasetPathKey(file.path)));
    if (hasPicker) source.selectedPaths = (source.scan?.files || []).filter(file => selected.has(datasetPathKey(file.path))).map(file => file.path);
    source.fileOverrides = Object.fromEntries(
      Object.entries(state.fileOverrides || {}).filter(([path]) => sourceKeys.has(datasetPathKey(path))).map(([path, value]) => [path, {...value}]),
    );
  }
  const resolution = $('datasetResolutionSelect');
  for (const source of sources) {
    if (resolution?.options?.length) source.resolution = resolution.value || 'auto';
    if ($('figureTypeSelect').value) source.figureType = $('figureTypeSelect').value;
  }
}
function renderDatasetSourceShelf() {
  const shelf = $('datasetSourceShelf');
  const list = $('datasetSourceList');
  if (!shelf || !list) return;
  shelf.hidden = state.datasetSources.length === 0;
  const includedCount = includedDatasetSources().length;
  $('datasetSourceCount').textContent = t('sourceSelectionCount', includedCount, state.datasetSources.length);
  list.replaceChildren();
  for (const source of state.datasetSources) {
    const included = source.included !== false;
    const files = (source.scan?.files || []).filter(file => file.usable);
    const hicCount = files.filter(file => file.role === 'hic').length;
    const card = document.createElement('div');
    card.className = `dataset-source-card${included ? ' included' : ''}`;

    const main = document.createElement('label'); main.className = 'dataset-source-main';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = included; checkbox.className = 'dataset-source-checkbox';
    checkbox.setAttribute('aria-label', `${included ? t('includedSource') : t('excludedSource')} · ${datasetSourceLabel(source)}`);
    const copy = document.createElement('span'); copy.className = 'dataset-source-copy';
    const titleRow = document.createElement('span'); titleRow.className = 'dataset-source-title-row';
    const name = document.createElement('strong'); name.textContent = datasetSourceLabel(source);
    const statePill = document.createElement('span'); statePill.className = 'dataset-source-state'; statePill.textContent = included ? t('includedSource') : t('excludedSource');
    titleRow.append(name, statePill);
    const meta = document.createElement('span'); meta.className = 'dataset-source-meta'; meta.textContent = t('sourceMeta', hicCount, files.length);
    const path = document.createElement('span'); path.className = 'dataset-source-path'; path.textContent = source.path;
    copy.append(titleRow, meta, path); main.append(checkbox, copy);
    checkbox.addEventListener('change', () => toggleDatasetSource(source.key, checkbox.checked));

    const actions = document.createElement('span'); actions.className = 'dataset-source-actions';
    const refresh = document.createElement('button');
    refresh.type = 'button'; refresh.className = 'dataset-source-refresh'; refresh.textContent = t('refreshSource');
    refresh.setAttribute('aria-label', `${t('refreshSource')} · ${datasetSourceLabel(source)}`);
    refresh.addEventListener('click', () => { void refreshDatasetSource(source.key); });
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'dataset-source-remove'; remove.textContent = t('removeSource');
    remove.setAttribute('aria-label', `${t('removeSource')} · ${datasetSourceLabel(source)}`);
    remove.title = state.language === 'en' ? 'Remove from this page only; files on disk are not deleted.' : '仅从当前页面移除，不会删除磁盘文件。';
    remove.addEventListener('click', () => removeDatasetSource(source.key));
    actions.append(refresh, remove);
    card.append(main, actions);
    list.appendChild(card);
  }
  updateDatasetWorkspaceSummary();
}
function appendDatasetNotices(scan) {
  $('datasetResults').querySelectorAll('.dataset-notices').forEach(node => node.remove());
  const notices = [...(scan?.missing || []), ...(scan?.warnings || [])];
  if (!notices.length) return;
  const note = document.createElement('small');
  note.className = 'dataset-notices';
  note.textContent = notices.join(state.language === 'en' ? '; ' : '；');
  $('datasetResults').querySelector('.dataset-results-body')?.appendChild(note);
}
function hideDatasetAuthorization() {
  const banner = $('datasetAuthorizationBanner');
  if (banner) banner.hidden = true;
}
function showDatasetAuthorization(path, detail='', failed=false) {
  const banner = $('datasetAuthorizationBanner');
  const message = $('datasetAuthorizationMessage');
  const button = $('authorizeDataset');
  if (!banner || !message || !button) return;
  message.textContent = failed ? t('authorizationFailed', detail) : t('authorizationMessage', path);
  message.title = failed ? detail : '';
  button.hidden = false;
  banner.hidden = false;
  openDialog('dataDialog');
  requestAnimationFrame(() => {
    banner.scrollIntoView({behavior:'smooth', block:'nearest'});
    button.focus({preventScroll:true});
  });
}
function renderCombinedDatasetWorkspace(focusedSource=null, fillMinimum=false) {
  const sources = includedDatasetSources();
  if (!sources.length) {
    state.datasetScan = null;
    state.fileOverrides = {};
    const results = $('datasetResults');
    results.hidden = state.datasetSources.length === 0;
    results.classList.remove('error');
    $('datasetSummary').textContent = t('noSourceSelected');
    $('datasetFiles').replaceChildren();
    $('datasetResolution').hidden = true;
    $('uploadDataset').hidden = true;
    $('buildDataset').hidden = true;
    $('addTracksDataset').hidden = true;
    hideFigureReadinessHint();
    $('workflowActionHint').hidden = true;
    updateWorkflowReadiness();
    renderDatasetSourceShelf();
    updateDatasetWorkspaceSummary();
    renderReferenceBuilds();
    return;
  }
  const focus = focusedSource && sources.includes(focusedSource)
    ? focusedSource
    : (activeDatasetSource() && sources.includes(activeDatasetSource()) ? activeDatasetSource() : sources[0]);
  state.datasetSourceRestoring = true;
  try {
    if (focusedSource) focusDatasetSourceFields(focus);
    state.activeDatasetKey = focus.key;
    const scan = combinedDatasetScan(sources);
    state.datasetScan = {path:focus.path, sourcePaths:sources.map(source => source.path), gene:datasetQuery(), scan};
    state.fileOverrides = Object.assign({}, ...sources.map(source => cloneFileOverrides(source.fileOverrides)));

    const results = $('datasetResults');
    results.hidden = false;
    results.classList.remove('error');
    results.querySelectorAll('.dataset-import-error,.dataset-notices').forEach(node => node.remove());
    $('datasetSummary').replaceChildren();
    const summary = document.createElement('strong'); summary.textContent = scan.summary;
    $('datasetSummary').appendChild(summary);
    $('workflowActionHint').textContent = state.language === 'en'
      ? 'Files detected. Choose a figure type above; workflows stay disabled until their required inputs are present. Upload supporting files below when needed.'
      : '文件已识别。请在上方选择图类型；下方工作流会在依赖齐全后启用，缺少文件时可从下方上传补充。';
    $('workflowActionHint').hidden = false;
    const selectedPaths = sources.flatMap(source => Array.isArray(source.selectedPaths) ? source.selectedPaths : []);
    renderDatasetFiles(scan, selectedPaths, fillMinimum);
    renderReferenceBuilds();
    $('buildDataset').hidden = false;
    $('uploadDataset').hidden = false;
    appendDatasetNotices(scan);
    updateDatasetResolutionControl();
    const resolution = String(focus.resolution || 'auto');
    if ([...$('datasetResolutionSelect').options].some(option => option.value === resolution)) $('datasetResolutionSelect').value = resolution;
    const hasRealSession = Boolean(state.sessionId && !isDraftSession());
    $('applyFigureType').disabled = !hasRealSession;
    $('applyFigureType').hidden = !hasRealSession;
    $('applyFigureType').textContent = hasRealSession ? t('applyCurrent') : t('selectFigure');
  } finally {
    state.datasetSourceRestoring = false;
  }
  updateDatasetSelectionSummary();
  renderDatasetSourceShelf();
  updateDatasetWorkspaceSummary();
}
function displayDatasetSource(source, announce=false, fillMinimum=false) {
  if (!source) return;
  focusDatasetSourceFields(source);
  renderCombinedDatasetWorkspace(source, fillMinimum);
  if (announce && !state.activeJob) setStatus(t('sourceIncluded', datasetSourceLabel(source), includedDatasetSources().length), 'success');
}
function toggleDatasetSource(key, included) {
  saveActiveDatasetSourceView();
  const source = state.datasetSources.find(item => item.key === key);
  if (!source) return;
  source.included = included;
  const nextFocus = included ? source : (includedDatasetSources()[0] || source);
  renderCombinedDatasetWorkspace(nextFocus, false);
  if (!state.activeJob) setStatus(included ? t('sourceIncluded', datasetSourceLabel(source), includedDatasetSources().length) : t('sourceExcluded', datasetSourceLabel(source)), included ? 'success' : 'checking');
}
async function refreshDatasetSource(key) {
  const source = state.datasetSources.find(item => item.key === key);
  if (!source) return;
  saveActiveDatasetSourceView();
  focusDatasetSourceFields(source);
  await scanDatasetPath(source.path, {
    gene:datasetQuery(),
    geneInput:$('datasetGene').value.trim(),
    regionEdited:datasetRegionMode() === 'manual',
    referenceBuild:selectedReferenceBuild(),
    refresh:true,
  });
}
function clearDatasetWorkspace() {
  state.datasetScan = null;
  state.activeDatasetKey = null;
  state.fileOverrides = {};
  $('datasetResults').hidden = true;
  $('datasetResults').classList.remove('error');
  $('datasetSummary').replaceChildren();
  $('datasetFiles').replaceChildren();
  $('datasetResolution').hidden = true;
  hideDatasetAuthorization();
  $('uploadDataset').hidden = true;
  $('buildDataset').hidden = true;
  $('addTracksDataset').hidden = true;
  hideFigureReadinessHint();
  $('workflowActionHint').hidden = true;
  const hasRealSession = Boolean(state.sessionId && !isDraftSession());
  $('applyFigureType').disabled = !hasRealSession;
  $('applyFigureType').hidden = !hasRealSession;
  $('applyFigureType').textContent = hasRealSession ? t('applyCurrent') : t('selectFigure');
  updateWorkflowReadiness();
  renderDatasetSourceShelf();
  updateDatasetRegionControl();
  updateDatasetWorkspaceSummary();
  renderReferenceBuilds();
}
function removeDatasetSource(key) {
  saveActiveDatasetSourceView();
  const index = state.datasetSources.findIndex(source => source.key === key);
  if (index < 0) return;
  const [removed] = state.datasetSources.splice(index, 1);
  const next = includedDatasetSources()[0] || state.datasetSources[Math.min(index, state.datasetSources.length - 1)] || null;
  if (next) renderCombinedDatasetWorkspace(next, false);
  else clearDatasetWorkspace();
  if (!state.activeJob) setStatus(t('sourceRemoved', datasetSourceLabel(removed)), 'success');
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
  if (!picker) { hideFigureReadinessHint(); updateDatasetResolutionControl(); updateWorkflowReadiness(); updateDatasetRegionControl(); syncReferenceChoiceFromFiles(); return; }
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
  const newTracks = newSelectedTrackFiles();
  const validHicCount = !rule || (selectedHics.length >= rule.min && (rule.max == null || selectedHics.length <= rule.max));
  const readiness = state.datasetScan ? workflowReadiness(item) : {ready:true, missing:'', issues:[]};
  const figureReady = readiness.ready;
  const tracksUnsupported = selectedTracks.length > 0 && !trackFigureSupported;
  const selectionMismatch = Boolean(rule && !validHicCount);
  const regionError = datasetRegionError();
  // A track selection must never prevent the selected Hi-C figure from being
  // built. Unsupported tracks remain available for an integrated workflow.
  const canBuildFigure = selectedHics.length > 0 && validHicCount && figureReady && !regionError;
  const comparisonHint = selectedHics.length >= 2 && rule?.max === 1
    ? (state.language === 'en'
      ? 'Tip: choose “Two-sample Hi-C comparison” to use both.'
      : '提示：要对比两个样本，请选择“双样本 Hi-C 对比”。')
    : '';
  const count = picker.querySelector('.dataset-selection-count');
  if (count) {
    count.replaceChildren();
    const header = document.createElement('div'); header.className = 'selection-summary-header';
    const headerCopy = document.createElement('div');
    const title = document.createElement('strong'); title.textContent = state.language === 'en' ? 'Current figure inputs' : '当前绘图输入';
    const requirement = document.createElement('small');
    requirement.textContent = `${item?.label || $('figureTypeSelect').value} · ${state.language === 'en' ? 'Requires' : '需要'} ${figureRequirementText(item)}`;
    headerCopy.append(title, requirement);
    const status = document.createElement('span'); status.className = `selection-status ${canBuildFigure ? 'ready' : 'pending'}`;
    status.textContent = canBuildFigure
      ? (state.language === 'en' ? 'Ready' : '可以生成')
      : regionError || readinessStatusText(readiness, selectionMismatch);
    header.append(headerCopy, status); count.appendChild(header);

    const metrics = document.createElement('div'); metrics.className = 'selection-metrics';
    const addMetric = (labelText, valueText, tone='') => {
      const metric = document.createElement('div'); metric.className = `selection-metric${tone ? ` ${tone}` : ''}`;
      const label = document.createElement('span'); label.textContent = labelText;
      const value = document.createElement('strong'); value.textContent = valueText;
      metric.append(label, value); metrics.appendChild(metric);
    };
    addMetric('Hi-C', selectedNames.length
      ? `${selectedNames.length} · ${selectedNames.join(state.language === 'en' ? ', ' : '、')}`
      : (state.language === 'en' ? 'Not selected' : '尚未选择'), selectedNames.length ? 'has-value' : 'empty');
    addMetric(state.language === 'en' ? 'Resolution' : '共同分辨率', sharedResolutions?.length
      ? sharedResolutions.map(value => `${formatBp(value)} bp`).join(state.language === 'en' ? ', ' : '、')
      : sharedResolutions === null ? (state.language === 'en' ? 'Server validation' : '服务端校验') : (state.language === 'en' ? 'None' : '无'), sharedResolutions?.length ? 'has-value' : 'empty');
    addMetric(state.language === 'en' ? 'Tracks' : '附加轨道', selectedTrackNames.length
      ? `${selectedTrackNames.length} · ${selectedTrackNames.join(state.language === 'en' ? ', ' : '、')}`
      : (state.language === 'en' ? 'None' : '无'), selectedTrackNames.length ? 'has-value' : 'empty');
    count.appendChild(metrics);
    if (comparisonHint) {
      const tip = document.createElement('p'); tip.className = 'selection-tip'; tip.textContent = comparisonHint; count.appendChild(tip);
    }
  }
  const toolbarTitle = picker.querySelector('.dataset-file-toolbar-title');
  if (toolbarTitle) {
    const selectedCount = selectedDatasetPaths().length;
    const fileCount = Number(picker.dataset.fileCount || 0);
    toolbarTitle.textContent = state.language === 'en'
      ? `Detected files · ${selectedCount}/${fileCount} selected`
      : `检测到的文件 · 已选 ${selectedCount}/${fileCount}`;
  }
  picker.querySelectorAll('.dataset-file').forEach(row => {
    row.classList.toggle('selected', Boolean(row.querySelector('input[data-dataset-path]')?.checked));
  });
  updateFigureReadinessHint(readiness, selectionMismatch, rule);
  $('buildDataset').disabled = !canBuildFigure;
  $('addTracksDataset').hidden = !state.sessionId || isDraftSession() || !selectedTracks.length || !trackFigureSupported;
  $('addTracksDataset').disabled = !newTracks.length || !trackFigureSupported;
  $('addTracksDataset').textContent = newTracks.length
    ? (state.language === 'en' ? `Add ${newTracks.length} new track${newTracks.length === 1 ? '' : 's'} to current figure` : `添加 ${newTracks.length} 条新轨道到当前图`)
    : t('trackAlreadyPresent');
  $('buildDataset').textContent = canBuildFigure
      ? (state.language === 'en'
        ? `Build “${item?.label || 'figure'}”${tracksUnsupported ? ` ${t('tracksExcluded')}` : ''}`
        : `生成「${item?.label || '所选图形'}」${tracksUnsupported ? t('tracksExcluded') : ''}`)
      : regionError
        ? t('specifyRegion')
      : selectionMismatch
        ? (state.language === 'en' ? `Adjust Hi-C selection (${cardinalityText(rule)})` : `调整 Hi-C 选择（${cardinalityText(rule)}）`)
        : (state.language === 'en' ? `Missing: ${readiness.missing || 'required input'}` : `缺少：${readiness.missing || '所需输入'}`);
  $('buildDataset').title = canBuildFigure ? '' : (regionError
    ? regionError
    : selectionMismatch
    ? (state.language === 'en' ? `Select ${cardinalityText(rule)}.` : `请选择${cardinalityText(rule)}。`)
    : (state.language === 'en' ? `Missing: ${readiness.missing}` : `缺少：${readiness.missing}`));
  if (tracksUnsupported) {
    $('workflowActionHint').textContent = state.language === 'en'
      ? `${item?.label || 'This figure'} uses a CFIZZ API without an integrated track panel. The Hi-C figure can still be built; switch to an integrated CFIZZ workflow to place the checked tracks below it.`
      : `${item?.label || '当前图形'} 使用的 CFIZZ 官方接口没有整合轨道面板。仍可生成 Hi-C 图；如需把已选轨道放在图下方，请切换到支持整合轨道的 CFIZZ 工作流。`;
    $('workflowActionHint').hidden = false;
  }
  updateWorkflowReadiness();
  saveActiveDatasetSourceView();
  syncReferenceChoiceFromFiles();
  scheduleDatasetRegionPreview();
}
function renderDatasetFiles(scan, selectedPaths=null, fillMinimum=false) {
  const target = $('datasetFiles');
  target.replaceChildren();
  const files = scan.files.filter(item => item.usable);
  const selectedKeys = Array.isArray(selectedPaths) ? new Set(selectedPaths.map(datasetPathKey)) : null;
  if (!files.length) {
    target.textContent = state.language === 'en' ? 'No supported files were found.' : '没有发现可选择的支持文件。';
    return;
  }
  const picker = document.createElement('div');
  picker.className = 'dataset-file-picker';
  picker.dataset.fileCount = String(files.length);
  const heading = document.createElement('div');
  heading.className = 'dataset-selection-count';
  picker.appendChild(heading);
  const fileSection = document.createElement('details');
  fileSection.className = 'dataset-files-section';
  const toolbar = document.createElement('summary');
  toolbar.className = 'dataset-file-toolbar';
  const toolbarRow = document.createElement('span');
  toolbarRow.className = 'dataset-file-toolbar-row';
  const toolbarTitle = document.createElement('strong');
  toolbarTitle.className = 'dataset-file-toolbar-title';
  const toolbarActions = document.createElement('span');
  toolbarActions.className = 'dataset-file-toolbar-actions';
  const selectAll = document.createElement('button');
  selectAll.type = 'button'; selectAll.textContent = t('selectAll');
  const clearAll = document.createElement('button');
  clearAll.type = 'button'; clearAll.textContent = t('clearAll');
  const setAll = (checked, event) => {
    event.preventDefault();
    event.stopPropagation();
    picker.querySelectorAll('input[data-dataset-path]:not(:disabled)').forEach(input => { input.checked = checked; });
    enforceDatasetSelectionForFigure(null, false);
  };
  selectAll.addEventListener('click', event => setAll(true, event));
  clearAll.addEventListener('click', event => setAll(false, event));
  toolbarActions.append(selectAll, clearAll);
  toolbarRow.append(toolbarTitle, toolbarActions);
  toolbar.appendChild(toolbarRow);
  const fileList = document.createElement('div');
  fileList.className = 'dataset-file-list';
  fileSection.append(toolbar, fileList);
  picker.appendChild(fileSection);
  const showSourceGroups = new Set(files.map(file => file.dataset_source_key).filter(Boolean)).size > 1;
  let previousSourceKey = null;
  for (const file of files) {
    if (showSourceGroups && file.dataset_source_key !== previousSourceKey) {
      const sourceHeading = document.createElement('div'); sourceHeading.className = 'dataset-file-source-heading';
      const sourceName = document.createElement('strong'); sourceName.textContent = file.dataset_source_label || (state.language === 'en' ? 'Data source' : '数据源');
      const sourceHint = document.createElement('span'); sourceHint.textContent = state.language === 'en' ? 'Imported source' : '导入来源';
      sourceHeading.append(sourceName, sourceHint); fileList.appendChild(sourceHeading);
      previousSourceKey = file.dataset_source_key;
    }
    const row = document.createElement('div');
    row.className = `dataset-file${file.role === 'unknown' ? ' unknown' : ''}`;
    row.title = `${file.path}\n${(file.role_evidence || []).join('；')}`;
    const label = document.createElement('label'); label.className = 'dataset-file-select';
    const input = document.createElement('input');
    input.type = 'checkbox'; input.checked = selectedKeys ? selectedKeys.has(datasetPathKey(file.path)) : false; input.dataset.datasetPath = file.path;
    const compatibleRoles = compatibleRolesForFile(file);
    const sourceCompatible = fileSupportsRole(file, file.role);
    input.disabled = file.role === 'unknown' || !sourceCompatible || !compatibleRoles.length;
    if (input.disabled) input.checked = false;
    input.addEventListener('change', () => {
      if (file.role === 'gene_annotation' && input.checked) {
        for (const other of document.querySelectorAll('#datasetFiles input[data-dataset-path]')) {
          if (other !== input && datasetFileByPath(other.dataset.datasetPath)?.role === 'gene_annotation') other.checked = false;
        }
      }
      enforceDatasetSelectionForFigure(input, false);
    });
    const name = document.createElement('span'); name.className = 'dataset-file-name'; name.textContent = file.name;
    label.append(input, name); row.appendChild(label);
    const resolutionMeta = file.role === 'hic' && Array.isArray(file.resolutions) && file.resolutions.length
      ? ` · ${file.resolutions.map(value => formatBp(Number(value))).join('/')}`
      : '';
    const controls = document.createElement('div'); controls.className = 'dataset-file-controls';
    const allowedRoles = compatibleRoles;
    const roleSelect = document.createElement('select'); roleSelect.className = 'dataset-role-select'; roleSelect.title = t('detectedRole'); roleSelect.setAttribute('aria-label', `${t('detectedRole')} · ${file.name}`);
    if (!allowedRoles.length) {
      const option = document.createElement('option'); option.value = 'unknown'; option.textContent = state.language === 'en' ? 'Unrecognized' : '未识别'; roleSelect.appendChild(option); roleSelect.disabled = true;
    } else {
      for (const role of allowedRoles) {
        const option = document.createElement('option'); option.value = role; option.textContent = datasetRoleLabel(role); roleSelect.appendChild(option);
      }
      roleSelect.value = allowedRoles.includes(file.role) ? file.role : allowedRoles[0];
      roleSelect.disabled = allowedRoles.length < 2;
    }
    const sampleInput = document.createElement('input'); sampleInput.type = 'text'; sampleInput.className = 'dataset-sample-input'; sampleInput.maxLength = 120; sampleInput.placeholder = t('sampleName'); sampleInput.value = file.sample || file.auto_sample || ''; sampleInput.disabled = file.role === 'unknown'; sampleInput.setAttribute('aria-label', `${t('sampleName')} · ${file.name}`);
    controls.append(sampleInput, roleSelect); row.appendChild(controls);
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
      renderReferenceBuilds();
      updateWorkflowReadiness();
      renderDatasetSourceShelf();
    });
    sampleInput.addEventListener('change', () => {
      file.sample = sampleInput.value.trim() || file.auto_sample || null;
      state.fileOverrides[file.path] = {...(state.fileOverrides[file.path] || {}), sample:file.sample || ''};
      updateMeta();
      saveActiveDatasetSourceView();
    });
    fileList.appendChild(row);
  }
  target.appendChild(picker);
  enforceDatasetSelectionForFigure(null, fillMinimum || selectedKeys === null);
}
async function scanDatasetPath(path, options={}) {
  saveActiveDatasetSourceView();
  const previousSource = activeDatasetSource();
  const inputKey = datasetPathKey(path);
  const existing = state.datasetSources.find(source => source.key === inputKey || datasetPathKey(source.path) === inputKey) || null;
  const gene = Object.prototype.hasOwnProperty.call(options, 'gene') ? options.gene : datasetQuery();
  const geneInput = options.geneInput ?? $('datasetGene').value.trim();
  const regionEdited = options.regionEdited ?? (datasetRegionMode() === 'manual');
  const referenceBuild = options.referenceBuild || selectedReferenceBuild();
  const overrides = cloneFileOverrides(existing?.fileOverrides || {});
  state.fileOverrides = cloneFileOverrides(overrides);
  state.pendingDatasetPath = path;
  state.pendingDatasetOptions = {gene, geneInput, regionEdited, referenceBuild, refresh:Boolean(options.refresh)};
  const results = $('datasetResults');
  const importButton = $('dataForm').querySelector('button[type="submit"]');
  importButton.disabled = true;
  results.hidden = false;
  results.classList.remove('error');
  hideDatasetAuthorization();
  hideFigureReadinessHint();
  $('datasetSummary').textContent = state.language === 'en' ? 'Scanning the directory and identifying file types…' : '正在扫描目录并识别数据类型……';
  $('datasetFiles').replaceChildren();
  $('datasetResolution').hidden = true;
  $('buildDataset').hidden = true;
  $('addTracksDataset').hidden = true;
  $('datasetResults').querySelectorAll('.dataset-notices').forEach(node => node.remove());
  $('buildDataset').disabled = true;
  try {
    const scan = await api('/api/datasets/scan', {
      method:'POST', body:JSON.stringify({path, gene, reference_build:referenceBuild, file_overrides:overrides})
    });
    const canonicalKey = datasetPathKey(scan.root || path);
    const saved = state.datasetSources.find(source => source.key === canonicalKey) || existing;
    const refreshed = Boolean(saved || options.refresh);
    const source = saved || {};
    const previousIndex = saved ? state.datasetSources.indexOf(saved) : state.datasetSources.length;
    Object.assign(source, {
      key:canonicalKey,
      path,
      gene,
      geneInput,
      regionEdited:Boolean(regionEdited),
      referenceBuild,
      scan,
      included:saved?.included ?? true,
      fileOverrides:cloneFileOverrides(overrides),
      selectedPaths:saved?.selectedPaths ?? null,
      resolution:saved?.resolution || 'auto',
      figureType:saved?.figureType || $('figureTypeSelect').value,
    });
    state.datasetSources = state.datasetSources.filter(item => item !== source && item.key !== canonicalKey);
    state.datasetSources.splice(Math.min(previousIndex, state.datasetSources.length), 0, source);
    state.pendingDatasetPath = null;
    state.pendingDatasetOptions = null;
    displayDatasetSource(source, false, !refreshed);
    updateDatasetRegionControl();
    requestAnimationFrame(() => $('datasetSourceShelf')?.scrollIntoView({block:'nearest'}));
    if (!state.activeJob) setStatus(refreshed ? t('sourceRefreshed', datasetSourceLabel(source)) : t('sourceImported', datasetSourceLabel(source)), 'success');
  } catch(error) {
    const needsAuthorization = error.message.includes('已授权目录') || /authoriz/i.test(error.message);
    if (previousSource && state.datasetSources.includes(previousSource)) {
      displayDatasetSource(previousSource, false);
      $('dataPath').value = path;
      if (needsAuthorization) {
        showDatasetAuthorization(path, error.message);
      } else {
        const note = document.createElement('div');
        note.className = 'dataset-import-error';
        note.textContent = state.language === 'en' ? `Could not import “${path}”: ${error.message}` : `未能导入“${path}”：${error.message}`;
        $('datasetResults').querySelector('.dataset-results-body')?.prepend(note);
      }
    } else {
      results.classList.add('error');
      $('datasetSummary').textContent = needsAuthorization ? t('authorizationTitle') : error.message;
      $('datasetFiles').textContent = state.language === 'en'
        ? 'Check that the directory is authorized and contains at least one .cool or .mcool file.'
        : '请确认目录已授权，并包含至少一个 .cool 或 .mcool 文件。';
      $('buildDataset').hidden = true;
      $('addTracksDataset').hidden = true;
      $('uploadDataset').hidden = true;
      if (needsAuthorization) showDatasetAuthorization(path, error.message);
    }
    if (!state.activeJob) setStatus(state.language === 'en' ? 'Data source import failed' : '数据源导入失败', 'failed');
  } finally {
    importButton.disabled = false;
  }
}
$('authorizeDataset').addEventListener('click', async () => {
  const path = state.pendingDatasetPath || $('dataPath').value.trim();
  const options = state.pendingDatasetOptions || {};
  const button = $('authorizeDataset');
  try {
    button.disabled = true;
    button.textContent = t('authorizing');
    setStatus(t('authorizing'), 'checking');
    await api('/api/datasets/authorize', {method:'POST', body:JSON.stringify({path})});
    await scanDatasetPath(path, options);
  } catch(error) {
    showDatasetAuthorization(path, error.message, true);
    if (!state.activeJob) setStatus(state.language === 'en' ? 'Directory authorization failed' : '目录授权失败', 'failed');
  } finally {
    button.disabled = false;
    button.textContent = t('authorizeRetry');
  }
});
$('dismissDatasetAuthorization').addEventListener('click', () => {
  hideDatasetAuthorization();
  if (!state.activeJob) setStatus(state.language === 'en' ? 'Existing data sources remain available' : '已保留当前数据源', 'success');
});
$('figureReadinessAction').addEventListener('click', () => {
  const action = $('figureReadinessAction');
  if (action.dataset.mode === 'upload') {
    $('datasetUploadInput').click();
    return;
  }
  if (action.dataset.mode === 'resolution') {
    $('datasetResults').open = true;
    $('datasetResolution').scrollIntoView({behavior:'smooth', block:'center'});
    $('datasetResolutionSelect').focus({preventScroll:true});
    return;
  }
  const roles = new Set(String(action.dataset.roles || '').split(',').filter(Boolean));
  const inputs = [...document.querySelectorAll('#datasetFiles input[data-dataset-path]')].filter(input => {
    const file = datasetFileByPath(input.dataset.datasetPath);
    return file && (!roles.size || roles.has(file.role)) && !input.disabled;
  });
  const target = action.dataset.issue === 'too_many_role'
    ? inputs.find(input => input.checked)
    : inputs.find(input => !input.checked) || inputs[0];
  const row = target?.closest('.dataset-file') || $('datasetFiles');
  const fileSection = $('datasetFiles').querySelector('.dataset-files-section');
  $('datasetResults').open = true;
  if (fileSection) fileSection.open = true;
  requestAnimationFrame(() => {
    row.scrollIntoView({behavior:'smooth', block:'center'});
    if (target) target.focus({preventScroll:true});
    if (row.classList?.contains('dataset-file')) {
      row.classList.remove('requirement-focus');
      void row.offsetWidth;
      row.classList.add('requirement-focus');
      setTimeout(() => row.classList.remove('requirement-focus'), 1800);
    }
  });
});
$('uploadDataset').addEventListener('click', () => $('datasetUploadInput').click());
$('datasetUploadInput').addEventListener('change', async event => {
  const input = event.currentTarget;
  const files = [...(input.files || [])];
  const scanned = activeDatasetSource() || includedDatasetSources()[0] || null;
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
    const uploadedAnnotationNames = new Set(
      files.filter(file => /\.(?:gtf|gff|gff3)$/i.test(file.name)).map(file => file.name.toLowerCase())
    );
    const uploadedAnnotation = userReferenceAnnotations().find(file => uploadedAnnotationNames.has(String(file.name || '').toLowerCase()));
    if (uploadedAnnotation) {
      renderReferenceBuilds(uploadedAnnotation.path);
      applyReferenceAnnotationChoice(uploadedAnnotation.path);
    }
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
  if (!requireValidDatasetRegion()) return;
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
      method:'POST', body:JSON.stringify({session_id:sessionId, path:selected.path, source_paths:selected.sourcePaths || [selected.path], gene:datasetQuery(), reference_build:selectedReferenceBuild(), selected_paths:chosenPaths, figure_type:$('figureTypeSelect').value || 'hic_triangle', window:500000, resolution:selectedResolutionValue(), file_overrides:fileOverridesPayload(), render:true})
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
  if (!selected || !state.sessionId || isDraftSession()) return;
  const newTracks = newSelectedTrackFiles();
  if (!newTracks.length) {
    addMessage('assistant', t('trackAlreadyPresent'), 'data:directory');
    return;
  }
  try {
    $('addTracksDataset').disabled = true;
    setStatus(t('addingTracks'), 'running', state.language === 'en' ? 'Checking chromosome compatibility and passing the tracks to the official CFIZZ renderer…' : '正在检查染色体兼容性，并交给 CFIZZ 官方接口组织轨道……');
    const payload = await api(`/api/sessions/${state.sessionId}/tracks/from-dataset`, {
      method:'POST', body:JSON.stringify({path:selected.path, source_paths:selected.sourcePaths || [selected.path], gene:datasetQuery(), reference_build:selectedReferenceBuild(), selected_paths:selectedDatasetPaths(), file_overrides:fileOverridesPayload()})
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
  event.preventDefault();
  const message = $('chatInput').value.trim();
  if (!message) return;
  try {
    await ensureChatSession();
    $('chatInput').value=''; addMessage('user',message);
    let payload=await api(`/api/sessions/${state.sessionId}/chat`, {method:'POST',body:JSON.stringify({message,provider:state.provider})});
    if(payload.intent.requires_confirmation && !payload.job) {
      const accepted=window.confirm(`${payload.intent.reply}\n\n这是科学参数修改，是否继续？`);
      if(accepted) payload=await api(`/api/sessions/${state.sessionId}/chat`, {method:'POST',body:JSON.stringify({message,provider:state.provider,confirm_scientific_change:true})});
      else { addMessage('assistant','已取消修改。'); return; }
    }
    addMessage('assistant',payload.intent.reply,payload.intent.planner); updateSession(payload); watchJob(payload.job);
  } catch(error) { addMessage('assistant',error.message, 'system:chat'); }
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
$('figureZoom').addEventListener('input', event => setFigureZoom(event.target.value));
$('fitFigure').addEventListener('click', () => setFigureZoom(100));
$('figureImage').addEventListener('load', scheduleFigureZoom);
window.addEventListener('resize', scheduleFigureZoom);
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
    closeDialog('apiDialog');
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
$('datasetRegionMode').addEventListener('change', () => {
  updateDatasetRegionControl();
  if (datasetRegionMode() === 'manual') $('datasetGene').focus();
  if (state.datasetScan) updateDatasetSelectionSummary();
});
$('datasetGene').addEventListener('input', () => {
  state.regionEdited = datasetRegionMode() === 'manual';
  updateDatasetRegionControl();
  if (state.datasetScan) updateDatasetSelectionSummary();
});
$('referenceBuild').addEventListener('change', () => {
  applyReferenceAnnotationChoice(selectedReferenceAnnotationPath());
  scheduleDatasetRegionPreview(100);
});
$('applyFigureType').addEventListener('click', async () => {
  const figureType = $('figureTypeSelect').value;
  const item = state.figureTypes.find(value => value.id === figureType);
  if ((!state.sessionId || isDraftSession()) && state.datasetScan) {
    if (!requireValidDatasetRegion()) return;
    try {
      setStatus(state.language === 'en' ? 'Building selected figure' : '正在生成所选图形', 'running', t('preparing'));
      const sessionId = `dataset_${Date.now()}`;
      const payload = await api('/api/sessions/from-dataset', {
        method:'POST', body:JSON.stringify({session_id:sessionId, path:state.datasetScan.path, source_paths:state.datasetScan.sourcePaths || [state.datasetScan.path], gene:datasetQuery(), reference_build:selectedReferenceBuild(), selected_paths:selectedDatasetPaths(), figure_type:figureType, window:500000, resolution:selectedResolutionValue(), file_overrides:fileOverridesPayload(), render:true})
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
applyFigureZoom();
checkServerCompatibility();
loadPlannerStatus();
loadReferenceBuilds();
loadFigureTypes();
initializeChatSession();
