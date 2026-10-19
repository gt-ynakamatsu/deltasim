/**
 * deltasim GUI v2 — メインアプリケーション
 *
 * 変更点 (§11 対応):
 *  - CodeMirror 5 によるエディタ（SV/YAML構文ハイライト + エラー行表示 + 保存）
 *  - サーバー側ファイルブラウザ（127.0.0.1限定・パストラバーサル防止）
 *  - SSE によるテスト進捗表示（REST POST + SSE GET）
 *  - VCD 遅延読み込み（index → range）、最大200信号・初期20信号
 */

"use strict";

// =========================================================================
// 状態
// =========================================================================

const state = {
  project: { svFiles: [], specFile: null, top: null },
  module: null,
  testResult: null,
  waveformData: null,   // { timescale, signals: {name: {width, changes:[]}}, end_time }
  vcdIndex: null,       // { timescale, signals: {name: {width, vcd_id}}, end_time }
  vcdPath: null,
  selectedCase: null,
  selectedSignals: [],  // 波形ビューアに表示する信号名[]
  radix: "hex",
  zoom: 1.0,
  cursorA: -1,
  cursorB: -1,
  running: false,
  currentJobId: null,
  editorPath: null,     // 現在エディタに開いているファイルパス
  editorLang: "text",
  errorLines: [],       // エラー行番号リスト（1-indexed）
  browserPath: null,    // ファイルブラウザの現在パス
  browserSelected: null,// ブラウザで選択中のエントリ
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
  btnZoomIn: $("btn-zoom-in"),
  btnZoomOut: $("btn-zoom-out"),
  btnZoomFit: $("btn-zoom-fit"),
  btnSaveFile: $("btn-save-file"),
  btnClearLog: $("btn-clear-log"),
  seedInput: $("seed-input"),
  statusBarTop: $("status-bar-top"),
  progressBar: $("progress-bar"),
  // Sidebar
  fileList: $("file-list"),
  specList: $("spec-list"),
  moduleSelect: $("module-select"),
  exampleList: $("example-list"),
  // Editor
  editorFilename: $("editor-filename"),
  editorErrorsBadge: $("editor-errors-badge"),
  // Waveform
  signalNames: $("signal-names"),
  waveformCanvas: $("waveform-canvas"),
  waveformWrapper: $("waveform-canvas-wrapper"),
  waveSigCount: $("wave-sig-count"),
  // Results
  summaryBar: $("summary-bar"),
  resultList: $("result-list"),
  // Log
  logOutput: $("log-output"),
  // Statusbar
  statusbar: $("statusbar"),
  statusText: $("status-text"),
  dropOverlay: $("drop-overlay"),
  // File browser dialog
  fileBrowserOverlay: $("file-browser-overlay"),
  browserBreadcrumb: $("browser-breadcrumb"),
  browserBody: $("browser-body"),
  browserSelectedPath: $("browser-selected-path"),
  btnBrowserClose: $("btn-browser-close"),
  btnBrowserCancel: $("btn-browser-cancel"),
  btnBrowserOpen: $("btn-browser-open"),
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
    indentUnit: 2,
    tabSize: 2,
    indentWithTabs: false,
    lineWrapping: false,
    gutters: ["CodeMirror-linenumbers", "error-gutter"],
    extraKeys: {
      "Ctrl-S": saveFile,
      "Cmd-S":  saveFile,
    },
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

function setEditorErrors(lines) {
  if (!cmEditor) return;
  clearEditorErrors();
  state.errorLines = lines;
  for (const ln of lines) {
    const lineIdx = ln - 1;
    cmEditor.setGutterMarker(lineIdx, "error-gutter", makeErrorMarker());
    cmEditor.addLineClass(lineIdx, "background", "cm-error-line");
  }
  if (lines.length > 0) {
    dom.editorErrorsBadge.style.display = "";
    dom.editorErrorsBadge.textContent = `⚠ ${lines.length} エラー行`;
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
  dom.editorErrorsBadge.textContent = "";
}

function makeErrorMarker() {
  const el = document.createElement("div");
  el.className = "cm-error-gutter";
  el.textContent = "●";
  el.title = "エラー行";
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
  examples:     ()              => apiFetch("GET", "/api/examples"),
  load:         (files, top)    => apiFetch("POST", "/api/load", { files, top }),
  browseRoots:  ()              => apiFetch("GET", "/api/browse/roots"),
  browse:       (path)          => apiFetch("GET", `/api/browse?path=${encodeURIComponent(path)}`),
  fileRead:     (path)          => apiFetch("GET", `/api/file/read?path=${encodeURIComponent(path)}`),
  fileWrite:    (path, content) => apiFetch("POST", "/api/file/write", { path, content }),
  testStart:    (sv_files, spec, top, seed) => apiFetch("POST", "/api/test/start", {
    sv_files, spec, top: top ?? null, seed: seed !== "" ? parseInt(seed) : null,
  }),
  testCancel:   (jid)           => apiFetch("POST", `/api/test/${jid}/cancel`, {}),
  testResult:   (jid)           => apiFetch("GET",  `/api/test/${jid}/result`),
  waveIndex:    (path)          => apiFetch("GET", `/api/waveform/index?path=${encodeURIComponent(path)}`),
  waveRange:    (path, sigs, t0, t1) => {
    const p = new URLSearchParams({ path, t_start: t0 ?? 0 });
    if (t1 != null) p.append("t_end", t1);
    if (sigs?.length) p.append("signals", sigs.join(","));
    return apiFetch("GET", `/api/waveform/range?${p}`);
  },
  projectSave: (path, data)  => apiFetch("POST", "/api/project", { path, ...data }),
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
    li.title = state.project.specFile.path;
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
    log(`ファイルを開きました: ${path}`, "log-info");
  } catch (e) {
    log(`ファイル読み込みエラー: ${e.message}`, "log-error");
  }
}

// =========================================================================
// ファイル保存
// =========================================================================

async function saveFile() {
  if (!state.editorPath || !cmEditor) return;
  const content = cmEditor.getValue();
  try {
    await api.fileWrite(state.editorPath, content);
    log(`保存しました: ${state.editorPath}`, "log-info");

    // SV ファイルの場合は再ロード
    const ext = state.editorPath.split(".").pop().toLowerCase();
    if (ext === "sv" || ext === "v") {
      const svPaths = state.project.svFiles.map(f => f.path);
      if (svPaths.length > 0) {
        await loadModule(svPaths, state.project.top);
      }
    }
  } catch (e) {
    log(`保存エラー: ${e.message}`, "log-error");
  }
}

// =========================================================================
// モジュールロード
// =========================================================================

async function loadModule(svPaths, top) {
  try {
    const data = await api.load(svPaths, top);
    state.module = data;
    const portSummary = data.ports.map(p => `${p.direction.padEnd(7)} [${p.width-1}:0] ${p.name}`).join("\n");
    log(`ロード成功: ${data.module} (${data.ports.length} ポート)`, "log-info");

    // エラー情報をエディタに反映
    if (data.errors?.length > 0) {
      const errorLines = data.errors.filter(e => e.line).map(e => e.line);
      setEditorErrors(errorLines);
      data.errors.forEach(e => log(`[${e.severity}] ${e.message}${e.line ? ` (行 ${e.line})` : ""}`, "log-error"));
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
  } catch (e) {
    log(`サンプル取得エラー: ${e.message}`, "log-error");
  }
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

  try {
    const svPaths = state.project.svFiles.map(f => f.path);
    const specPath = state.project.specFile.path;
    const seed = dom.seedInput.value.trim();
    const top = state.project.top;

    const { job_id } = await api.testStart(svPaths, specPath, top, seed);
    state.currentJobId = job_id;
    log(`ジョブ開始: ${job_id}`, "log-info");

    await consumeSSE(`/api/test/${job_id}/events`);

    const { done, result } = await api.testResult(job_id);
    if (done && result) {
      state.testResult = result;
      state.vcdPath = result.vcd_path;
      renderResults(result);

      const s = result.summary;
      const cls = s.fail > 0 ? "fail" : s.error > 0 ? "error" : "pass";
      const msg = `完了: ${s.pass} PASS / ${s.fail} FAIL / ${s.error} ERROR`;
      setStatus(msg, cls);
      log(`✓ ${msg}`, cls === "pass" ? "log-pass" : "log-fail");
      setProgress(100);

      if (result.vcd_path) {
        log(`波形読み込み中: ${result.vcd_path}`, "log-info");
        await loadWaveformIndex(result.vcd_path);
      }

      (result.cases?.[0]?.log ?? []).forEach(l => log(l));
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
  return new Promise((resolve, reject) => {
    const es = new EventSource(url);
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        handleSSEEvent(event);
        if (event.event === "finish" || event.event === "error") {
          es.close();
          resolve();
        }
      } catch (_) {}
    };
    es.onerror = (e) => {
      es.close();
      resolve(); // エラーでも result を取得して続行
    };
  });
}

function handleSSEEvent(event) {
  switch (event.event) {
    case "start":
      log(`シミュ開始: ${event.spec ?? ""}`, "log-info");
      setProgress(10);
      break;
    case "progress": {
      const pct = event.total > 0 ? Math.round(10 + 80 * event.cycle / event.total) : 50;
      setProgress(pct);
      const phase = event.phase ?? "sim";
      dom.statusBarTop.textContent = `[${phase}] ${event.cycle}/${event.total} cycles  t=${event.time_ns ?? 0}ns`;
      break;
    }
    case "case_done":
      log(`ケース完了: ${event.name} → ${event.status}`, event.status === "PASS" ? "log-pass" : "log-fail");
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
  } catch (e) {
    log(`キャンセルエラー: ${e.message}`, "log-error");
  }
}

// =========================================================================
// 結果表示
// =========================================================================

function clearResults() { dom.resultList.innerHTML = ""; dom.summaryBar.innerHTML = ""; }

function renderResults(result) {
  const s = result.summary;
  dom.summaryBar.innerHTML = [
    ["pass",  s.pass,  "badge-pass"],
    ["fail",  s.fail,  "badge-fail"],
    ["error", s.error, "badge-error"],
    ["skip",  s.skip,  "badge-skip"],
  ].map(([l,c,cls]) => `<span class="count-badge ${cls}">${c} ${l.toUpperCase()}</span>`).join("");

  dom.resultList.innerHTML = "";
  result.cases.forEach((c, i) => {
    const card = document.createElement("div");
    card.className = "case-card";
    const dur = c.duration_sec != null ? `${c.duration_sec.toFixed(2)}s` : "";
    card.innerHTML = `
      <div class="case-header">
        <div class="status-dot dot-${c.status}"></div>
        <div class="case-name">${escHtml(c.name)}</div>
        <div class="case-meta">${dur}</div>
      </div>
      <div class="case-detail">
        ${c.seed != null ? `<div class="failure-label">シード: ${c.seed}</div>` : ""}
        ${c.message ? `<div class="failure-label">${escHtml(c.message)}</div>` : ""}
        ${renderFailures(c.failures)}
        <div class="case-actions">
          <button class="btn btn-secondary" style="font-size:10px;padding:2px 7px;" onclick="rerunWithSeed(${c.seed})">↺ このシードで再実行</button>
        </div>
      </div>`;
    card.addEventListener("click", () => selectCase(card, c));
    dom.resultList.appendChild(card);
    if (i === 0) selectCase(card, c);
  });
}

function renderFailures(failures) {
  if (!failures?.length) return '<div class="failure-label">✓ 全チェック合格</div>';
  return failures.map(f =>
    `<div class="failure-item">cycle=${f.cycle} <b>${escHtml(String(f.signal))}</b> 期待:<b>${escHtml(String(f.expected))}</b> 実測:<b>${escHtml(String(f.actual))}</b></div>`
  ).join("");
}

function selectCase(card, c) {
  document.querySelectorAll(".case-card").forEach(el => el.classList.remove("selected"));
  card.classList.add("selected");
  state.selectedCase = c;
  if (c.failures?.length > 0 && state.waveformData) {
    state.cursorA = c.failures[0].time_ns ?? c.failures[0].cycle * 10;
    drawWaveform();
  }
}

async function rerunWithSeed(seed) {
  if (!seed) return;
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

    log(`波形インデックス: ${allSigs.length} 信号, 終端≈${idx.end_time}ns（概算）`, "log-info");

    // 初期20信号分の値変化を取得
    await fetchWaveRange();
  } catch (e) {
    log(`波形インデックスエラー: ${e.message}`, "log-error");
  }
}

async function fetchWaveRange() {
  if (!state.vcdPath || !state.selectedSignals.length) return;
  try {
    const data = await api.waveRange(
      state.vcdPath,
      state.selectedSignals,
      0,
      null  // 全範囲
    );
    state.waveformData = data;
    renderSignalNames();
    state.zoom = 1.0;
    zoomFit();
    log(`波形データ取得: ${state.selectedSignals.length} 信号`, "log-info");
  } catch (e) {
    log(`波形データエラー: ${e.message}`, "log-error");
  }
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
  state.selectedSignals.forEach((name) => {
    const row = document.createElement("div");
    row.className = "sig-name-row";
    row.textContent = name;
    row.title = name;
    row.addEventListener("click", () => row.classList.toggle("highlighted"));
    dom.signalNames.appendChild(row);
  });
}

// =========================================================================
// 波形描画（Canvas）
// =========================================================================

const WAVE_ROW_H = 28;
const WAVE_RULER_H = 24;

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

  canvas.width = totalW;
  canvas.height = totalH;
  canvas.style.width = totalW + "px";
  canvas.style.height = totalH + "px";

  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#12121f";
  ctx.fillRect(0, 0, totalW, totalH);

  // グリッド
  const step = _niceStep(endTime, Math.floor(totalW / 60));
  ctx.strokeStyle = "#2a2a40";
  ctx.lineWidth = 1;
  for (let t = 0; t <= endTime + step; t += step) {
    const x = Math.round(t * pxPerNs) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, totalH); ctx.stroke();
  }

  // ルーラー
  ctx.fillStyle = "#3d3d5c";
  ctx.fillRect(0, 0, totalW, WAVE_RULER_H);
  ctx.fillStyle = "#a3a3b4";
  ctx.font = "10px monospace";
  ctx.textAlign = "left";
  for (let t = 0; t <= endTime + step; t += step) {
    const x = Math.round(t * pxPerNs);
    ctx.fillText(`${t}ns`, x + 3, 15);
  }

  // 信号描画
  sigs.forEach((name, si) => {
    const sigData = state.waveformData.signals[name];
    if (!sigData) return;
    _drawSignal(ctx, sigData, WAVE_RULER_H + si * WAVE_ROW_H, WAVE_ROW_H, totalW, endTime, pxPerNs);
  });

  // カーソル A
  if (state.cursorA >= 0) {
    const x = state.cursorA * pxPerNs;
    ctx.strokeStyle = "#f9c74f"; ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, totalH); ctx.stroke();
    ctx.setLineDash([]);
  }

  // カーソル B
  if (state.cursorB >= 0) {
    const x = state.cursorB * pxPerNs;
    ctx.strokeStyle = "#60a5fa"; ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, totalH); ctx.stroke();
    ctx.setLineDash([]);
  }

  if (state.cursorA >= 0 && state.cursorB >= 0) {
    dom.statusBarTop.textContent = `ΔT = ${Math.abs(state.cursorB - state.cursorA)} ns`;
  }
}

function _drawSignal(ctx, sigData, y0, rowH, totalW, endTime, pxPerNs) {
  const changes = sigData.changes;
  const width = sigData.width;
  const is1bit = width === 1;
  const highY = y0 + 4, lowY = y0 + rowH - 6, midY = (highY + lowY) / 2;

  if (!changes?.length) return;
  const pts = [...changes.map(([t,v]) => ({t, v})), {t: endTime + 1, v: changes[changes.length-1][1]}];

  ctx.lineWidth = 1.5;
  ctx.font = "9px monospace";
  ctx.textAlign = "center";

  for (let i = 0; i < pts.length - 1; i++) {
    const {t: t0, v: v0} = pts[i], {t: t1} = pts[i+1];
    const x0 = t0 * pxPerNs, x1 = t1 * pxPerNs;
    if (x1 < 0 || x0 > totalW) continue;

    const isX = typeof v0 === "string" && v0.includes("x");
    const isZ = typeof v0 === "string" && v0.includes("z");

    if (is1bit) {
      const isHigh = !isX && !isZ && v0 === "1";
      const y = isHigh ? highY : lowY;
      ctx.strokeStyle = isX ? "#fb923c" : isZ ? "#60a5fa" : "#7c6af7";
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      if (i > 0) {
        const prevY = pts[i-1].v === "1" ? highY : lowY;
        ctx.beginPath(); ctx.moveTo(x0, prevY); ctx.lineTo(x0, y); ctx.stroke();
      }
    } else {
      const mw = 5, lx1 = x0 + mw, rx0 = x1 - mw;
      ctx.strokeStyle = isX ? "#fb923c" : isZ ? "#60a5fa" : "#7c6af7";
      ctx.fillStyle = isX ? "rgba(251,146,60,0.15)" : isZ ? "rgba(96,165,250,0.12)" : "rgba(124,106,247,0.12)";
      ctx.beginPath();
      ctx.moveTo(x0, midY); ctx.lineTo(lx1, highY);
      ctx.lineTo(Math.max(lx1, rx0), highY);
      ctx.lineTo(x1, midY);
      ctx.lineTo(Math.max(lx1, rx0), lowY);
      ctx.lineTo(lx1, lowY);
      ctx.closePath();
      ctx.fill(); ctx.stroke();

      const segW = x1 - x0;
      if (segW > 18) {
        ctx.fillStyle = isX ? "#fb923c" : isZ ? "#60a5fa" : "#e2e2f0";
        const dv = fmtVal(v0, width, state.radix);
        ctx.fillText(_truncate(dv, segW - 10, ctx), (x0 + x1) / 2, midY + 3.5);
      }
    }
  }
}

function fmtVal(binStr, width, radix) {
  if (typeof binStr !== "string") binStr = String(binStr);
  if (binStr === "x" || binStr === "z") return binStr.toUpperCase();
  if (binStr.includes("x") || binStr.includes("z")) return binStr;
  if (radix === "bin") return "b" + binStr.padStart(width, "0");
  const n = parseInt(binStr, 2);
  if (isNaN(n)) return binStr;
  if (radix === "hex") return "0x" + n.toString(16).toUpperCase().padStart(Math.ceil(width/4), "0");
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
  state.zoom = (w - 40) / (end * 0.8) / 1;
  drawWaveform();
}

// =========================================================================
// Canvas インタラクション
// =========================================================================

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

// =========================================================================
// 基数切替
// =========================================================================

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
    // 許可ルート一覧を取得
    const roots = await api.browseRoots();
    if (roots.roots.length > 0) {
      state.browserPath = roots.roots[0];
    }
  }
  await browseTo(state.browserPath);
}

async function browseTo(path) {
  state.browserPath = path;
  try {
    const data = await api.browse(path);
    renderBrowser(data.path, data.entries);
  } catch (e) {
    log(`ブラウザエラー: ${e.message}`, "log-error");
  }
}

function renderBrowser(currentPath, entries) {
  // パンくず
  const parts = currentPath.replace(/\\/g, "/").split("/").filter(Boolean);
  dom.browserBreadcrumb.innerHTML = parts.map((p, i) => {
    const partPath = "/" + parts.slice(0, i + 1).join("/");
    return `<a href="#" data-path="${partPath}" class="breadcrumb-link" style="color:#7c6af7; text-decoration:none;">${p}</a>`;
  }).join(" / ");
  dom.browserBreadcrumb.querySelectorAll(".breadcrumb-link").forEach(a => {
    a.addEventListener("click", e => { e.preventDefault(); browseTo(a.dataset.path); });
  });

  // エントリ一覧
  dom.browserBody.innerHTML = "";
  // 上の階層
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
      if (entry.type === "dir") {
        browseTo(entry.path);
        return;
      }
      document.querySelectorAll("#browser-body .fb-entry").forEach(el2 => el2.classList.remove("selected"));
      el.classList.add("selected");
      state.browserSelected = entry;
      dom.browserSelectedPath.textContent = entry.path;
    });

    el.addEventListener("dblclick", () => {
      if (entry.type === "dir") return;
      state.browserSelected = entry;
      confirmBrowserOpen();
    });

    dom.browserBody.appendChild(el);
  });
}

function closeBrowser() {
  dom.fileBrowserOverlay.style.display = "none";
}

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

// =========================================================================
// プロジェクト保存
// =========================================================================

async function saveProject() {
  const projPath = `${window.location.origin}` + "/deltasim.project.yaml";
  // デフォルトはカレントディレクトリ
  try {
    await api.projectSave("deltasim.project.yaml", {
      sv_files: state.project.svFiles.map(f => f.path),
      top: state.project.top,
      spec: state.project.specFile?.path ?? null,
      params: {},
    });
    log("プロジェクトを deltasim.project.yaml に保存しました", "log-info");
  } catch (e) {
    log(`プロジェクト保存エラー: ${e.message}`, "log-error");
  }
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

window.addEventListener("resize", () => { if (state.waveformData) drawWaveform(); });

// =========================================================================
// 初期化
// =========================================================================

(async function init() {
  initEditor();
  setStatus("ready", "idle");
  dom.btnStop.disabled = true;
  log("deltasim GUI v2 起動", "log-info");
  log("サンプルを読み込むか、「📂 開く」でファイルを選択してください。", "log-line");
  await loadExamplesList();

  // 環境変数経由の初期プロジェクト（CLI引数から）を取得
  try {
    const res = await fetch("/api/initial-project");
    if (res.ok) {
      const proj = await res.json();
      if (proj?.sv_files?.length) {
        state.project.svFiles = proj.sv_files.map(p => ({ path: p }));
        state.project.specFile = proj.spec ? { path: proj.spec } : null;
        state.project.top = proj.top;
        renderFileList();
        if (proj.sv_files.length > 0) {
          await loadModule(proj.sv_files, proj.top);
          await openFileInEditor(proj.sv_files[0]);
          log("CLI引数からプロジェクトを読み込みました", "log-info");
        }
      }
    }
  } catch (_) {}
})();
