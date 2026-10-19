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
  radix: "hex",
  zoom: 1.0,
  cursorA: -1,
  cursorB: -1,
  running: false,
  currentJobId: null,
  editorPath: null,
  editorLang: "text",
  errorLines: [],
  browserPath: null,
  browserSelected: null,
  // デバッグパネル
  debugSession: null,   // {session_id, module, signals:[]}
  debugWaveData: null,
};

// =========================================================================
// DOM refs
// =========================================================================

const $ = id => document.getElementById(id);

const dom = {
  btnBrowse: $("btn-browse"),
  btnLoadExamples: $("btn-load-examples"),
  btnRunTest: $("btn-run-test"),
  btnStop: $("btn-stop"),
  btnSaveProject: $("btn-save-project"),
  btnDebug: $("btn-debug"),
  btnZoomIn: $("btn-zoom-in"),
  btnZoomOut: $("btn-zoom-out"),
  btnZoomFit: $("btn-zoom-fit"),
  btnSaveFile: $("btn-save-file"),
  btnClearLog: $("btn-clear-log"),
  seedInput: $("seed-input"),
  statusBarTop: $("status-bar-top"),
  progressBar: $("progress-bar"),
  fileList: $("file-list"),
  specList: $("spec-list"),
  moduleSelect: $("module-select"),
  exampleList: $("example-list"),
  editorFilename: $("editor-filename"),
  editorErrorsBadge: $("editor-errors-badge"),
  signalNames: $("signal-names"),
  waveformCanvas: $("waveform-canvas"),
  waveformWrapper: $("waveform-canvas-wrapper"),
  waveSigCount: $("wave-sig-count"),
  summaryBar: $("summary-bar"),
  resultList: $("result-list"),
  truncatedBanner: $("truncated-banner"),
  truncatedMsg: $("truncated-msg"),
  btnOpenVcd: $("btn-open-vcd"),
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
  testStart:    (sv, spec, top, seed) => apiFetch("POST", "/api/test/start", {
    sv_files: sv, spec, top: top ?? null, seed: seed !== "" ? parseInt(seed) : null,
  }),
  testCancel:   (jid)               => apiFetch("POST", `/api/test/${jid}/cancel`, {}),
  testResult:   (jid)               => apiFetch("GET",  `/api/test/${jid}/result`),
  waveIndex:    (path)              => apiFetch("GET", `/api/waveform/index?path=${encodeURIComponent(path)}`),
  waveRange:    (path, sigs, t0, t1) => {
    const p = new URLSearchParams({ path, t_start: t0 ?? 0 });
    if (t1 != null) p.append("t_end", t1);
    if (sigs?.length) p.append("signals", sigs.join(","));
    return apiFetch("GET", `/api/waveform/range?${p}`);
  },
  projectSave:  (path, data)        => apiFetch("POST", "/api/project", { path, ...data }),
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
  dom.moduleSelect.innerHTML = "";
  if (state.module) {
    const opt = document.createElement("option");
    opt.value = state.module.module;
    opt.textContent = state.module.module;
    dom.moduleSelect.appendChild(opt);
    state.project.top = state.module.module;
  }
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
// モジュールロード
// =========================================================================

async function loadModule(svPaths, top) {
  try {
    const data = await api.load(svPaths, top);
    state.module = data;
    log(`ロード成功: ${data.module} (${data.ports.length} ポート)`, "log-info");
    // 構造化エラーをエディタに反映（M5 ErrorInfo 形式）
    if (data.errors?.length > 0) {
      setEditorErrors(data.errors.filter(e => e.severity === "error" || e.severity === "warning"));
      data.errors.forEach(e =>
        log(`[${e.severity}] ${e.message}${e.line ? ` (行 ${e.line})` : ""}`,
            e.severity === "error" ? "log-error" : "log-warn")
      );
    } else {
      clearEditorErrors();
    }
    renderFileList();
    setStatus(`${data.module} ロード済み`, "idle");
  } catch (e) {
    log(`ロードエラー: ${e.message}`, "log-error");
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

    const { job_id } = await api.testStart(svPaths, specPath, top, seed);
    state.currentJobId = job_id;
    await consumeSSE(`/api/test/${job_id}/events`);

    const { done, result } = await api.testResult(job_id);
    if (done && result) {
      state.testResult = result;
      state.vcdPath = result.vcd_path;
      renderResults(result);

      // §12.3: truncated_at 警告
      if (result.truncated_at != null) {
        showTruncatedBanner(result.truncated_at, result.vcd_path);
      }

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
    case "finish":
      setProgress(95);
      break;
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
  state.selectedCase = null;
}

function renderResults(result) {
  const s = result.summary;
  // §13.1: summary.elapsed_ms が推奨
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

  // §13.2: cases[] ベースで表示（PR#8 で追加された per-case details）
  const cases = result.cases ?? [];

  if (cases.length === 0 && result.status === "ERROR") {
    // パース失敗等: ケースなしでエラー表示
    const card = _makeCaseCard({
      name: result.module || "error",
      status: "ERROR",
      seed: null,
      elapsed_ms: totalMs,
      log: result.log ?? [],
      checks: [],
      errors: result.errors ?? [],
      truncated_at: null,
      cancelled: result.cancelled,
    });
    dom.resultList.appendChild(card);
    return;
  }

  cases.forEach((c, i) => {
    const card = _makeCaseCard(c);
    dom.resultList.appendChild(card);
    if (i === 0) selectCase(card, c);
  });
}

function _makeCaseCard(c) {
  // §13.2: cases[] の要素（name/status/seed/elapsed_ms/checks/log）
  const card = document.createElement("div");
  card.className = "case-card";
  const dur = c.elapsed_ms != null ? `${(c.elapsed_ms / 1000).toFixed(3)}s` : "";

  // failures: checks[] から FAIL/ERROR のものを抽出
  const failures = (c.checks ?? []).filter(chk => chk.status !== "PASS" && chk.status !== "SKIP");
  const errMsg   = (c.errors ?? []).map(e => e.message).join("; ");

  card.innerHTML = `
    <div class="case-header">
      <div class="status-dot dot-${c.status}"></div>
      <div class="case-name">${escHtml(c.name)}</div>
      <div class="case-meta">${dur}</div>
    </div>
    <div class="case-detail">
      ${c.seed != null ? `<div class="failure-label">シード: <code>${c.seed}</code></div>` : ""}
      ${c.truncated_at != null ? `<div class="failure-label" style="color:#fb923c;">⚠ 波形上限 ${c.truncated_at}ns</div>` : ""}
      ${c.cancelled ? `<div class="failure-label" style="color:#a3a3b4;">⚠ キャンセル</div>` : ""}
      ${errMsg ? `<div class="failure-label" style="color:#fb923c;">${escHtml(errMsg)}</div>` : ""}
      ${_renderFailures(failures)}
      ${_renderCaseLog(c.log ?? [])}
    </div>`;
  card.addEventListener("click", () => selectCase(card, c));
  return card;
}

function _renderFailures(failures) {
  if (!failures?.length) return '<div class="failure-label">✓ 全チェック合格</div>';
  return failures.slice(0, 10).map(f =>
    `<div class="failure-item">cycle=${f.cycle} <b>${escHtml(String(f.signal))}</b> `+
    `期待:<b>${escHtml(String(f.expected))}</b> 実測:<b>${escHtml(String(f.actual))}</b></div>`
  ).join("") + (failures.length > 10 ? `<div class="failure-label">… 他 ${failures.length - 10} 件</div>` : "");
}

function _renderCaseLog(logLines) {
  if (!logLines?.length) return "";
  return `<div class="failure-label" style="margin-top:4px;">ログ:</div>`
    + logLines.slice(0, 5).map(l =>
        `<div style="font-family:monospace;font-size:10px;color:#a3a3b4;padding:0 4px;">${escHtml(l)}</div>`
      ).join("")
    + (logLines.length > 5 ? `<div class="failure-label">… 他 ${logLines.length - 5} 行</div>` : "");
}

function selectCase(card, c) {
  document.querySelectorAll(".case-card").forEach(el => el.classList.remove("selected"));
  card.classList.add("selected");
  state.selectedCase = c;
  // 波形カーソルを最初の失敗へジャンプ
  const firstFail = (c.checks ?? []).find(chk => chk.status !== "PASS");
  if (firstFail && state.waveformData) {
    state.cursorA = firstFail.time_ns ?? (firstFail.cycle ?? 0) * 10;
    drawWaveform();
  }
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

function renderSignalNames() {
  dom.signalNames.innerHTML = "";
  state.selectedSignals.forEach(name => {
    const row = document.createElement("div");
    row.className = "sig-name-row";
    row.textContent = name;
    row.title = name;
    row.addEventListener("click", () => row.classList.toggle("highlighted"));
    dom.signalNames.appendChild(row);
  });
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
    _drawSignal(ctx, sigData, WAVE_RULER_H + si * WAVE_ROW_H, WAVE_ROW_H, totalW, endTime, pxPerNs);
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

function _drawSignal(ctx, sigData, y0, rowH, totalW, endTime, pxPerNs) {
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
        ctx.fillStyle = stroke;
        ctx.fillText(_truncate(fmtVal(v0, width, state.radix), segW - 10, ctx),
                     (x0 + x1) / 2, midY + 3.5);
      }
    }
  }
}

// §12.4: Z と X の区別（大文字小文字は正規化済み）
function fmtVal(binStr, width, radix) {
  if (typeof binStr !== "string") binStr = String(binStr);
  const lower = binStr.toLowerCase();
  // 単一文字 x/z はそのまま大文字表示
  if (lower === "x") return "X";
  if (lower === "z") return "Z";
  // 混在バイナリ → そのまま
  if (lower.includes("x") || lower.includes("z")) return binStr;
  if (radix === "bin") return "b" + lower.padStart(width, "0");
  const n = parseInt(lower, 2);
  if (isNaN(n)) return binStr;
  if (radix === "hex") return "0x" + n.toString(16).toUpperCase().padStart(Math.ceil(width / 4), "0");
  if (radix === "dec") return String(n);
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
dom.btnRunTest.addEventListener("click", runTest);
dom.btnStop.addEventListener("click", stopTest);
dom.btnSaveProject.addEventListener("click", saveProject);
dom.btnDebug.addEventListener("click", openDebugPanel);
dom.btnZoomIn.addEventListener("click", zoomIn);
dom.btnZoomOut.addEventListener("click", zoomOut);
dom.btnZoomFit.addEventListener("click", zoomFit);
dom.btnSaveFile.addEventListener("click", saveFile);
dom.btnClearLog.addEventListener("click", logClear);

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
  log("deltasim GUI v3 起動（M5 API + §12 対応）", "log-info");
  log("サンプルを読み込むか、「📂 開く」でファイルを選択してください。", "log-line");
  await loadExamplesList();

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
