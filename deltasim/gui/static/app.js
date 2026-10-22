/**
 * deltasim GUI v3 — M5公開API対応版
 *
 * §12 対応:
 *  - Z/X 区別色（z=青系 #60a5fa, x=橙系 #fb923c）
 *  - truncated_at 警告バナー + VCDを開く導線
 *  - case_done をケース単位表示（individual checks をリスト内に）
 *  - cancelled 時の未実行ケース SKIP 表示
 *
 * §8 対応:
 *  - 対話デバッグパネル（Simulator API / REST POST + SSE不要）
 *  - step / run_until / set / force / release / breakpoint
 *  - in-memory 波形（get_signal_changes）
 */

"use strict";

// =========================================================================
// 状態
// =========================================================================

const state = {
  project: { svFiles: [], specFile: null, top: null },
  module: null,
  testResult: null,
  waveformData: null,
  vcdIndex: null,
  vcdPath: null,
  selectedCase: null,
  selectedSignals: [],
  signalRadix: {},        // §18.4 B3: 信号ごとの radix (name → 'bin'|'hex'|'unsigned'|'signed')
  activeSignal: null,     // §18.4 B2: エッジ検索対象の信号名
  radix: "hex",
  zoom: 1.0,
  cursorA: -1,
  cursorB: -1,
  running: false,
  currentJobId: null,
  currentExportJobId: null,
  genResult: null,        // §18.4 A: 最後の生成結果（保存用）
  editorPath: null,
  editorLang: "text",
  errorLines: [],
  browserPath: null,
  browserSelected: null,
  sigPickerSelected: new Set(),  // 信号ピッカーで選択中
  // デバッグパネル
  debugSession: null,
  debugWaveData: null,
};

// =========================================================================
// DOM refs
// =========================================================================

const $ = id => document.getElementById(id);

const dom = {
  btnBrowse: $("btn-browse"),
  btnLoadExamples: $("btn-load-examples"),
  btnGen: $("btn-gen"),
  btnRunTest: $("btn-run-test"),
  btnRunSim: $("btn-run-sim"),
  simCyclesInput: $("sim-cycles-input"),
  btnStop: $("btn-stop"),
  btnSaveProject: $("btn-save-project"),
  btnDebug: $("btn-debug"),
  // 波形ツールバー
  edgeTypeSelect: $("edge-type-select"),
  btnEdgePrev: $("btn-edge-prev"),
  btnEdgeNext: $("btn-edge-next"),
  btnWaveAddSig: $("btn-wave-add-sig"),
  btnWavesetSave: $("btn-waveset-save"),
  btnWavesetLoad: $("btn-waveset-load"),
  // 信号追加ピッカー
  sigPickerOverlay: $("sig-picker-overlay"),
  sigPickerSearch: $("sig-picker-search"),
  sigPickerList: $("sig-picker-list"),
  btnSigPickerClose: $("btn-sig-picker-close"),
  btnSigPickerCancel: $("btn-sig-picker-cancel"),
  btnSigPickerAdd: $("btn-sig-picker-add"),
  // 波形セット保存ダイアログ
  wavesetSaveOverlay: $("waveset-save-overlay"),
  wavesetNameInput: $("waveset-name-input"),
  wavesetSaveInfo: $("waveset-save-info"),
  btnWavesetSaveClose: $("btn-waveset-save-close"),
  btnWavesetSaveCancel: $("btn-waveset-save-cancel"),
  btnWavesetSaveOk: $("btn-waveset-save-ok"),
  // 生成保存ダイアログ
  genSaveOverlay: $("gen-save-overlay"),
  genSaveDir: $("gen-save-dir"),
  genSaveTb: $("gen-save-tb"),
  genSaveSpec: $("gen-save-spec"),
  genSavePreview: $("gen-save-preview"),
  btnGenSaveClose: $("btn-gen-save-close"),
  btnGenSaveCancel: $("btn-gen-save-cancel"),
  btnGenSaveOk: $("btn-gen-save-ok"),
  btnZoomIn: $("btn-zoom-in"),
  btnZoomOut: $("btn-zoom-out"),
  btnZoomFit: $("btn-zoom-fit"),
  btnSaveFile: $("btn-save-file"),
  btnClearLog: $("btn-clear-log"),
  seedInput: $("seed-input"),
  caseFilterInput: $("case-filter-input"),
  btnCaseList: $("btn-case-list"),
  caseDropdown: $("case-dropdown"),
  statusBarTop: $("status-bar-top"),
  progressBar: $("progress-bar"),
  fileList: $("file-list"),
  specList: $("spec-list"),
  moduleSelect: $("module-select"),
  moduleHint: $("module-hint"),
  exampleList: $("example-list"),
  recentList: $("recent-list"),
  sectionRecent: $("section-recent"),
  editorFilename: $("editor-filename"),
  editorErrorsBadge: $("editor-errors-badge"),
  signalNames: $("signal-names"),
  waveformCanvas: $("waveform-canvas"),
  waveformWrapper: $("waveform-canvas-wrapper"),
  waveSigCount: $("wave-sig-count"),
  summaryBar: $("summary-bar"),
  resultList: $("result-list"),
  caseDetailPanel: $("case-detail-panel"),
  truncatedBanner: $("truncated-banner"),
  truncatedMsg: $("truncated-msg"),
  btnOpenVcd: $("btn-open-vcd"),
  exportButtons: $("export-buttons"),
  btnExportJson: $("btn-export-json"),
  btnExportJunit: $("btn-export-junit"),
  logOutput: $("log-output"),
  statusbar: $("statusbar"),
  statusText: $("status-text"),
  dropOverlay: $("drop-overlay"),
  fileBrowserOverlay: $("file-browser-overlay"),
  browserBreadcrumb: $("browser-breadcrumb"),
  browserBody: $("browser-body"),
  browserSelectedPath: $("browser-selected-path"),
  btnBrowserClose: $("btn-browser-close"),
  btnBrowserCancel: $("btn-browser-cancel"),
  btnBrowserOpen: $("btn-browser-open"),
  // Debug panel
  debugOverlay: $("debug-overlay"),
  btnDebugClose: $("btn-debug-close"),
  debugTime: $("debug-time"),
  btnDbgStep: $("btn-dbg-step"),
  dbgStepN: $("dbg-step-n"),
  btnDbgRunUntil: $("btn-dbg-run-until"),
  dbgUntilNs: $("dbg-until-ns"),
  btnDbgReset: $("btn-dbg-reset"),
  debugSignalsList: $("debug-signals-list"),
  dbgSigName: $("dbg-sig-name"),
  dbgSigVal: $("dbg-sig-val"),
  btnDbgForce: $("btn-dbg-force"),
  btnDbgSet: $("btn-dbg-set"),
  btnDbgRelease: $("btn-dbg-release"),
  dbgBpExpr: $("dbg-bp-expr"),
  btnDbgBpAdd: $("btn-dbg-bp-add"),
  debugBps: $("debug-bps"),
  debugWaveformCanvas: $("debug-waveform-canvas"),
  debugLog: $("debug-log"),
};

// =========================================================================
// CodeMirror エディタ
// =========================================================================

let cmEditor = null;

function initEditor() {
  const ta = $("editor-textarea");
  cmEditor = CodeMirror.fromTextArea(ta, {
    theme: "dracula",
    lineNumbers: true,
    mode: "text/x-systemverilog",
    indentUnit: 2, tabSize: 2,
    lineWrapping: false,
    gutters: ["CodeMirror-linenumbers", "error-gutter"],
    extraKeys: { "Ctrl-S": saveFile, "Cmd-S": saveFile },
  });
  cmEditor.setSize("100%", "100%");
}

function setEditorMode(lang) {
  if (!cmEditor) return;
  const mode = lang === "verilog" ? "text/x-systemverilog"
             : lang === "yaml"    ? "text/x-yaml"
             : "text/plain";
  cmEditor.setOption("mode", mode);
  state.editorLang = lang;
}

function setEditorContent(content, lang) {
  if (!cmEditor) return;
  setEditorMode(lang);
  cmEditor.setValue(content);
  cmEditor.clearHistory();
  clearEditorErrors();
}

function setEditorErrors(errors) {
  if (!cmEditor) return;
  clearEditorErrors();
  state.errorLines = errors.filter(e => e.line > 0).map(e => e.line);
  for (const ln of state.errorLines) {
    const idx = ln - 1;
    cmEditor.setGutterMarker(idx, "error-gutter", makeErrorMarker(errors.find(e => e.line === ln)));
    cmEditor.addLineClass(idx, "background", "cm-error-line");
  }
  if (state.errorLines.length > 0) {
    dom.editorErrorsBadge.style.display = "";
    dom.editorErrorsBadge.textContent = `⚠ ${state.errorLines.length} エラー行`;
  }
}

function clearEditorErrors() {
  if (!cmEditor) return;
  for (const ln of state.errorLines) {
    cmEditor.setGutterMarker(ln - 1, "error-gutter", null);
    cmEditor.removeLineClass(ln - 1, "background", "cm-error-line");
  }
  state.errorLines = [];
  dom.editorErrorsBadge.style.display = "none";
}

function makeErrorMarker(err) {
  const el = document.createElement("div");
  el.className = "cm-error-gutter";
  el.textContent = "●";
  el.title = err ? `[${err.code || err.severity}] ${err.message}` : "エラー行";
  return el;
}

// =========================================================================
// API ヘルパー
// =========================================================================

async function apiFetch(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch (_) {}
    throw new Error(typeof detail === "object" ? JSON.stringify(detail) : detail);
  }
  return res.json();
}

const api = {
  examples:     ()                  => apiFetch("GET", "/api/examples"),
  load:         (files, top)        => apiFetch("POST", "/api/load", { files, top }),
  browseRoots:  ()                  => apiFetch("GET", "/api/browse/roots"),
  browse:       (path)              => apiFetch("GET", `/api/browse?path=${encodeURIComponent(path)}`),
  fileRead:     (path)              => apiFetch("GET", `/api/file/read?path=${encodeURIComponent(path)}`),
  fileWrite:    (path, content)     => apiFetch("POST", "/api/file/write", { path, content }),
  gen:          (sv, top)           => apiFetch("POST", "/api/gen", { sv_files: sv, top: top ?? null }),
  genSave:      (sv, top, outDir, tbPath, specPath, force) => apiFetch("POST", "/api/gen", {
    sv_files: sv, top: top ?? null, out_dir: outDir ?? null,
    tb_path: tbPath ?? null, spec_path: specPath ?? null, force: !!force,
  }),
  waveEdge:     (path, signal, fromNs, dir, edge) => {
    const p = new URLSearchParams({ path, signal, from_ns: fromNs, direction: dir, edge });
    return apiFetch("GET", `/api/waveform/edge?${p}`);
  },
  wavesetGet:   (svFiles, spec)     => {
    const p = new URLSearchParams({ sv_files: svFiles.join(",") });
    if (spec) p.append("spec", spec);
    return apiFetch("GET", `/api/waveset?${p}`);
  },
  wavesetSave:  (svFiles, spec, name, signals, radix) =>
    apiFetch("POST", "/api/waveset", { sv_files: svFiles, spec: spec ?? null, name, signals, radix }),
  wavesetDel:   (svFiles, spec, name) =>
    apiFetch("DELETE", "/api/waveset", { sv_files: svFiles, spec: spec ?? null, name }),
  testStart:    (sv, spec, top, seed, filterCases) => apiFetch("POST", "/api/test/start", {
    sv_files: sv, spec, top: top ?? null,
    seed: seed !== "" && seed != null ? parseInt(seed) : null,
    filter_cases: filterCases?.length ? filterCases : null,
  }),
  specCases:    (path)              => apiFetch("GET", `/api/spec/cases?path=${encodeURIComponent(path)}`),
  testCancel:   (jid)               => apiFetch("POST", `/api/test/${jid}/cancel`, {}),
  testResult:   (jid)               => apiFetch("GET",  `/api/test/${jid}/result`),
  testExport:   (jid, fmt)          => `/api/test/${jid}/export?fmt=${encodeURIComponent(fmt)}`,
  waveIndex:    (path)              => apiFetch("GET", `/api/waveform/index?path=${encodeURIComponent(path)}`),
  waveRange:    (path, sigs, t0, t1) => {
    const p = new URLSearchParams({ path, t_start: t0 ?? 0 });
    if (t1 != null) p.append("t_end", t1);
    if (sigs?.length) p.append("signals", sigs.join(","));
    return apiFetch("GET", `/api/waveform/range?${p}`);
  },
  projectSave:  (path, data)        => apiFetch("POST", "/api/project", { path, ...data }),
  recentProjects: ()                => apiFetch("GET", "/api/recent-projects"),
  recentAdd:    (entry)             => apiFetch("POST", "/api/recent-projects/add", entry),
  runStart:     (sv, top, cycles)   => apiFetch("POST", "/api/run/start", {
    sv_files: sv, top: top ?? null, cycles: cycles ?? 100,
  }),
  runResult:    (jid)               => apiFetch("GET",  `/api/run/${jid}/result`).catch(() => null),
  // Simulator API (§8)
  simCreate:    (sv, top, params, hp, rc) => apiFetch("POST", "/api/simulator", {
    sv_files: sv, top: top ?? null, params: params ?? {}, half_period: hp ?? 5, reset_cycles: rc ?? 10,
  }),
  simList:      ()                  => apiFetch("GET", "/api/simulator"),
  simDelete:    (sid)               => apiFetch("DELETE", `/api/simulator/${sid}`),
  simStep:      (sid, n)            => apiFetch("POST", `/api/simulator/${sid}/step`, { n }),
  simRunUntil:  (sid, ns)           => apiFetch("POST", `/api/simulator/${sid}/run_until`, { time_ns: ns }),
  simSignals:   (sid)               => apiFetch("GET", `/api/simulator/${sid}/signals`),
  simSet:       (sid, sig, val)     => apiFetch("POST", `/api/simulator/${sid}/set`, { signal: sig, value: val }),
  simForce:     (sid, sig, val)     => apiFetch("POST", `/api/simulator/${sid}/force`, { signal: sig, value: val }),
  simRelease:   (sid, sig)          => apiFetch("POST", `/api/simulator/${sid}/release`, { signal: sig }),
  simReset:     (sid)               => apiFetch("POST", `/api/simulator/${sid}/reset`, {}),
  simBpAdd:     (sid, cond)         => apiFetch("POST", `/api/simulator/${sid}/breakpoints`, { condition: cond }),
  simBpList:    (sid)               => apiFetch("GET", `/api/simulator/${sid}/breakpoints`),
  simBpDel:     (sid, id)           => apiFetch("DELETE", `/api/simulator/${sid}/breakpoints/${id}`),
  simWaveform:  (sid, sigs, t0, t1) => {
    const p = new URLSearchParams({ t_start: t0 ?? 0 });
    if (t1 != null) p.append("t_end", t1);
    if (sigs?.length) p.append("signals", sigs.join(","));
    return apiFetch("GET", `/api/simulator/${sid}/waveform?${p}`);
  },
};

// =========================================================================
// ログ
// =========================================================================

function log(msg, cls = "log-line") {
  const el = document.createElement("div");
  el.className = cls;
  el.textContent = msg;
  dom.logOutput.appendChild(el);
  dom.logOutput.scrollTop = dom.logOutput.scrollHeight;
}
function logClear() { dom.logOutput.innerHTML = ""; }

function dbgLog(msg, cls = "dbg-log-line") {
  const el = document.createElement("div");
  el.className = cls;
  el.textContent = msg;
  dom.debugLog.appendChild(el);
  dom.debugLog.scrollTop = dom.debugLog.scrollHeight;
}

// =========================================================================
// ステータスバー / 進捗
// =========================================================================

function setStatus(text, cls = "idle") {
  dom.statusbar.className = cls;
  dom.statusText.textContent = text;
}
function setProgress(pct) {
  dom.progressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
}

// =========================================================================
// サイドバー描画
// =========================================================================

function renderFileList() {
  dom.fileList.innerHTML = "";
  state.project.svFiles.forEach(f => {
    const name = f.path.split(/[\\/]/).pop();
    const li = document.createElement("li");
    li.className = "file-item";
    li.innerHTML = `<span>📄</span><span>${name}</span>`;
    li.title = f.path;
    li.addEventListener("click", () => openFileInEditor(f.path));
    dom.fileList.appendChild(li);
  });
  dom.specList.innerHTML = "";
  if (state.project.specFile) {
    const name = state.project.specFile.path.split(/[\\/]/).pop();
    const li = document.createElement("li");
    li.className = "file-item active";
    li.innerHTML = `<span>📋</span><span>${name}</span>`;
    li.addEventListener("click", () => openFileInEditor(state.project.specFile.path));
    dom.specList.appendChild(li);
  }

  // §18.4: トップ候補を先頭に表示（all_modules があれば使用、なければ module のみ）
  dom.moduleSelect.innerHTML = "";
  if (dom.moduleHint) dom.moduleHint.textContent = "";

  const allMods = state.module?.all_modules ?? [];
  if (allMods.length > 0) {
    const topCands = allMods.filter(m => m.is_top_candidate);
    const others   = allMods.filter(m => !m.is_top_candidate);

    if (topCands.length > 0 && others.length > 0) {
      const grp1 = document.createElement("optgroup");
      grp1.label = "トップ候補";
      topCands.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.name; opt.textContent = m.name;
        grp1.appendChild(opt);
      });
      dom.moduleSelect.appendChild(grp1);
      const grp2 = document.createElement("optgroup");
      grp2.label = "その他のモジュール";
      others.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.name; opt.textContent = m.name;
        grp2.appendChild(opt);
      });
      dom.moduleSelect.appendChild(grp2);
      if (dom.moduleHint) dom.moduleHint.textContent = `トップ候補 ${topCands.length} 件 / 全 ${allMods.length} 件`;
    } else {
      allMods.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.name; opt.textContent = m.name;
        dom.moduleSelect.appendChild(opt);
      });
    }

    // 現在の top または最初のトップ候補に合わせて選択
    const desired = state.project.top || (topCands[0]?.name ?? allMods[0]?.name);
    if (desired) dom.moduleSelect.value = desired;
    state.project.top = dom.moduleSelect.value;
  } else if (state.module?.module) {
    const opt = document.createElement("option");
    opt.value = state.module.module;
    opt.textContent = state.module.module;
    dom.moduleSelect.appendChild(opt);
    state.project.top = state.module.module;
  }

  dom.moduleSelect.onchange = () => {
    state.project.top = dom.moduleSelect.value;
  };
}

// =========================================================================
// ファイルをエディタで開く
// =========================================================================

async function openFileInEditor(path) {
  try {
    const data = await api.fileRead(path);
    state.editorPath = path;
    dom.editorFilename.textContent = path.split(/[\\/]/).pop();
    setEditorContent(data.content, data.lang);
  } catch (e) { log(`ファイル読み込みエラー: ${e.message}`, "log-error"); }
}

// =========================================================================
// ファイル保存
// =========================================================================

async function saveFile() {
  if (!state.editorPath || !cmEditor) return;
  try {
    await api.fileWrite(state.editorPath, cmEditor.getValue());
    log(`保存しました: ${state.editorPath}`, "log-info");
    const ext = state.editorPath.split(".").pop().toLowerCase();
    if ((ext === "sv" || ext === "v") && state.project.svFiles.length > 0) {
      await loadModule(state.project.svFiles.map(f => f.path), state.project.top);
    }
  } catch (e) { log(`保存エラー: ${e.message}`, "log-error"); }
}

// =========================================================================
// 最近使ったプロジェクト（§18.4）
// =========================================================================

async function loadRecentProjects() {
  try {
    const data = await api.recentProjects();
    const items = data.recent ?? [];
    if (!items.length) {
      dom.sectionRecent.style.display = "none";
      return;
    }
    dom.sectionRecent.style.display = "";
    dom.recentList.innerHTML = items.map((item, i) => {
      const name = item.name || item.top || (item.sv_files?.[0]?.split(/[\\/]/).pop() ?? "?");
      const missing = item.missing ? ' style="color:#f87171;"' : "";
      const missingTitle = item.missing ? ` (存在しないパスあり: ${(item.missing_paths ?? []).join(", ")})` : "";
      return `<div class="file-item recent-item" data-idx="${i}" title="${escHtml(name + missingTitle)}"${missing}>
        ${item.missing ? "⚠ " : "📁 "}<span>${escHtml(name)}</span>
      </div>`;
    }).join("");
    dom.recentList.querySelectorAll(".recent-item").forEach(el => {
      el.addEventListener("click", async () => {
        const idx = parseInt(el.dataset.idx);
        const item = items[idx];
        if (!item) return;
        state.project.svFiles = (item.sv_files ?? []).map(p => ({ path: p }));
        state.project.specFile = item.spec ? { path: item.spec } : null;
        state.project.top = item.top ?? null;
        renderFileList();
        if (item.sv_files?.length) {
          await loadModule(item.sv_files, item.top);
          await openFileInEditor(item.sv_files[0]);
        }
        log(`最近のプロジェクトを開きました: ${item.name || item.top || ""}`, "log-info");
      });
    });
  } catch (e) {
    dom.sectionRecent.style.display = "none";
  }
}

async function addCurrentToRecent() {
  const svPaths = state.project.svFiles.map(f => f.path);
  if (!svPaths.length) return;
  const entry = {
    sv_files: svPaths,
    top: state.project.top ?? null,
    spec: state.project.specFile?.path ?? null,
    name: state.project.top ?? (svPaths[0].split(/[\\/]/).pop().replace(/\.(sv|v)$/i, "")),
  };
  try { await api.recentAdd(entry); } catch (_) {}
}

// =========================================================================
// 生成ボタン（§18.4 gen）
// =========================================================================

// =========================================================================
// 生成ボタン（§18.4 A: 生成結果の保存画面）
// =========================================================================

async function runGen() {
  if (!state.project.svFiles.length) {
    log("SV ファイルを選択してください", "log-error");
    return;
  }
  const svPaths = state.project.svFiles.map(f => f.path);
  const top = state.project.top;
  log("⚙ テストベンチ・スペック生成中…", "log-info");
  setStatus("生成中…", "running");
  try {
    const result = await api.gen(svPaths, top);
    if (result.status === "ERROR") {
      log(`生成失敗: ${(result.errors ?? []).map(e => e.message).join(", ")}`, "log-error");
      setStatus("生成失敗", "error");
      return;
    }
    state.genResult = result;
    if (result.spec_yaml) {
      setEditorContent(result.spec_yaml, "yaml");
      log(`✓ スペック YAML を生成しました（${result.module}）`, "log-info");
      if (result.tb_sv) log(`TB SV も生成済み（表示はスペックのみ）`, "log-line");
    } else if (result.spec_path) {
      state.project.specFile = { path: result.spec_path };
      renderFileList();
      await openFileInEditor(result.spec_path);
      log(`✓ 生成完了: ${result.spec_path}`, "log-info");
    }
    setStatus("生成完了", "idle");
    // 保存ダイアログを開く（文字列モードの場合）
    if (result.spec_yaml || result.tb_sv) {
      openGenSaveDialog(result);
    }
  } catch (e) {
    log(`生成エラー: ${e.message}`, "log-error");
    setStatus("生成エラー", "error");
  }
}

function openGenSaveDialog(result) {
  // デフォルト保存先: プロジェクトのフォルダまたはカレント
  const firstSv = state.project.svFiles[0]?.path ?? "";
  const projDir = firstSv ? firstSv.replace(/[\\/][^\\/]+$/, "") : ".";
  if (dom.genSaveDir) dom.genSaveDir.value = projDir;
  if (dom.genSaveTb) dom.genSaveTb.checked = !!result.tb_sv;
  if (dom.genSaveSpec) dom.genSaveSpec.checked = !!result.spec_yaml;
  _updateGenSavePreview();
  if (dom.genSaveOverlay) dom.genSaveOverlay.style.display = "flex";
}

function _updateGenSavePreview() {
  if (!dom.genSavePreview || !state.genResult) return;
  const dir = dom.genSaveDir?.value?.trim() || ".";
  const mod = state.genResult.module || "module";
  const lines = [];
  if (dom.genSaveTb?.checked) lines.push(`${dir}/${mod}_tb.sv`);
  if (dom.genSaveSpec?.checked) lines.push(`${dir}/${mod}_spec.yaml`);
  dom.genSavePreview.textContent = lines.length
    ? "書き出し先:\n" + lines.join("\n")
    : "(保存するファイルを選択してください)";
}

async function execGenSave(force = false) {
  if (!state.genResult || !state.project.svFiles.length) return;
  const dir = dom.genSaveDir?.value?.trim() || ".";
  const mod = state.genResult.module || "module";
  const svPaths = state.project.svFiles.map(f => f.path);
  const top = state.project.top;
  const tbPath = dom.genSaveTb?.checked ? null : "SKIP";   // null = auto, "SKIP" = no-tb
  const specPath = dom.genSaveSpec?.checked ? null : "SKIP";
  const noTb = !dom.genSaveTb?.checked;
  const noSpec = !dom.genSaveSpec?.checked;

  try {
    const res = await api.genSave(
      svPaths, top,
      dir,
      noTb ? `${dir}/__skip_tb__` : null,   // trick: pass null for auto
      noSpec ? `${dir}/__skip_spec__` : null,
      force,
    );

    // よりシンプルに: out_dir だけ指定して force を渡す
    const res2 = await apiFetch("POST", "/api/gen", {
      sv_files: svPaths,
      top: top ?? null,
      out_dir: dir,
      tb_path: noTb ? undefined : null,
      spec_path: noSpec ? undefined : null,
      force: force,
    }).catch(e => { throw e; });

    if (res2.status === "ERROR") {
      log(`保存失敗: ${(res2.errors ?? []).map(e => e.message).join(", ")}`, "log-error");
      return;
    }
    if (dom.genSaveOverlay) dom.genSaveOverlay.style.display = "none";
    log(`✓ 保存完了: ${res2.tb_path ?? ""} ${res2.spec_path ?? ""}`, "log-info");
    if (res2.spec_path) {
      state.project.specFile = { path: res2.spec_path };
      renderFileList();
      await openFileInEditor(res2.spec_path);
      await addCurrentToRecent();
      await loadRecentProjects();
    }
    setStatus("保存完了", "idle");
  } catch (e) {
    log(`保存エラー: ${e.message}`, "log-error");
    setStatus("保存エラー", "error");
  }
}

async function handleGenSaveOk() {
  if (!state.genResult || !state.project.svFiles.length) return;
  const dir = dom.genSaveDir?.value?.trim() || ".";
  const noTb = !dom.genSaveTb?.checked;
  const noSpec = !dom.genSaveSpec?.checked;
  const svPaths = state.project.svFiles.map(f => f.path);
  const top = state.project.top;

  try {
    // まず force=false で試みる
    const res = await apiFetch("POST", "/api/gen", {
      sv_files: svPaths,
      top: top ?? null,
      out_dir: dir,
      force: false,
    });

    if (res.status === "ERROR") {
      log(`保存失敗: ${(res.errors ?? []).map(e => e.message).join(", ")}`, "log-error");
      return;
    }

    _applyGenSaveResult(res);
  } catch (errObj) {
    // 409: ファイルが既に存在する → 確認ダイアログ
    let msg = errObj.message ?? String(errObj);
    // fetch の場合はレスポンスボディに conflict がある
    if (msg.includes("既に存在") || msg.includes("409") || msg.includes("conflict")) {
      const ok = window.confirm(
        `ファイルが既に存在します。上書きしますか？\n\n${msg.slice(0, 200)}`
      );
      if (!ok) return;
      try {
        const res2 = await apiFetch("POST", "/api/gen", {
          sv_files: svPaths,
          top: top ?? null,
          out_dir: dir,
          force: true,
        });
        _applyGenSaveResult(res2);
      } catch (e2) {
        log(`上書き保存失敗: ${e2.message}`, "log-error");
      }
    } else {
      log(`保存エラー: ${msg}`, "log-error");
      setStatus("保存エラー", "error");
    }
  }
}

function _applyGenSaveResult(res) {
  if (dom.genSaveOverlay) dom.genSaveOverlay.style.display = "none";
  log(`✓ 保存完了: ${[res.tb_path, res.spec_path].filter(Boolean).join(", ")}`, "log-info");
  if (res.spec_path) {
    state.project.specFile = { path: res.spec_path };
    renderFileList();
    openFileInEditor(res.spec_path);
    addCurrentToRecent().then(() => loadRecentProjects());
  }
  setStatus("保存完了", "idle");
}

// =========================================================================
// シミュ実行ボタン（§18.4 run with end time）
// =========================================================================

async function runSim() {
  if (state.running) return;
  if (!state.project.svFiles.length) {
    log("SV ファイルを選択してください", "log-error");
    return;
  }
  state.running = true;
  dom.btnStop.disabled = false;
  const svPaths = state.project.svFiles.map(f => f.path);
  const top = state.project.top;
  const cycles = parseInt(dom.simCyclesInput?.value || "100", 10) || 100;
  log(`▶ シミュ実行: top=${top ?? "auto"} cycles=${cycles}`, "log-info");
  setStatus("シミュ実行中…", "running");
  setProgress(5);
  clearWaveform();

  try {
    const { job_id } = await api.runStart(svPaths, top, cycles);
    state.currentJobId = job_id;
    await consumeSSE(`/api/run/${job_id}/events`);
    // /api/run/{id}/result は未実装のため /api/run/{id}/events の finish イベントに依存
    // finish イベントで result が来る (run_sim_job が queue に積む)
    setStatus("シミュ完了", "idle");
    setProgress(100);
  } catch (e) {
    log(`シミュエラー: ${e.message}`, "log-error");
    setStatus("シミュエラー", "error");
  } finally {
    state.running = false;
    state.currentJobId = null;
    dom.btnStop.disabled = true;
  }
}

// =========================================================================
// エクスポートボタン（§18.4 JUnit/JSON ダウンロード）
// =========================================================================

function exportResult(fmt) {
  if (!state.currentExportJobId) {
    log("エクスポートするテスト結果がありません", "log-error");
    return;
  }
  const url = api.testExport(state.currentExportJobId, fmt);
  const a = document.createElement("a");
  a.href = url;
  a.download = `result_${state.currentExportJobId.slice(0, 8)}.${fmt === "junit" ? "xml" : "json"}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// =========================================================================
// モジュールロード
// =========================================================================

async function loadModule(svPaths, top) {
  try {
    const data = await api.load(svPaths, top);
    state.module = data;

    // §18.5: E_AMBIGUOUS_TOP のとき candidates でドロップダウンを更新
    const ambig = (data.errors || []).find(e => e.code === "E_AMBIGUOUS_TOP");
    if (ambig) {
      const cands = ambig.candidates || [];
      log(`E_AMBIGUOUS_TOP: ${ambig.message}`, "log-warn");
      if (cands.length > 0) {
        log(`  トップ候補: ${cands.join(", ")} - サイドバーのドロップダウンから選択してください`, "log-warn");
        if (!(data.all_modules && data.all_modules.length)) {
          data.all_modules = cands.map(function(n) { return { name: n, is_top_candidate: true }; });
        }
      }
    } else if (data.module) {
      log(`ロード成功: ${data.module} (${data.ports.length} ポート)`, "log-info");
    }

    // §18.5: 構造化エラーをエディタに反映（各コードに対してわかりやすいメッセージ）
    if (data.errors && data.errors.length > 0) {
      setEditorErrors(data.errors.filter(function(e) { return e.severity === "error" || e.severity === "warning"; }));
      data.errors.forEach(function(e) {
        var prefixMap = {
          "E_AMBIGUOUS_TOP": "トップモジュールが不明",
          "E_UNKNOWN_PORT":  "不明なポート名",
          "E_NO_CHECK":      "チェックなし (E_NO_CHECK)",
          "E002":            "トップモジュールが見つかりません",
        };
        var prefix = prefixMap[e.code] || ("[" + e.severity + "]");
        var locSuffix = e.line ? " (行 " + e.line + ")" : "";
        log(prefix + ": " + e.message + locSuffix,
            e.severity === "error" ? "log-error" : "log-warn");
      });
    } else {
      clearEditorErrors();
    }
    renderFileList();
    var statusLabel = ambig
      ? "top 未確定 (" + (data.all_modules ? data.all_modules.length : 0) + " 候補)"
      : ((data.module || "?") + " ロード済み");
    setStatus(statusLabel, "idle");
  } catch (e) {
    log("ロードエラー: " + e.message, "log-error");
    setStatus("ロードエラー", "error");
  }
}

// =========================================================================
// サンプル読み込み
// =========================================================================

async function loadExamplesList() {
  try {
    const data = await api.examples();
    dom.exampleList.innerHTML = "";
    data.examples.forEach(ex => {
      const btn = document.createElement("button");
      btn.className = "example-btn";
      btn.textContent = ex.name;
      btn.addEventListener("click", () => loadExample(ex));
      dom.exampleList.appendChild(btn);
    });
    if (!data.examples.length) dom.exampleList.textContent = "(サンプルなし)";
  } catch (e) { log(`サンプル取得エラー: ${e.message}`, "log-error"); }
}

async function loadExample(ex) {
  logClear();
  log(`サンプル読み込み: ${ex.name}`, "log-info");
  state.project.svFiles = ex.sv_files.map(p => ({ path: p }));
  state.project.specFile = { path: ex.spec };
  state.project.top = ex.top;
  renderFileList();
  await loadModule(ex.sv_files, ex.top);
  if (ex.sv_files.length > 0) await openFileInEditor(ex.sv_files[0]);
  // §18.4.1: プロジェクトを開いたときに最近一覧に追加
  await addCurrentToRecent();
  await loadRecentProjects();
}

// =========================================================================
// テスト実行（SSE）
// =========================================================================

async function runTest() {
  if (state.running) return;
  if (!state.project.svFiles.length || !state.project.specFile) {
    log("SV ファイルとスペックファイルを選択してください", "log-error");
    return;
  }
  state.running = true;
  dom.btnRunTest.disabled = true;
  dom.btnStop.disabled = false;
  logClear();
  setStatus("テスト実行中…", "running");
  setProgress(5);
  log("▶ テスト実行開始 (SSE)", "log-info");
  clearResults();
  clearWaveform();
  hideTruncatedBanner();

  try {
    const svPaths = state.project.svFiles.map(f => f.path);
    const specPath = state.project.specFile.path;
    const seed = dom.seedInput.value.trim();
    const top = state.project.top;
    const filterCases = parseCaseFilter(dom.caseFilterInput.value);

    const { job_id } = await api.testStart(svPaths, specPath, top, seed, filterCases);
    state.currentJobId = job_id;
    await consumeSSE(`/api/test/${job_id}/events`);

    const { done, result } = await api.testResult(job_id);
    if (done && result) {
      state.testResult = result;
      state.vcdPath = result.vcd_path;
      state.currentExportJobId = job_id;  // §18.4: エクスポート用に保存
      renderResults(result);

      // §12.3: truncated_at 警告
      if (result.truncated_at != null) {
        showTruncatedBanner(result.truncated_at, result.vcd_path);
      }

      // §18.4: エクスポートボタンを表示
      if (dom.exportButtons) dom.exportButtons.style.display = "flex";

      const s = result.summary;
      const totalMs = s.elapsed_ms ?? result.elapsed_ms ?? 0;
      const cls = s.fail > 0 ? "fail" : s.error > 0 ? "error" : "pass";
      const msg = `完了: ${s.pass} PASS / ${s.fail} FAIL / ${s.error} ERROR`
        + (s.skip > 0 ? ` / ${s.skip} SKIP` : "")
        + (result.cancelled ? " [キャンセル]" : "")
        + `  ${(totalMs / 1000).toFixed(2)}s`;
      setStatus(msg, cls);
      log(`✓ ${msg}`, cls === "pass" ? "log-pass" : "log-fail");
      setProgress(100);

      if (result.vcd_path) {
        log(`波形読み込み中: ${result.vcd_path}`, "log-info");
        await loadWaveformIndex(result.vcd_path);
      }
      (result.log ?? []).forEach(l => log(l));

      // §18.4: 最近のプロジェクトに追加
      await addCurrentToRecent();
      await loadRecentProjects();
    }
  } catch (e) {
    log(`エラー: ${e.message}`, "log-error");
    setStatus("実行エラー", "error");
  } finally {
    state.running = false;
    state.currentJobId = null;
    dom.btnRunTest.disabled = false;
    dom.btnStop.disabled = true;
  }
}

async function consumeSSE(url) {
  return new Promise((resolve) => {
    const es = new EventSource(url);
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        handleSSEEvent(event);
        if (event.event === "finish" || event.event === "error") {
          es.close(); resolve();
        }
      } catch (_) {}
    };
    es.onerror = () => { es.close(); resolve(); };
  });
}

function handleSSEEvent(event) {
  const evType = event.event || event.type;
  switch (evType) {
    case "start":
      log(`シミュ開始: ${event.spec ?? ""}`, "log-info");
      setProgress(10);
      break;
    case "progress": {
      const total = event.total ?? event.total_cycles ?? 0;
      const pct = total > 0 ? Math.round(10 + 80 * (event.cycle ?? 0) / total) : 50;
      setProgress(pct);
      dom.statusBarTop.textContent =
        `[${event.phase ?? "sim"}] ${event.cycle ?? 0}/${total} cycles  t=${event.time_ns ?? 0}ns`;
      break;
    }
    case "case_done":
      // §12.2: ケース単位 case_done（checks リスト付き）
      log(`ケース完了: ${event.name} → ${event.status}${event.checks?.length ? ` (${event.checks.length} checks)` : ""}`,
          event.status === "PASS" ? "log-pass" : "log-fail");
      break;
    case "finish": {
      setProgress(95);
      // シミュ実行の finish イベントには result が含まれることがある
      const finResult = event.result;
      if (finResult?.vcd_path) {
        log(`波形読み込み中: ${finResult.vcd_path}`, "log-info");
        loadWaveformIndex(finResult.vcd_path).catch(() => {});
      }
      break;
    }
    case "error":
      log(`エラー: ${event.message}`, "log-error");
      break;
  }
}

async function stopTest() {
  if (!state.currentJobId) return;
  try {
    await api.testCancel(state.currentJobId);
    log("キャンセルリクエストを送信しました", "log-warn");
  } catch (e) { log(`キャンセルエラー: ${e.message}`, "log-error"); }
}

// =========================================================================
// §12.3: truncated_at 警告バナー
// =========================================================================

function showTruncatedBanner(truncated_at, vcdPath) {
  dom.truncatedBanner.style.display = "flex";
  dom.truncatedMsg.textContent =
    `⚠ 波形記録が上限に達しました（truncated_at: ${truncated_at}ns）。` +
    `VCD を読み込むと全体を見られます。`;
  dom.btnOpenVcd.style.display = vcdPath ? "" : "none";
  dom.btnOpenVcd.onclick = vcdPath ? () => loadWaveformFromVcd(vcdPath) : null;
}

function hideTruncatedBanner() {
  dom.truncatedBanner.style.display = "none";
}

async function loadWaveformFromVcd(vcdPath) {
  log(`VCD から波形を読み込み中: ${vcdPath}`, "log-info");
  await loadWaveformIndex(vcdPath);
}

// =========================================================================
// 結果表示（§12.1/§12.2 対応）
// =========================================================================

function clearResults() {
  dom.resultList.innerHTML = "";
  dom.summaryBar.innerHTML = "";
  dom.caseDetailPanel.innerHTML = '<div style="color:#8888aa;">ケースを選択すると詳細が表示されます</div>';
  state.selectedCase = null;
  // トップレベル warnings バナーをクリア
  const existing = dom.resultList.parentElement?.querySelector("#top-warnings-banner");
  if (existing) existing.remove();
}

// §15.12/13: トップレベル warnings[] をバナーとして表示
function _renderWarningsTop(warnings) {
  if (!warnings?.length) return "";
  const items = warnings.map(w => {
    const icon = w.code === "W_UNKNOWN_KEY" ? "🔑" : "⚠";
    return `<div class="top-warn-item">
      <span class="warn-icon">${icon}</span>
      <span class="warn-code">[${escHtml(w.code)}]</span>
      <span class="warn-msg">${escHtml(w.message)}</span>
    </div>`;
  }).join("");
  return `<div class="top-warnings-block">${items}</div>`;
}

function renderResults(result) {
  const s = result.summary;
  const totalMs = s.elapsed_ms ?? 0;

  dom.summaryBar.innerHTML = [
    ["pass",  s.pass,  "badge-pass"],
    ["fail",  s.fail,  "badge-fail"],
    ["error", s.error, "badge-error"],
    ["skip",  s.skip,  "badge-skip"],
  ].map(([l, c, cls]) => `<span class="count-badge ${cls}">${c} ${l.toUpperCase()}</span>`).join("")
  + (result.cancelled ? `<span class="count-badge" style="background:#2d2d44;color:#a3a3b4;">キャンセル</span>` : "")
  + (totalMs > 0 ? `<span style="font-size:10px;color:#8888aa;margin-left:auto;">${(totalMs/1000).toFixed(2)}s</span>` : "");

  dom.resultList.innerHTML = "";

  // §13.2: cases[] ベースで表示
  const cases = result.cases ?? [];

  // §15.12/13: トップレベル warnings[] を表示（E_NO_CASE より先に表示）
  if ((result.warnings ?? []).length > 0) {
    const warnHtml = _renderWarningsTop(result.warnings);
    const warnDiv = document.createElement("div");
    warnDiv.id = "top-warnings-banner";
    warnDiv.innerHTML = warnHtml;
    // 結果ヘッダの後ろに追加（既存の追加があれば削除）
    const existing = dom.resultList.parentElement?.querySelector("#top-warnings-banner");
    if (existing) existing.remove();
    dom.resultList.before(warnDiv);
  }

  // §18.5: E_NO_CHECK / E_UNKNOWN_PORT を ERROR として表示
  const noCheckErr = (result.errors ?? []).find(e => e.code === "E_NO_CHECK");
  const unknPortErrors = (result.errors ?? []).filter(e => e.code === "E_UNKNOWN_PORT");
  if (noCheckErr) {
    const banner = document.createElement("div");
    banner.id = "top-warnings-banner";
    banner.style.cssText = "padding:6px 10px;background:rgba(251,146,60,0.1);border-bottom:1px solid #fb923c;font-size:11px;";
    const el = document.createElement("div");
    el.textContent = `E_NO_CHECK: ${noCheckErr.message || "チェックが0件です。スペックにexpectを追加してください。"}`;
    el.style.color = "#fb923c";
    banner.appendChild(el);
    const existing = dom.resultList.parentElement?.querySelector("#top-warnings-banner");
    if (existing) existing.remove();
    dom.resultList.before(banner);
  }
  if (unknPortErrors.length > 0) {
    unknPortErrors.forEach(e => log("E_UNKNOWN_PORT: " + e.message, "log-error"));
  }

  // E_NO_CASE: フィルタに一致するケースがない（API が直接返す）
  if ((cases.length === 0 && result.status === "ERROR"
      && (result.errors ?? []).some(e => e.code === "E_NO_CASE"))) {
    const errMsg = (result.errors ?? []).find(e => e.code === "E_NO_CASE")?.message
      ?? "フィルタに一致するケースがありません";
    dom.resultList.innerHTML = `
      <div style="padding:12px 10px;">
        <div style="color:#fb923c;font-weight:700;margin-bottom:6px;">⚠ E_NO_CASE</div>
        <div style="color:#a3a3b4;font-size:11px;">${escHtml(errMsg)}</div>
        <div style="color:#8888aa;font-size:10px;margin-top:6px;">
          ケースフィルタを変更するか、空欄にして全ケースを実行してください。
        </div>
      </div>`;
    return;
  }

  if (cases.length === 0 && result.status === "ERROR") {
    // パース失敗等: ケースなしでエラー表示
    const errMsg = (result.errors ?? []).map(e => e.message).join("\n") || "エラーが発生しました";
    dom.resultList.innerHTML = `
      <div style="padding:10px 8px;">
        <div class="status-dot dot-ERROR" style="display:inline-block;margin-right:4px;"></div>
        <span style="color:#fb923c;font-size:11px;">ERROR</span>
        <div style="color:#a3a3b4;font-size:10px;margin-top:4px;white-space:pre-wrap;">${escHtml(errMsg)}</div>
      </div>`;
    return;
  }

  cases.forEach((c, i) => {
    const card = _makeCaseCard(c);
    dom.resultList.appendChild(card);
    if (i === 0) selectCase(card, c);
  });
}

function _makeCaseCard(c) {
  // 上段: コンパクトな一覧行（クリックで下段に詳細表示）
  const card = document.createElement("div");
  card.className = "case-card";
  const dur = c.elapsed_ms != null ? `${(c.elapsed_ms / 1000).toFixed(3)}s` : "";
  const nFail = (c.checks ?? []).filter(chk => chk.status !== "PASS" && chk.status !== "SKIP").length;
  const failBadge = nFail > 0 ? `<span style="font-size:10px;color:#f87171;"> ${nFail}×FAIL</span>` : "";

  card.innerHTML = `
    <div class="case-header">
      <div class="status-dot dot-${c.status}"></div>
      <div class="case-name">${escHtml(c.name)}</div>
      ${failBadge}
      <div class="case-meta" style="margin-left:auto;">${dur}</div>
    </div>`;
  card.addEventListener("click", () => selectCase(card, c));
  return card;
}

function selectCase(card, c) {
  document.querySelectorAll(".case-card").forEach(el => el.classList.remove("selected"));
  card.classList.add("selected");
  state.selectedCase = c;
  _renderCaseDetail(c);

  // §18.4 B5: FAIL 選択時に対象信号をハイライト・未表示なら追加・time_ns へジャンプ
  const failChecks = (c.checks ?? []).filter(chk => chk.status !== "PASS" && chk.status !== "SKIP");
  if (failChecks.length > 0 && state.waveformData) {
    // 実際の信号名（<expr> や空欄は除外）
    const failSigs = [...new Set(
      failChecks
        .map(chk => chk.signal)
        .filter(s => s && s !== "<expr>" && !s.startsWith("<"))
    )];
    if (failSigs.length > 0) addSigs(failSigs);

    // カーソル A を最初の失敗の time_ns へ
    const firstFail = failChecks[0];
    state.cursorA = firstFail.time_ns ?? (firstFail.cycle ?? 0) * 10;

    // 失敗信号名を highlighted 表示
    document.querySelectorAll(".sig-name-row").forEach(row => {
      const sigName = row.dataset.sig;
      if (failSigs.includes(sigName)) {
        row.classList.add("sig-fail-highlight");
      } else {
        row.classList.remove("sig-fail-highlight");
      }
    });

    drawWaveform();
  }
}

// ---------------------------------------------------------------------------
// §15.8: checks の時刻を「サイクル4 +3ns（38ns）」形式でフォーマット
// ---------------------------------------------------------------------------

function _fmtCheckTime(chk) {
  const cycle = chk.cycle ?? 0;
  const offset = chk.offset_ns ?? 0;
  const timeNs = chk.time_ns ?? 0;
  if (offset > 0) {
    return `サイクル${cycle} +${offset}ns（${timeNs}ns）`;
  }
  return `サイクル${cycle}（${timeNs}ns）`;
}

// §15.12/13: warnings[] をレンダリング（code で判定・message はそのまま表示）
function _renderWarnings(warnings) {
  if (!warnings?.length) return "";
  return `<div class="detail-section-hdr" style="color:#fbbf24;">⚠ 警告 ${warnings.length} 件</div>`
    + warnings.map(w => {
        // §15.13: code で判定（message は解析せずそのまま表示）
        const icon = w.code === "W_UNKNOWN_KEY" ? "🔑" : "⚠";
        const loc = w.file ? ` (${w.file}${w.line > 0 ? `:${w.line}` : ""})` : "";
        return `<div class="detail-warn-item">
          <span class="warn-icon">${icon}</span>
          <span class="warn-code">${escHtml(w.code)}</span>
          <span class="warn-msg">${escHtml(w.message)}${escHtml(loc)}</span>
        </div>`;
      }).join("");
}

function _renderCaseDetail(c) {
  // 下段: 選択ケースの詳細（checks・message・warnings・log）
  const checks = c.checks ?? [];
  const failures = checks.filter(chk => chk.status !== "PASS" && chk.status !== "SKIP");
  const passed  = checks.filter(chk => chk.status === "PASS");
  const errMsg  = (c.errors ?? []).map(e => e.message).join("\n");

  let html = `<div class="detail-section-hdr">${escHtml(c.name)}`;
  if (c.seed != null) html += ` <span style="font-weight:400;">seed=${c.seed}</span>`;
  if (c.elapsed_ms != null) html += ` <span style="font-weight:400;">${(c.elapsed_ms/1000).toFixed(3)}s</span>`;
  html += `</div>`;

  // §18.4: 「このシードで再実行」ボタン
  if (c.seed != null) {
    html += `<div style="padding:2px 4px;margin-bottom:4px;">
      <button class="btn btn-secondary" style="font-size:10px;padding:2px 8px;"
        onclick="rerunWithSeed(${c.seed})">↺ このシードで再実行 (seed=${c.seed})</button>
    </div>`;
  }

  // message フィールド（あれば表示、なければ省略）
  if (c.message) {
    html += `<div style="padding:2px 4px;color:#a3a3b4;font-size:10px;">${escHtml(c.message)}</div>`;
  }

  if (c.cancelled) {
    html += `<div style="padding:2px 4px;color:#a3a3b4;font-size:10px;">⚠ キャンセルされました</div>`;
  }
  if (c.truncated_at != null) {
    html += `<div style="padding:2px 4px;color:#fb923c;font-size:10px;">⚠ 波形記録 ${c.truncated_at}ns で上限到達</div>`;
  }
  if (errMsg) {
    html += `<div style="padding:2px 4px;color:#fb923c;font-size:10px;">${escHtml(errMsg)}</div>`;
  }

  // §15.12/13: cases[].warnings[] 表示（code で判定・message はそのまま）
  html += _renderWarnings(c.warnings ?? []);

  // 失敗 checks（§15.8: 時刻を「サイクル4 +3ns（38ns）」形式で表示）
  if (failures.length > 0) {
    html += `<div class="detail-section-hdr">失敗 ${failures.length} 件</div>`;
    html += failures.slice(0, 50).map(f => `
      <div class="detail-check-item">
        <div class="detail-check-dot" style="background:#f87171;"></div>
        <div class="detail-check-name">${escHtml(f.signal || f.name)}</div>
        <div class="detail-check-val">
          ${escHtml(_fmtCheckTime(f))}
          期待:<b>${escHtml(String(f.expected))}</b>
          実測:<b>${escHtml(String(f.actual))}</b>
        </div>
      </div>`).join("");
    if (failures.length > 50) html += `<div style="padding:3px 8px;color:#8888aa;font-size:10px;">… 他 ${failures.length-50} 件</div>`;
  }

  // 合格 checks（折りたたみ表示）
  if (passed.length > 0) {
    html += `<div class="detail-section-hdr">合格 ${passed.length} 件</div>`;
    html += passed.slice(0, 10).map(f => `
      <div class="detail-check-item">
        <div class="detail-check-dot" style="background:#4ade80;"></div>
        <div class="detail-check-name">${escHtml(f.signal || f.name)}</div>
        <div class="detail-check-val" style="color:#8888aa;">cycle=${f.cycle}</div>
      </div>`).join("");
    if (passed.length > 10) html += `<div style="padding:3px 8px;color:#8888aa;font-size:10px;">… 他 ${passed.length-10} 件</div>`;
  }

  // per-case ログ
  if ((c.log ?? []).length > 0) {
    html += `<div class="detail-section-hdr">ログ</div>`;
    html += (c.log ?? []).slice(0, 10).map(l =>
      `<div style="padding:1px 4px;font-family:monospace;font-size:10px;color:#a3a3b4;">${escHtml(l)}</div>`
    ).join("");
    if (c.log.length > 10) html += `<div style="padding:2px 8px;color:#8888aa;font-size:10px;">… 他 ${c.log.length-10} 行</div>`;
  }

  dom.caseDetailPanel.innerHTML = html;
}

async function rerunWithSeed(seed) {
  if (!seed && seed !== 0) return;
  dom.seedInput.value = String(seed);
  await runTest();
}

// =========================================================================
// 波形ビューア — 遅延読み込み（index → range）
// =========================================================================

const MAX_DISPLAY_SIGNALS = 200;
const INIT_DISPLAY_SIGNALS = 20;

async function loadWaveformIndex(vcdPath) {
  try {
    const idx = await api.waveIndex(vcdPath);
    state.vcdIndex = idx;
    state.vcdPath = vcdPath;
    const allSigs = Object.keys(idx.signals);
    state.selectedSignals = allSigs.slice(0, INIT_DISPLAY_SIGNALS);
    dom.waveSigCount.textContent = `${allSigs.length} 信号中 ${state.selectedSignals.length} 表示`;
    log(`波形インデックス: ${allSigs.length} 信号`, "log-info");
    await fetchWaveRange();
  } catch (e) { log(`波形インデックスエラー: ${e.message}`, "log-error"); }
}

async function fetchWaveRange() {
  if (!state.vcdPath || !state.selectedSignals.length) return;
  try {
    const data = await api.waveRange(state.vcdPath, state.selectedSignals, 0, null);
    state.waveformData = data;
    renderSignalNames();
    zoomFit();
    log(`波形データ取得: ${state.selectedSignals.length} 信号`, "log-info");
  } catch (e) { log(`波形データエラー: ${e.message}`, "log-error"); }
}

function clearWaveform() {
  state.waveformData = null;
  state.vcdIndex = null;
  state.selectedSignals = [];
  dom.signalNames.innerHTML = "";
  dom.waveSigCount.textContent = "";
  const canvas = dom.waveformCanvas;
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

// §18.4 B3: radix の表示ラベル
const RADIX_LABELS = { bin: "BIN", hex: "HEX", unsigned: "U", signed: "S" };
const RADIX_CYCLE  = ["hex", "bin", "unsigned", "signed"];

function _nextRadix(cur) {
  const idx = RADIX_CYCLE.indexOf(cur);
  return RADIX_CYCLE[(idx + 1) % RADIX_CYCLE.length];
}

function renderSignalNames() {
  dom.signalNames.innerHTML = "";
  state.selectedSignals.forEach((name, idx) => {
    const row = document.createElement("div");
    row.className = "sig-name-row";
    row.dataset.sig = name;

    const radix = state.signalRadix[name] || state.radix;
    const isActive = state.activeSignal === name;

    row.innerHTML = `
      <button class="sig-btn sig-btn-up" title="上へ" ${idx === 0 ? "disabled" : ""}>▴</button>
      <button class="sig-btn sig-btn-dn" title="下へ" ${idx === state.selectedSignals.length - 1 ? "disabled" : ""}>▾</button>
      <span class="sig-name-label ${isActive ? "sig-active" : ""}" title="${escHtml(name)}">${escHtml(name)}</span>
      <button class="sig-btn sig-radix-btn" title="基数切替: ${radix}">${RADIX_LABELS[radix] ?? radix}</button>
      <button class="sig-btn sig-btn-rm" title="削除">✕</button>
    `;

    row.querySelector(".sig-btn-up").addEventListener("click", e => {
      e.stopPropagation();
      _moveSig(name, -1);
    });
    row.querySelector(".sig-btn-dn").addEventListener("click", e => {
      e.stopPropagation();
      _moveSig(name, +1);
    });
    row.querySelector(".sig-name-label").addEventListener("click", () => {
      state.activeSignal = state.activeSignal === name ? null : name;
      renderSignalNames();
    });
    row.querySelector(".sig-radix-btn").addEventListener("click", e => {
      e.stopPropagation();
      state.signalRadix[name] = _nextRadix(radix);
      renderSignalNames();
      drawWaveform();
    });
    row.querySelector(".sig-btn-rm").addEventListener("click", e => {
      e.stopPropagation();
      removeSig(name);
    });

    dom.signalNames.appendChild(row);
  });
  dom.waveSigCount.textContent = `${state.selectedSignals.length} 信号表示`;
}

function _moveSig(name, delta) {
  const idx = state.selectedSignals.indexOf(name);
  if (idx < 0) return;
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= state.selectedSignals.length) return;
  state.selectedSignals.splice(idx, 1);
  state.selectedSignals.splice(newIdx, 0, name);
  renderSignalNames();
  drawWaveform();
}

function removeSig(name) {
  state.selectedSignals = state.selectedSignals.filter(s => s !== name);
  if (state.activeSignal === name) state.activeSignal = null;
  renderSignalNames();
  drawWaveform();
}

function addSigs(names) {
  const existing = new Set(state.selectedSignals);
  for (const n of names) {
    if (!existing.has(n)) {
      state.selectedSignals.push(n);
      existing.add(n);
    }
  }
  renderSignalNames();
  if (state.waveformData) drawWaveform();
}

// =========================================================================
// 信号追加ピッカー（§18.4 B1）
// =========================================================================

function openSigPicker() {
  if (!state.vcdIndex) {
    log("VCD が読み込まれていません", "log-warn");
    return;
  }
  state.sigPickerSelected = new Set();
  renderSigPicker("");
  if (dom.sigPickerOverlay) dom.sigPickerOverlay.style.display = "flex";
  if (dom.sigPickerSearch) {
    dom.sigPickerSearch.value = "";
    dom.sigPickerSearch.focus();
  }
}

function renderSigPicker(filter) {
  if (!dom.sigPickerList || !state.vcdIndex) return;
  const allSigs = Object.keys(state.vcdIndex.signals || {});
  const flt = filter.toLowerCase();
  const filtered = flt ? allSigs.filter(s => s.toLowerCase().includes(flt)) : allSigs;

  dom.sigPickerList.innerHTML = filtered.map(name => {
    const added = state.selectedSignals.includes(name);
    const sel = state.sigPickerSelected.has(name);
    return `<div class="sig-picker-row ${sel ? "selected" : ""} ${added ? "already-added" : ""}"
      data-name="${escHtml(name)}" title="${escHtml(name)}">
      ${sel ? "☑" : "☐"} ${escHtml(name)} ${added ? "<span style='color:#8888aa;font-size:9px;'>(追加済)</span>" : ""}
    </div>`;
  }).join("") || '<div style="padding:8px;color:#8888aa;">一致する信号がありません</div>';

  dom.sigPickerList.querySelectorAll(".sig-picker-row").forEach(el => {
    el.addEventListener("click", () => {
      const n = el.dataset.name;
      if (state.sigPickerSelected.has(n)) {
        state.sigPickerSelected.delete(n);
      } else {
        state.sigPickerSelected.add(n);
      }
      renderSigPicker(dom.sigPickerSearch?.value ?? "");
    });
  });
}

// =========================================================================
// エッジ検索（§18.4 B2）
// =========================================================================

async function searchEdge(direction) {
  if (!state.vcdPath || !state.activeSignal) {
    log("VCD と検索対象信号を選択してください", "log-warn");
    return;
  }
  const edgeType = dom.edgeTypeSelect?.value ?? "any";
  const fromNs = state.cursorA >= 0 ? state.cursorA : 0;
  try {
    const data = await api.waveEdge(state.vcdPath, state.activeSignal, fromNs, direction, edgeType);
    if (data.found && data.time_ns != null) {
      state.cursorA = data.time_ns;
      drawWaveform();
      log(`エッジ: ${state.activeSignal} t=${data.time_ns}ns (${direction})`, "log-info");
    } else {
      log(`エッジが見つかりません: ${state.activeSignal} (${direction}, ${edgeType})`, "log-warn");
    }
  } catch (e) {
    log(`エッジ検索エラー: ${e.message}`, "log-error");
  }
}

// =========================================================================
// 波形表示セット保存・読込（§18.4 B4）
// =========================================================================

function openWavesetSaveDialog() {
  if (!state.selectedSignals.length) {
    log("表示中の信号がありません", "log-warn");
    return;
  }
  if (dom.wavesetNameInput) dom.wavesetNameInput.value = "";
  if (dom.wavesetSaveInfo) {
    dom.wavesetSaveInfo.textContent =
      `信号 ${state.selectedSignals.length} 件を保存します。`;
  }
  if (dom.wavesetSaveOverlay) dom.wavesetSaveOverlay.style.display = "flex";
  if (dom.wavesetNameInput) dom.wavesetNameInput.focus();
}

async function execWavesetSave() {
  const name = dom.wavesetNameInput?.value?.trim();
  if (!name) { log("セット名を入力してください", "log-warn"); return; }
  const svPaths = state.project.svFiles.map(f => f.path);
  const spec = state.project.specFile?.path ?? null;
  try {
    await api.wavesetSave(svPaths, spec, name, state.selectedSignals, state.signalRadix);
    if (dom.wavesetSaveOverlay) dom.wavesetSaveOverlay.style.display = "none";
    log(`✓ 波形セット「${name}」を保存しました`, "log-info");
  } catch (e) {
    log(`セット保存エラー: ${e.message}`, "log-error");
  }
}

async function openWavesetLoadMenu() {
  const svPaths = state.project.svFiles.map(f => f.path);
  const spec = state.project.specFile?.path ?? null;
  if (!svPaths.length) { log("プロジェクトが未選択です", "log-warn"); return; }
  try {
    const data = await api.wavesetGet(svPaths, spec);
    const sets = data.sets ?? [];
    if (!sets.length) { log("保存済みのセットがありません", "log-warn"); return; }
    // シンプルな選択: window.prompt で名前を入力
    const names = sets.map(s => s.name).join(", ");
    const chosen = window.prompt(`読み込むセット名:\n${names}`, sets[0].name);
    if (!chosen) return;
    const found = sets.find(s => s.name === chosen);
    if (!found) { log(`セットが見つかりません: ${chosen}`, "log-warn"); return; }
    state.selectedSignals = [...(found.signals ?? [])];
    state.signalRadix = { ...(found.radix ?? {}) };
    renderSignalNames();
    if (state.vcdPath) await fetchWaveRange();
    log(`✓ 波形セット「${chosen}」を読み込みました`, "log-info");
  } catch (e) {
    log(`セット読込エラー: ${e.message}`, "log-error");
  }
}

// =========================================================================
// 波形描画（Canvas）— §12.4 Z/X 区別色
// =========================================================================

const WAVE_ROW_H = 28;
const WAVE_RULER_H = 24;

// §12.4: Z は "z"（青系）、X は "x"（橙系）
const COLOR_SIG  = "#7c6af7"; // 通常信号
const COLOR_X    = "#fb923c"; // X（不定）
const COLOR_Z    = "#60a5fa"; // Z（ハイインピーダンス）
const COLOR_FILL_SIG = "rgba(124,106,247,0.12)";
const COLOR_FILL_X   = "rgba(251,146,60,0.15)";
const COLOR_FILL_Z   = "rgba(96,165,250,0.12)";

function _sigColor(v) {
  const s = String(v).toLowerCase();
  if (s === "z" || (s.includes("z") && !s.includes("x"))) return COLOR_Z;
  if (s.includes("x") || s === "x") return COLOR_X;
  return COLOR_SIG;
}
function _sigFill(v) {
  const s = String(v).toLowerCase();
  if (s === "z" || (s.includes("z") && !s.includes("x"))) return COLOR_FILL_Z;
  if (s.includes("x") || s === "x") return COLOR_FILL_X;
  return COLOR_FILL_SIG;
}
function _isX(v) { return String(v).toLowerCase().includes("x"); }
function _isZ(v) {
  const s = String(v).toLowerCase();
  return s === "z" || (s.includes("z") && !s.includes("x"));
}

function drawWaveform() {
  if (!state.waveformData) return;
  const wrapper = dom.waveformWrapper;
  const canvas = dom.waveformCanvas;
  const sigs = state.selectedSignals;
  if (!sigs.length) return;

  const endTime = state.waveformData.end_time || 1;
  const pxPerNs = state.zoom * 0.8;
  const totalW = Math.max(wrapper.clientWidth, Math.ceil(endTime * pxPerNs) + 60);
  const totalH = WAVE_RULER_H + sigs.length * WAVE_ROW_H + 4;

  canvas.width = totalW; canvas.height = totalH;
  canvas.style.width = totalW + "px"; canvas.style.height = totalH + "px";

  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#12121f";
  ctx.fillRect(0, 0, totalW, totalH);

  const step = _niceStep(endTime, Math.floor(totalW / 60));
  ctx.strokeStyle = "#2a2a40"; ctx.lineWidth = 1;
  for (let t = 0; t <= endTime + step; t += step) {
    const x = Math.round(t * pxPerNs) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, totalH); ctx.stroke();
  }
  ctx.fillStyle = "#3d3d5c";
  ctx.fillRect(0, 0, totalW, WAVE_RULER_H);
  ctx.fillStyle = "#a3a3b4"; ctx.font = "10px monospace"; ctx.textAlign = "left";
  for (let t = 0; t <= endTime + step; t += step) {
    ctx.fillText(`${t}ns`, Math.round(t * pxPerNs) + 3, 15);
  }

  sigs.forEach((name, si) => {
    const sigData = state.waveformData.signals[name];
    if (!sigData) return;
    _drawSignal(ctx, sigData, WAVE_RULER_H + si * WAVE_ROW_H, WAVE_ROW_H, totalW, endTime, pxPerNs, name);
  });

  _drawCursor(ctx, state.cursorA, "#f9c74f", totalH);
  _drawCursor(ctx, state.cursorB, "#60a5fa", totalH);

  if (state.cursorA >= 0 && state.cursorB >= 0) {
    dom.statusBarTop.textContent = `ΔT = ${Math.abs(state.cursorB - state.cursorA)} ns`;
  }
}

function _drawCursor(ctx, t, color, h) {
  if (t < 0) return;
  const x = t * state.zoom * 0.8;
  ctx.strokeStyle = color; ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  ctx.setLineDash([]);
}

function _drawSignal(ctx, sigData, y0, rowH, totalW, endTime, pxPerNs, name) {
  const changes = sigData.changes;
  const width = sigData.width;
  const is1bit = width === 1;
  const highY = y0 + 4, lowY = y0 + rowH - 6, midY = (highY + lowY) / 2;
  if (!changes?.length) return;

  const pts = [...changes.map(([t, v]) => ({ t, v })),
               { t: endTime + 1, v: changes[changes.length - 1][1] }];

  ctx.lineWidth = 1.5;
  ctx.font = "9px monospace"; ctx.textAlign = "center";

  for (let i = 0; i < pts.length - 1; i++) {
    const { t: t0, v: v0 } = pts[i], { t: t1 } = pts[i + 1];
    const x0 = t0 * pxPerNs, x1 = t1 * pxPerNs;
    if (x1 < 0 || x0 > totalW) continue;

    const stroke = _sigColor(v0);
    ctx.strokeStyle = stroke;

    if (is1bit) {
      // §12.4: Z=青 / X=橙 / 1=紫（高位） / 0=紫（低位）
      const y = (_isZ(v0) || _isX(v0)) ? midY : (v0 === "1" ? highY : lowY);
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      if (i > 0) {
        const prev_v = pts[i - 1].v;
        const prevY = (_isZ(prev_v) || _isX(prev_v)) ? midY : (prev_v === "1" ? highY : lowY);
        ctx.beginPath(); ctx.moveTo(x0, prevY); ctx.lineTo(x0, y); ctx.stroke();
      }
    } else {
      const mw = 5, lx1 = x0 + mw, rx0 = x1 - mw;
      ctx.fillStyle = _sigFill(v0);
      ctx.beginPath();
      ctx.moveTo(x0, midY); ctx.lineTo(lx1, highY);
      ctx.lineTo(Math.max(lx1, rx0), highY); ctx.lineTo(x1, midY);
      ctx.lineTo(Math.max(lx1, rx0), lowY); ctx.lineTo(lx1, lowY);
      ctx.closePath(); ctx.fill(); ctx.stroke();
      const segW = x1 - x0;
      if (segW > 18) {
        const sigRadix = state.signalRadix[name] || state.radix;
        ctx.fillStyle = stroke;
        ctx.fillText(_truncate(fmtVal(v0, width, sigRadix), segW - 10, ctx),
                     (x0 + x1) / 2, midY + 3.5);
      }
    }
  }
}

// §12.4 / §18.4 B3: Z/X 区別色、符号付き・符号なし表示
function fmtVal(binStr, width, radix) {
  if (typeof binStr !== "string") binStr = String(binStr);
  const lower = binStr.toLowerCase();
  if (lower === "x") return "X";
  if (lower === "z") return "Z";
  if (lower.includes("x") || lower.includes("z")) return binStr;  // 混在 → そのまま
  if (radix === "bin") return "b" + lower.padStart(width, "0");
  const n = parseInt(lower, 2);
  if (isNaN(n)) return binStr;
  if (radix === "hex") return "0x" + n.toString(16).toUpperCase().padStart(Math.ceil(width / 4), "0");
  if (radix === "dec" || radix === "unsigned") return String(n);
  if (radix === "signed") {
    // 2 の補数
    const signed = n >= (1 << (width - 1)) ? n - (1 << width) : n;
    return String(signed);
  }
  return binStr;
}

function _truncate(str, maxPx, ctx) {
  if (ctx.measureText(str).width <= maxPx) return str;
  while (str.length > 2 && ctx.measureText(str + "…").width > maxPx) str = str.slice(0, -1);
  return str + "…";
}

function _niceStep(endTime, maxTicks) {
  if (maxTicks <= 0) maxTicks = 1;
  const raw = endTime / maxTicks;
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  for (const m of [1, 2, 5, 10]) { if (mag * m >= raw) return Math.max(1, mag * m); }
  return Math.max(1, mag * 10);
}

// =========================================================================
// ズーム
// =========================================================================

function zoomIn()  { state.zoom = Math.min(state.zoom * 1.5, 50); drawWaveform(); }
function zoomOut() { state.zoom = Math.max(state.zoom / 1.5, 0.05); drawWaveform(); }
function zoomFit() {
  if (!state.waveformData) return;
  const end = state.waveformData.end_time || 1;
  const w = dom.waveformWrapper.clientWidth || 400;
  state.zoom = (w - 40) / (end * 0.8);
  drawWaveform();
}

// Canvas インタラクション
(function setupCanvasEvents() {
  let dragging = false, dragStartX = 0;
  dom.waveformCanvas.addEventListener("mousedown", e => { dragging = true; dragStartX = e.clientX; });
  dom.waveformCanvas.addEventListener("mousemove", e => {
    if (dragging) dom.waveformWrapper.scrollLeft -= (e.clientX - dragStartX) * 0.5;
  });
  dom.waveformCanvas.addEventListener("mouseup", e => {
    dragging = false;
    if (Math.abs(e.clientX - dragStartX) < 4) {
      const rect = dom.waveformCanvas.getBoundingClientRect();
      const t = Math.round((e.clientX - rect.left) / (state.zoom * 0.8));
      if (e.shiftKey) state.cursorB = t; else state.cursorA = t;
      drawWaveform();
    }
  });
  dom.waveformCanvas.addEventListener("wheel", e => {
    e.preventDefault();
    if (e.deltaY < 0) zoomIn(); else zoomOut();
  }, { passive: false });
})();

// 基数切替
function setRadix(r) {
  state.radix = r;
  document.querySelectorAll(".radix-btn").forEach(b => b.classList.toggle("active", b.dataset.radix === r));
  drawWaveform();
}

// =========================================================================
// ファイルブラウザ
// =========================================================================

async function openFileBrowser() {
  dom.fileBrowserOverlay.style.display = "flex";
  if (!state.browserPath) {
    const roots = await api.browseRoots();
    if (roots.roots.length > 0) state.browserPath = roots.roots[0];
  }
  await browseTo(state.browserPath);
}

async function browseTo(path) {
  state.browserPath = path;
  try {
    const data = await api.browse(path);
    renderBrowser(data.path, data.entries);
  } catch (e) { log(`ブラウザエラー: ${e.message}`, "log-error"); }
}

function renderBrowser(currentPath, entries) {
  const parts = currentPath.replace(/\\/g, "/").split("/").filter(Boolean);
  dom.browserBreadcrumb.innerHTML = parts.map((p, i) => {
    const partPath = "/" + parts.slice(0, i + 1).join("/");
    return `<a href="#" data-path="${partPath}" class="breadcrumb-link" style="color:#7c6af7;text-decoration:none;">${p}</a>`;
  }).join(" / ");
  dom.browserBreadcrumb.querySelectorAll(".breadcrumb-link").forEach(a => {
    a.addEventListener("click", e => { e.preventDefault(); browseTo(a.dataset.path); });
  });
  dom.browserBody.innerHTML = "";
  const parentPath = currentPath.replace(/[\\/][^\\/]+$/, "") || currentPath;
  if (parentPath !== currentPath) {
    const up = document.createElement("div");
    up.className = "fb-entry dir";
    up.innerHTML = `<span class="fb-icon">📁</span><span class="fb-name">..</span>`;
    up.addEventListener("click", () => browseTo(parentPath));
    dom.browserBody.appendChild(up);
  }
  entries.forEach(entry => {
    const el = document.createElement("div");
    const ext = entry.ext ?? "";
    el.className = `fb-entry ${entry.type === "dir" ? "dir" : "file"}`;
    el.dataset.ext = ext;
    el.innerHTML = `<span class="fb-icon"></span><span class="fb-name">${escHtml(entry.name)}</span>`;
    el.title = entry.path;
    el.addEventListener("click", e => {
      if (entry.type === "dir") { browseTo(entry.path); return; }
      document.querySelectorAll("#browser-body .fb-entry").forEach(el2 => el2.classList.remove("selected"));
      el.classList.add("selected");
      state.browserSelected = entry;
      dom.browserSelectedPath.textContent = entry.path;
    });
    el.addEventListener("dblclick", () => { if (entry.type === "dir") return; state.browserSelected = entry; confirmBrowserOpen(); });
    dom.browserBody.appendChild(el);
  });
}

function closeBrowser() { dom.fileBrowserOverlay.style.display = "none"; }

async function confirmBrowserOpen() {
  const entry = state.browserSelected;
  if (!entry) return;
  closeBrowser();
  const p = entry.path;
  const ext = (entry.ext ?? "").toLowerCase();
  if (ext === ".sv" || ext === ".v") {
    state.project.svFiles.push({ path: p });
    renderFileList();
    await openFileInEditor(p);
    await loadModule([p], state.project.top);
  } else if (ext === ".yaml" || ext === ".yml") {
    state.project.specFile = { path: p };
    renderFileList();
    await openFileInEditor(p);
  } else {
    await openFileInEditor(p);
  }
  log(`ファイルを追加: ${p}`, "log-info");
}

// プロジェクト保存
async function saveProject() {
  try {
    await api.projectSave("deltasim.project.yaml", {
      sv_files: state.project.svFiles.map(f => f.path),
      top: state.project.top,
      spec: state.project.specFile?.path ?? null,
      params: {},
    });
    log("プロジェクトを deltasim.project.yaml に保存しました", "log-info");
    // §18.4.1: プロジェクトを保存したときに最近一覧に追加
    await addCurrentToRecent();
    await loadRecentProjects();
  } catch (e) { log(`プロジェクト保存エラー: ${e.message}`, "log-error"); }
}

// =========================================================================
// 対話デバッグパネル（§8）
// =========================================================================

async function openDebugPanel() {
  if (!state.project.svFiles.length) {
    log("SV ファイルを先に読み込んでください", "log-error");
    return;
  }
  dom.debugOverlay.style.display = "flex";

  if (!state.debugSession) {
    try {
      const sess = await api.simCreate(
        state.project.svFiles.map(f => f.path),
        state.project.top,
        {}
      );
      state.debugSession = sess;
      dbgLog(`セッション作成: ${sess.module}  signals: ${sess.signals.length}`, "dbg-log-info");
      renderDebugSignals(sess.signals);
      updateDebugTime(sess.time_ns, 0);
    } catch (e) {
      dbgLog(`セッション作成エラー: ${e.message}`, "dbg-log-line");
    }
  }
}

function closeDebugPanel() {
  dom.debugOverlay.style.display = "none";
}

function updateDebugTime(time_ns, cycle) {
  dom.debugTime.textContent = `t=${time_ns}ns  cycle=${cycle}`;
}

function renderDebugSignals(signals) {
  dom.debugSignalsList.innerHTML = "";
  signals.forEach(s => {
    const row = document.createElement("div");
    row.className = "dbg-sig-row";
    const valStr = fmtVal(s.value ?? "0", s.width, state.radix);
    row.innerHTML = `
      <span class="dbg-sig-dir">${s.direction || ""}</span>
      <span class="dbg-sig-name">${escHtml(s.name)}</span>
      <span class="dbg-sig-val" data-sig="${escHtml(s.name)}">${escHtml(valStr)}</span>`;
    dom.debugSignalsList.appendChild(row);
  });
}

async function dbgRefreshSignals() {
  if (!state.debugSession) return;
  try {
    const data = await api.simSignals(state.debugSession.session_id);
    state.debugSession.signals = data.signals;
    updateDebugTime(data.time_ns, data.cycle);
    // 値を更新
    data.signals.forEach(s => {
      const el = dom.debugSignalsList.querySelector(`[data-sig="${s.name}"]`);
      if (el) el.textContent = fmtVal(s.value ?? "0", s.width, state.radix);
    });
    // 波形を更新
    await dbgRefreshWaveform();
  } catch (e) { dbgLog(`signals エラー: ${e.message}`); }
}

async function dbgStep() {
  if (!state.debugSession) return;
  const n = parseInt(dom.dbgStepN.value) || 1;
  try {
    const snap = await api.simStep(state.debugSession.session_id, n);
    updateDebugTime(snap.time_ns, state.debugSession.cycle ?? 0);
    if (snap.breakpoint_hit != null) {
      dbgLog(`[bp ${snap.breakpoint_hit} hit]  t=${snap.time_ns}ns`, "dbg-log-hit");
    } else {
      dbgLog(`step ${n} → t=${snap.time_ns}ns`, "dbg-log-info");
    }
    await dbgRefreshSignals();
  } catch (e) { dbgLog(`step エラー: ${e.message}`); }
}

async function dbgRunUntil() {
  if (!state.debugSession) return;
  const ns = parseInt(dom.dbgUntilNs.value) || 100;
  try {
    const snap = await api.simRunUntil(state.debugSession.session_id, ns);
    updateDebugTime(snap.time_ns, state.debugSession.cycle ?? 0);
    dbgLog(`run_until ${ns}ns → t=${snap.time_ns}ns${snap.breakpoint_hit != null ? ` [bp ${snap.breakpoint_hit}]` : ""}`,
           snap.breakpoint_hit != null ? "dbg-log-hit" : "dbg-log-info");
    await dbgRefreshSignals();
  } catch (e) { dbgLog(`run_until エラー: ${e.message}`); }
}

async function dbgReset() {
  if (!state.debugSession) return;
  try {
    const data = await api.simReset(state.debugSession.session_id);
    updateDebugTime(data.time_ns, data.cycle);
    dbgLog("シミュレーションをリセットしました", "dbg-log-info");
    await dbgRefreshSignals();
  } catch (e) { dbgLog(`reset エラー: ${e.message}`); }
}

async function dbgForce() {
  if (!state.debugSession) return;
  const sig = dom.dbgSigName.value.trim();
  const val = parseInt(dom.dbgSigVal.value, 0);
  if (!sig || isNaN(val)) { dbgLog("信号名と値を入力してください"); return; }
  try {
    await api.simForce(state.debugSession.session_id, sig, val);
    dbgLog(`force ${sig} = ${val}`, "dbg-log-info");
  } catch (e) { dbgLog(`force エラー: ${e.message}`); }
}

async function dbgSet() {
  if (!state.debugSession) return;
  const sig = dom.dbgSigName.value.trim();
  const val = parseInt(dom.dbgSigVal.value, 0);
  if (!sig || isNaN(val)) { dbgLog("信号名と値を入力してください"); return; }
  try {
    await api.simSet(state.debugSession.session_id, sig, val);
    dbgLog(`set ${sig} = ${val}`, "dbg-log-info");
    await dbgRefreshSignals();
  } catch (e) { dbgLog(`set エラー: ${e.message}`); }
}

async function dbgRelease() {
  if (!state.debugSession) return;
  const sig = dom.dbgSigName.value.trim();
  if (!sig) { dbgLog("信号名を入力してください"); return; }
  try {
    await api.simRelease(state.debugSession.session_id, sig);
    dbgLog(`release ${sig}`, "dbg-log-info");
  } catch (e) { dbgLog(`release エラー: ${e.message}`); }
}

async function dbgAddBp() {
  if (!state.debugSession) return;
  const cond = dom.dbgBpExpr.value.trim();
  if (!cond) return;
  try {
    const { id } = await api.simBpAdd(state.debugSession.session_id, cond);
    dbgLog(`bp [${id}] 追加: ${cond}`, "dbg-log-info");
    dom.dbgBpExpr.value = "";
    await dbgRefreshBps();
  } catch (e) { dbgLog(`bp 追加エラー: ${e.message}`); }
}

async function dbgRefreshBps() {
  if (!state.debugSession) return;
  try {
    const { breakpoints } = await api.simBpList(state.debugSession.session_id);
    dom.debugBps.innerHTML = breakpoints.map(bp =>
      `<div class="dbg-bp-entry">
        <span class="bp-id">[${bp.id}]</span>
        <span class="bp-cond">${escHtml(bp.condition)}</span>
        <span class="bp-del" onclick="dbgDelBp(${bp.id})">✕</span>
      </div>`
    ).join("");
  } catch (e) {}
}

async function dbgDelBp(id) {
  if (!state.debugSession) return;
  try {
    await api.simBpDel(state.debugSession.session_id, id);
    await dbgRefreshBps();
  } catch (e) { dbgLog(`bp 削除エラー: ${e.message}`); }
}

async function dbgRefreshWaveform() {
  if (!state.debugSession) return;
  try {
    const data = await api.simWaveform(state.debugSession.session_id, null, 0, null);
    state.debugWaveData = data;
    _drawDebugWaveform(data);
  } catch (e) {}
}

function _drawDebugWaveform(data) {
  const canvas = dom.debugWaveformCanvas;
  if (!canvas || !data?.signals) return;
  const sigs = Object.keys(data.signals).slice(0, 20);
  if (!sigs.length) return;
  const endTime = data.end_time || 1;
  const pxPerNs = 0.4;
  const totalW = Math.max(canvas.parentElement?.clientWidth ?? 400, Math.ceil(endTime * pxPerNs) + 40);
  const totalH = WAVE_RULER_H + sigs.length * WAVE_ROW_H;
  canvas.width = totalW; canvas.height = totalH;
  canvas.style.width = totalW + "px"; canvas.style.height = totalH + "px";
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#12121f"; ctx.fillRect(0, 0, totalW, totalH);

  ctx.fillStyle = "#3d3d5c"; ctx.fillRect(0, 0, totalW, WAVE_RULER_H);
  ctx.fillStyle = "#a3a3b4"; ctx.font = "9px monospace"; ctx.textAlign = "left";
  const step = _niceStep(endTime, Math.floor(totalW / 50));
  for (let t = 0; t <= endTime + step; t += step) {
    const x = Math.round(t * pxPerNs);
    ctx.fillText(`${t}`, x + 2, 13);
  }

  sigs.forEach((name, si) => {
    const sigData = data.signals[name];
    if (!sigData) return;
    _drawSignal(ctx, sigData, WAVE_RULER_H + si * WAVE_ROW_H, WAVE_ROW_H, totalW, endTime, pxPerNs);
  });
}

// =========================================================================
// ケースフィルタ UI
// =========================================================================

function parseCaseFilter(raw) {
  // カンマ区切り or スペース区切り → リストに変換（空 → null）
  if (!raw || !raw.trim()) return [];
  return raw.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
}

async function loadCaseCandidates() {
  if (!state.project.specFile) {
    log("スペックファイルを先に選択してください", "log-error");
    return;
  }
  try {
    const data = await api.specCases(state.project.specFile.path);
    const names = data.case_names ?? [];
    if (!names.length) {
      log("ケース名が取得できませんでした", "log-warn");
      return;
    }
    showCaseDropdown(names);
  } catch (e) {
    log(`ケース候補取得エラー: ${e.message}`, "log-error");
  }
}

function showCaseDropdown(names) {
  const btn = dom.btnCaseList;
  const rect = btn.getBoundingClientRect();
  const dd = dom.caseDropdown;

  dd.innerHTML = names.map((n, i) => `
    <div class="case-candidate" data-name="${escHtml(n)}">${escHtml(n)}</div>
  `).join("");

  dd.querySelectorAll(".case-candidate").forEach(el => {
    el.addEventListener("click", () => {
      // 入力欄に追加（重複排除）
      const existing = parseCaseFilter(dom.caseFilterInput.value);
      const name = el.dataset.name;
      if (!existing.includes(name)) {
        existing.push(name);
      }
      dom.caseFilterInput.value = existing.join(", ");
      hideCaseDropdown();
    });
  });

  dd.style.display = "block";
  dd.style.left = rect.left + "px";
  dd.style.top  = (rect.bottom + 4) + "px";
}

function hideCaseDropdown() {
  dom.caseDropdown.style.display = "none";
}

// ドロップダウン外クリックで閉じる
document.addEventListener("click", e => {
  if (!dom.caseDropdown.contains(e.target) && e.target !== dom.btnCaseList) {
    hideCaseDropdown();
  }
});

// =========================================================================
// ユーティリティ
// =========================================================================

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// =========================================================================
// イベント接続
// =========================================================================

dom.btnBrowse.addEventListener("click", openFileBrowser);
dom.btnLoadExamples.addEventListener("click", loadExamplesList);
dom.btnGen.addEventListener("click", runGen);
dom.btnRunTest.addEventListener("click", runTest);
dom.btnRunSim.addEventListener("click", runSim);
dom.btnStop.addEventListener("click", stopTest);
dom.btnSaveProject.addEventListener("click", saveProject);
dom.btnDebug.addEventListener("click", openDebugPanel);
dom.btnCaseList.addEventListener("click", (e) => { e.stopPropagation(); loadCaseCandidates(); });
dom.btnZoomIn.addEventListener("click", zoomIn);
dom.btnZoomOut.addEventListener("click", zoomOut);
dom.btnZoomFit.addEventListener("click", zoomFit);
dom.btnSaveFile.addEventListener("click", saveFile);
dom.btnClearLog.addEventListener("click", logClear);
if (dom.btnExportJson) dom.btnExportJson.addEventListener("click", () => exportResult("json"));
if (dom.btnExportJunit) dom.btnExportJunit.addEventListener("click", () => exportResult("junit"));

// 波形ツールバー（§18.4 B1/B2/B4）
if (dom.btnEdgePrev) dom.btnEdgePrev.addEventListener("click", () => searchEdge("prev"));
if (dom.btnEdgeNext) dom.btnEdgeNext.addEventListener("click", () => searchEdge("next"));
if (dom.btnWaveAddSig) dom.btnWaveAddSig.addEventListener("click", openSigPicker);
if (dom.btnWavesetSave) dom.btnWavesetSave.addEventListener("click", openWavesetSaveDialog);
if (dom.btnWavesetLoad) dom.btnWavesetLoad.addEventListener("click", openWavesetLoadMenu);

// 信号ピッカー
if (dom.btnSigPickerClose) dom.btnSigPickerClose.addEventListener("click", () => { dom.sigPickerOverlay.style.display = "none"; });
if (dom.btnSigPickerCancel) dom.btnSigPickerCancel.addEventListener("click", () => { dom.sigPickerOverlay.style.display = "none"; });
if (dom.btnSigPickerAdd) dom.btnSigPickerAdd.addEventListener("click", () => {
  addSigs([...state.sigPickerSelected]);
  dom.sigPickerOverlay.style.display = "none";
  fetchWaveRange().catch(() => {});
});
if (dom.sigPickerSearch) dom.sigPickerSearch.addEventListener("input", e => renderSigPicker(e.target.value));
if (dom.sigPickerOverlay) dom.sigPickerOverlay.addEventListener("click", e => { if (e.target === dom.sigPickerOverlay) dom.sigPickerOverlay.style.display = "none"; });

// 波形セット保存ダイアログ
if (dom.btnWavesetSaveClose) dom.btnWavesetSaveClose.addEventListener("click", () => { dom.wavesetSaveOverlay.style.display = "none"; });
if (dom.btnWavesetSaveCancel) dom.btnWavesetSaveCancel.addEventListener("click", () => { dom.wavesetSaveOverlay.style.display = "none"; });
if (dom.btnWavesetSaveOk) dom.btnWavesetSaveOk.addEventListener("click", execWavesetSave);

// 生成保存ダイアログ
if (dom.btnGenSaveClose) dom.btnGenSaveClose.addEventListener("click", () => { dom.genSaveOverlay.style.display = "none"; });
if (dom.btnGenSaveCancel) dom.btnGenSaveCancel.addEventListener("click", () => { dom.genSaveOverlay.style.display = "none"; });
if (dom.btnGenSaveOk) dom.btnGenSaveOk.addEventListener("click", handleGenSaveOk);
if (dom.genSaveDir) dom.genSaveDir.addEventListener("input", _updateGenSavePreview);
if (dom.genSaveTb) dom.genSaveTb.addEventListener("change", _updateGenSavePreview);
if (dom.genSaveSpec) dom.genSaveSpec.addEventListener("change", _updateGenSavePreview);

dom.btnBrowserClose.addEventListener("click", closeBrowser);
dom.btnBrowserCancel.addEventListener("click", closeBrowser);
dom.btnBrowserOpen.addEventListener("click", confirmBrowserOpen);
dom.fileBrowserOverlay.addEventListener("click", e => {
  if (e.target === dom.fileBrowserOverlay) closeBrowser();
});

document.querySelectorAll(".radix-btn").forEach(btn => {
  btn.addEventListener("click", () => setRadix(btn.dataset.radix));
});

// デバッグパネル
dom.btnDebugClose.addEventListener("click", closeDebugPanel);
dom.debugOverlay.addEventListener("click", e => { if (e.target === dom.debugOverlay) closeDebugPanel(); });
dom.btnDbgStep.addEventListener("click", dbgStep);
dom.btnDbgRunUntil.addEventListener("click", dbgRunUntil);
dom.btnDbgReset.addEventListener("click", dbgReset);
dom.btnDbgForce.addEventListener("click", dbgForce);
dom.btnDbgSet.addEventListener("click", dbgSet);
dom.btnDbgRelease.addEventListener("click", dbgRelease);
dom.btnDbgBpAdd.addEventListener("click", dbgAddBp);

window.addEventListener("resize", () => { if (state.waveformData) drawWaveform(); });

// =========================================================================
// 初期化
// =========================================================================

(async function init() {
  initEditor();
  setStatus("ready", "idle");
  dom.btnStop.disabled = true;
  log("deltasim GUI v3 起動（M5 API + §12/§18.4 対応）", "log-info");
  log("サンプルを読み込むか、「📂 開く」でファイルを選択してください。", "log-line");
  await Promise.all([loadExamplesList(), loadRecentProjects()]);

  // CLI 引数からの初期プロジェクト（環境変数経由）
  try {
    const res = await fetch("/api/initial-project");
    if (res.ok) {
      const proj = await res.json();
      if (proj?.sv_files?.length) {
        state.project.svFiles = proj.sv_files.map(p => ({ path: p }));
        state.project.specFile = proj.spec ? { path: proj.spec } : null;
        state.project.top = proj.top;
        renderFileList();
        await loadModule(proj.sv_files, proj.top);
        if (proj.sv_files.length > 0) await openFileInEditor(proj.sv_files[0]);
        log("CLI引数からプロジェクトを読み込みました", "log-info");
      }
    }
  } catch (_) {}
})();
