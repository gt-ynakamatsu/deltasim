/**
 * deltasim GUI — メインアプリケーション
 *
 * 設計方針:
 *  - ビルドツール不要の vanilla JS (ES2020+)
 *  - Canvas で波形描画（WebGL へ移行可能な設計）
 *  - 値は 4 値文字列で受け取り、基数変換は GUI 側で行う
 *  - GUIはロジック判定を持たない薄い層
 */

"use strict";

// =========================================================================
// 状態
// =========================================================================

const state = {
  project: {
    svFiles: [],     // { path, name }[]
    specFile: null,  // { path, name }
    top: null,
  },
  module: null,      // { module, ports, params, errors }
  testResult: null,  // API /api/test の結果
  waveformData: null,// API /api/waveform の結果
  vcdPath: null,
  selectedCase: null,
  selectedSignals: [],// 波形ビューアに表示する信号名[]
  radix: "hex",       // "bin" | "hex" | "dec"
  zoom: 1.0,
  panX: 0,
  cursorA: -1,
  cursorB: -1,
  running: false,
  seed: "",
};

// =========================================================================
// DOM refs
// =========================================================================

const $ = id => document.getElementById(id);

const dom = {
  toolbar: $("toolbar"),
  btnLoadExamples: $("btn-load-examples"),
  btnOpenFiles: $("btn-open-files"),
  btnRunTest: $("btn-run-test"),
  btnStop: $("btn-stop"),
  btnZoomIn: $("btn-zoom-in"),
  btnZoomOut: $("btn-zoom-out"),
  btnZoomFit: $("btn-zoom-fit"),
  seedInput: $("seed-input"),
  statusBarTop: $("status-bar-top"),
  // Sidebar
  fileList: $("file-list"),
  specList: $("spec-list"),
  moduleSelect: $("module-select"),
  exampleList: $("example-list"),
  // Editor
  editorPre: $("editor-content"),
  // Waveform
  signalNames: $("signal-names"),
  waveformCanvas: $("waveform-canvas"),
  waveformWrapper: $("waveform-canvas-wrapper"),
  // Results
  summaryBar: $("summary-bar"),
  resultList: $("result-list"),
  // Log
  logOutput: $("log-output"),
  // Statusbar
  statusbar: $("statusbar"),
  statusText: $("status-text"),
  dropOverlay: $("drop-overlay"),
};

// =========================================================================
// API helpers
// =========================================================================

async function apiFetch(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
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
  examples: () => apiFetch("GET", "/api/examples"),
  load: (files, top) => apiFetch("POST", "/api/load", { files, top }),
  test: (sv_files, spec, top, seed) => apiFetch("POST", "/api/test", {
    sv_files, spec, top: top ?? null, seed: seed !== "" ? parseInt(seed) : null,
  }),
  waveform: (path, t_start, t_end, signals) => {
    const params = new URLSearchParams({ path, t_start: t_start ?? 0 });
    if (t_end != null) params.append("t_end", t_end);
    if (signals && signals.length) params.append("signals", signals.join(","));
    return apiFetch("GET", `/api/waveform?${params}`);
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

// =========================================================================
// ステータスバー
// =========================================================================

function setStatus(text, cls = "idle") {
  dom.statusbar.className = cls;
  dom.statusText.textContent = text;
}

// =========================================================================
// サイドバー — ファイル一覧
// =========================================================================

function renderFileList() {
  dom.fileList.innerHTML = "";
  state.project.svFiles.forEach(f => {
    const li = document.createElement("li");
    li.className = "file-item";
    li.innerHTML = `<span class="icon">📄</span><span>${f.name}</span>`;
    li.title = f.path;
    li.addEventListener("click", () => loadFileContent(f.path));
    dom.fileList.appendChild(li);
  });

  // Spec file
  dom.specList.innerHTML = "";
  if (state.project.specFile) {
    const li = document.createElement("li");
    li.className = "file-item active";
    li.innerHTML = `<span class="icon">📋</span><span>${state.project.specFile.name}</span>`;
    dom.specList.appendChild(li);
  }

  // Module selector
  dom.moduleSelect.innerHTML = "";
  if (state.module) {
    const opt = document.createElement("option");
    opt.value = state.module.module;
    opt.textContent = state.module.module;
    dom.moduleSelect.appendChild(opt);
  }
}

async function loadFileContent(path) {
  try {
    const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
    // /api/file は未実装のためフォールバック（ファイル名だけ表示）
    if (res.ok) {
      const text = await res.text();
      dom.editorPre.textContent = text;
    }
  } catch (_) {
    dom.editorPre.textContent = `// ${path}\n// (ファイル内容プレビューはM5対応予定)`;
  }
}

// =========================================================================
// サンプルプロジェクト読み込み
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
    if (!data.examples.length) {
      dom.exampleList.textContent = "(サンプルなし)";
    }
  } catch (e) {
    log(`サンプル一覧取得エラー: ${e.message}`, "log-error");
  }
}

async function loadExample(ex) {
  logClear();
  log(`サンプル読み込み: ${ex.name}`, "log-info");

  state.project.svFiles = ex.sv_files.map(p => ({ path: p, name: p.split(/[\\/]/).pop() }));
  state.project.specFile = { path: ex.spec, name: ex.spec.split(/[\\/]/).pop() };
  state.project.top = ex.top;

  renderFileList();

  try {
    const data = await api.load(ex.sv_files, ex.top);
    state.module = data;
    dom.editorPre.textContent = `// モジュール: ${data.module}\n// ポート:\n` +
      data.ports.map(p => `//   ${p.direction.padEnd(8)} [${p.width-1}:0] ${p.name}`).join("\n");
    log(`ロード成功: ${data.module} (${data.ports.length} ポート)`, "log-info");
    renderFileList();
    setStatus(`${data.module} ロード済み`, "idle");
  } catch (e) {
    log(`ロードエラー: ${e.message}`, "log-error");
    setStatus("ロードエラー", "error");
  }
}

// =========================================================================
// テスト実行
// =========================================================================

async function runTest() {
  if (state.running) return;
  if (!state.project.svFiles.length) {
    log("SV ファイルが選択されていません", "log-error");
    return;
  }
  if (!state.project.specFile) {
    log("スペックファイルが選択されていません", "log-error");
    return;
  }

  state.running = true;
  dom.btnRunTest.disabled = true;
  dom.btnStop.disabled = false;
  logClear();
  setStatus("テスト実行中…", "running");
  log("▶ テスト実行開始", "log-info");

  clearResults();
  clearWaveform();

  try {
    const svPaths = state.project.svFiles.map(f => f.path);
    const specPath = state.project.specFile.path;
    const seed = dom.seedInput.value.trim();
    const top = state.project.top;

    const result = await api.test(svPaths, specPath, top, seed);
    state.testResult = result;
    state.vcdPath = result.vcd_path;

    renderResults(result);

    const summary = result.summary;
    const statusCls = summary.fail > 0 ? "fail" : summary.error > 0 ? "error" : "pass";
    const statusMsg = `完了: ${summary.pass} PASS / ${summary.fail} FAIL / ${summary.error} ERROR`;
    setStatus(statusMsg, statusCls);
    log(`✓ ${statusMsg}`, statusCls === "pass" ? "log-pass" : "log-fail");

    // 波形ロード
    if (result.vcd_path) {
      log(`波形読み込み: ${result.vcd_path}`, "log-info");
      await loadWaveform(result.vcd_path);
    }

    // ログ出力
    if (result.cases?.length > 0) {
      const c = result.cases[0];
      (c.log ?? []).forEach(l => log(l));
    }
  } catch (e) {
    log(`エラー: ${e.message}`, "log-error");
    setStatus("実行エラー", "error");
  } finally {
    state.running = false;
    dom.btnRunTest.disabled = false;
    dom.btnStop.disabled = true;
  }
}

// =========================================================================
// 結果表示
// =========================================================================

function clearResults() {
  dom.resultList.innerHTML = "";
  dom.summaryBar.innerHTML = "";
  state.selectedCase = null;
}

function renderResults(result) {
  const s = result.summary;

  // サマリーバッジ
  dom.summaryBar.innerHTML = [
    ["pass",  s.pass,  "badge-pass"],
    ["fail",  s.fail,  "badge-fail"],
    ["error", s.error, "badge-error"],
    ["skip",  s.skip,  "badge-skip"],
  ].map(([label, count, cls]) =>
    `<span class="count-badge ${cls}">${count} ${label.toUpperCase()}</span>`
  ).join("");

  // ケース一覧
  dom.resultList.innerHTML = "";
  result.cases.forEach((c, i) => {
    const card = document.createElement("div");
    card.className = "case-card";
    card.dataset.idx = i;

    const dur = c.duration_sec != null ? `${c.duration_sec.toFixed(2)}s` : "";
    const seedStr = c.seed != null ? `seed=${c.seed}` : "";
    card.innerHTML = `
      <div class="case-header">
        <div class="status-dot dot-${c.status}"></div>
        <div class="case-name">${escapeHtml(c.name)}</div>
        <div class="case-meta">${dur}</div>
      </div>
      <div class="case-detail">
        ${seedStr ? `<div class="failure-label">シード: ${seedStr}</div>` : ""}
        ${c.message ? `<div class="failure-label">${escapeHtml(c.message)}</div>` : ""}
        ${renderFailures(c.failures)}
      </div>
    `;

    card.addEventListener("click", () => selectCase(card, c));
    dom.resultList.appendChild(card);

    // 最初のケースを自動選択
    if (i === 0) selectCase(card, c);
  });
}

function renderFailures(failures) {
  if (!failures?.length) return '<div class="failure-label">✓ 全チェック合格</div>';
  return failures.map(f => `
    <div class="failure-item">
      cycle=${f.cycle} <b>${escapeHtml(String(f.signal))}</b>
      期待: <b>${escapeHtml(String(f.expected))}</b>
      実測: <b>${escapeHtml(String(f.actual))}</b>
    </div>
  `).join("");
}

function selectCase(card, c) {
  document.querySelectorAll(".case-card").forEach(el => el.classList.remove("selected"));
  card.classList.add("selected");
  state.selectedCase = c;

  // 波形カーソルをジャンプ（最初の失敗サイクルへ）
  if (c.failures?.length > 0 && state.waveformData) {
    const firstFail = c.failures[0];
    state.cursorA = firstFail.time_ns ?? firstFail.cycle * 10;
    drawWaveform();
  }
}

// =========================================================================
// 波形ビューア
// =========================================================================

const WAVE_ROW_H = 28;
const WAVE_RULER_H = 24;

async function loadWaveform(vcdPath) {
  try {
    const data = await api.waveform(vcdPath);
    state.waveformData = data;
    state.selectedSignals = Object.keys(data.signals).slice(0, 20); // 最大20信号
    state.zoom = 1.0;
    state.panX = 0;
    state.cursorA = -1;
    state.cursorB = -1;
    renderSignalNames();
    drawWaveform();
    log(`波形ロード: ${state.selectedSignals.length} 信号, 終端=${data.end_time}ns`, "log-info");
  } catch (e) {
    log(`波形ロードエラー: ${e.message}`, "log-error");
  }
}

function clearWaveform() {
  state.waveformData = null;
  state.selectedSignals = [];
  dom.signalNames.innerHTML = "";
  const canvas = dom.waveformCanvas;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function renderSignalNames() {
  dom.signalNames.innerHTML = "";
  state.selectedSignals.forEach((name, i) => {
    const row = document.createElement("div");
    row.className = "sig-name-row";
    row.textContent = name;
    row.title = name;
    row.addEventListener("click", () => {
      row.classList.toggle("highlighted");
    });
    dom.signalNames.appendChild(row);
  });
}

function drawWaveform() {
  if (!state.waveformData) return;

  const wrapper = dom.waveformWrapper;
  const canvas = dom.waveformCanvas;
  const sigs = state.selectedSignals;
  if (!sigs.length) return;

  const endTime = state.waveformData.end_time || 1;
  const px_per_ns = state.zoom * 0.8;
  const totalW = Math.max(wrapper.clientWidth, Math.ceil(endTime * px_per_ns) + 40);
  const totalH = WAVE_RULER_H + sigs.length * WAVE_ROW_H + 4;

  canvas.width = totalW;
  canvas.height = totalH;
  canvas.style.width = totalW + "px";
  canvas.style.height = totalH + "px";

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, totalW, totalH);

  // 背景
  ctx.fillStyle = getComputedStyle(document.documentElement)
    .getPropertyValue("--waveform-bg").trim() || "#12121f";
  ctx.fillRect(0, 0, totalW, totalH);

  // グリッド
  const gridColor = "#2a2a40";
  const step = _niceStep(endTime, Math.floor(totalW / 60));
  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 1;
  for (let t = 0; t <= endTime + step; t += step) {
    const x = Math.round(t * px_per_ns) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, totalH);
    ctx.stroke();
  }

  // ルーラー
  ctx.fillStyle = "#3d3d5c";
  ctx.fillRect(0, 0, totalW, WAVE_RULER_H);
  ctx.fillStyle = "#a3a3b4";
  ctx.font = "10px monospace";
  ctx.textAlign = "left";
  for (let t = 0; t <= endTime + step; t += step) {
    const x = Math.round(t * px_per_ns);
    ctx.fillText(`${t}ns`, x + 3, 15);
  }

  // 信号波形
  sigs.forEach((name, si) => {
    const sigData = state.waveformData.signals[name];
    if (!sigData) return;
    const y0 = WAVE_RULER_H + si * WAVE_ROW_H;
    _drawSignal(ctx, sigData, y0, WAVE_ROW_H, totalW, endTime, px_per_ns, name);
  });

  // カーソル A
  if (state.cursorA >= 0) {
    const x = state.cursorA * px_per_ns;
    ctx.strokeStyle = "#f9c74f";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, totalH);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // カーソル B
  if (state.cursorB >= 0) {
    const x = state.cursorB * px_per_ns;
    ctx.strokeStyle = "#60a5fa";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, totalH);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // 差分表示
  if (state.cursorA >= 0 && state.cursorB >= 0) {
    const diff = Math.abs(state.cursorB - state.cursorA);
    dom.statusBarTop.textContent = `ΔT = ${diff} ns`;
  }
}

function _drawSignal(ctx, sigData, y0, rowH, totalW, endTime, pxPerNs, name) {
  const changes = sigData.changes;
  const width = sigData.width;
  const is1bit = width === 1;

  const highY = y0 + 4;
  const lowY  = y0 + rowH - 6;
  const midY  = (highY + lowY) / 2;

  ctx.lineWidth = 1.5;
  ctx.font = "9px monospace";
  ctx.textAlign = "center";

  if (!changes || changes.length === 0) return;

  // 変化リストを (time, value) として展開
  const pts = [];
  for (let i = 0; i < changes.length; i++) {
    pts.push({ t: changes[i][0], v: changes[i][1] });
  }
  // 終端に番哨を追加
  pts.push({ t: endTime + 1, v: pts[pts.length - 1]?.v ?? "0" });

  for (let i = 0; i < pts.length - 1; i++) {
    const { t: t0, v: v0 } = pts[i];
    const { t: t1 }       = pts[i + 1];
    const x0 = t0 * pxPerNs;
    const x1 = t1 * pxPerNs;
    if (x1 < 0 || x0 > totalW) continue;

    const hasX = typeof v0 === "string" && (v0.includes("x") || v0 === "x");
    const hasZ = typeof v0 === "string" && (v0.includes("z") || v0 === "z");

    if (is1bit) {
      const isHigh = !hasX && !hasZ && v0 === "1";
      const isX    = hasX;
      const isZ    = hasZ;
      const y = isHigh ? highY : lowY;

      ctx.strokeStyle = isX ? "#fb923c" : isZ ? "#60a5fa" : "#7c6af7";
      ctx.beginPath();
      ctx.moveTo(x0, y);
      ctx.lineTo(x1, y);
      ctx.stroke();

      // エッジ
      if (i > 0) {
        const prevY = pts[i - 1].v === "1" ? highY : lowY;
        ctx.beginPath();
        ctx.moveTo(x0, prevY);
        ctx.lineTo(x0, y);
        ctx.stroke();
      }
    } else {
      // 多ビット: 台形スタイル
      const midW = 6;
      const lx0 = x0, lx1 = x0 + midW;
      const rx0 = x1 - midW, rx1 = x1;

      ctx.strokeStyle = hasX ? "#fb923c" : hasZ ? "#60a5fa" : "#7c6af7";
      ctx.fillStyle = hasX ? "rgba(251,146,60,0.15)" : hasZ ? "rgba(96,165,250,0.12)" : "rgba(124,106,247,0.12)";

      ctx.beginPath();
      ctx.moveTo(lx0, midY);
      ctx.lineTo(lx1, highY);
      ctx.lineTo(Math.max(lx1, rx0), highY);
      ctx.lineTo(rx1, midY);
      ctx.lineTo(Math.max(lx1, rx0), lowY);
      ctx.lineTo(lx1, lowY);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      // 値テキスト
      const segW = x1 - x0;
      if (segW > 20) {
        ctx.fillStyle = hasX ? "#fb923c" : hasZ ? "#60a5fa" : "#e2e2f0";
        const displayVal = _formatValue(v0, width, state.radix);
        ctx.fillText(
          _truncate(displayVal, segW - 10, ctx),
          (x0 + x1) / 2,
          midY + 3.5,
        );
      }
    }
  }
}

function _formatValue(binStr, width, radix) {
  if (typeof binStr !== "string") binStr = String(binStr);

  // X/Z チェック
  if (binStr === "x" || binStr === "z") return binStr.toUpperCase();
  if (binStr.includes("x") || binStr.includes("z")) {
    // 混在 → そのまま表示
    if (radix === "bin") return binStr;
    return binStr; // hex 変換しない
  }

  if (radix === "bin") {
    return "b" + binStr.padStart(width, "0");
  }

  // 数値化
  const n = parseInt(binStr, 2);
  if (isNaN(n)) return binStr;

  if (radix === "hex") {
    const digits = Math.ceil(width / 4);
    return "0x" + n.toString(16).toUpperCase().padStart(digits, "0");
  }
  if (radix === "dec") {
    return String(n);
  }
  return binStr;
}

function _truncate(str, maxPx, ctx) {
  if (ctx.measureText(str).width <= maxPx) return str;
  while (str.length > 2 && ctx.measureText(str + "…").width > maxPx) {
    str = str.slice(0, -1);
  }
  return str + "…";
}

function _niceStep(endTime, maxTicks) {
  if (maxTicks <= 0) maxTicks = 1;
  const raw = endTime / maxTicks;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const mult of [1, 2, 5, 10]) {
    if (mag * mult >= raw) return Math.max(1, mag * mult);
  }
  return Math.max(1, mag * 10);
}

// =========================================================================
// ズーム / パン
// =========================================================================

function zoomIn() {
  state.zoom = Math.min(state.zoom * 1.5, 50);
  drawWaveform();
}

function zoomOut() {
  state.zoom = Math.max(state.zoom / 1.5, 0.05);
  drawWaveform();
}

function zoomFit() {
  if (!state.waveformData) return;
  const endTime = state.waveformData.end_time || 1;
  const wrapW = dom.waveformWrapper.clientWidth || 400;
  state.zoom = (wrapW - 40) / (endTime * 0.8) / 1;
  state.panX = 0;
  drawWaveform();
}

// =========================================================================
// Canvas インタラクション
// =========================================================================

(function setupCanvasEvents() {
  let dragging = false;
  let dragStartX = 0;
  let dragStartPan = 0;

  dom.waveformCanvas.addEventListener("mousedown", e => {
    if (e.button === 0) {
      dragging = true;
      dragStartX = e.clientX;
      dragStartPan = state.panX;
    }
  });

  dom.waveformCanvas.addEventListener("mousemove", e => {
    if (dragging) {
      const dx = e.clientX - dragStartX;
      state.panX = dragStartPan + dx;
      dom.waveformWrapper.scrollLeft -= dx * 0.5;
    }
  });

  dom.waveformCanvas.addEventListener("mouseup", e => {
    dragging = false;
    if (Math.abs(e.clientX - dragStartX) < 4) {
      // クリック → カーソルA
      const rect = dom.waveformCanvas.getBoundingClientRect();
      const xInCanvas = e.clientX - rect.left;
      const pxPerNs = state.zoom * 0.8;
      if (e.shiftKey) {
        state.cursorB = Math.round(xInCanvas / pxPerNs);
      } else {
        state.cursorA = Math.round(xInCanvas / pxPerNs);
      }
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
  document.querySelectorAll(".radix-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.radix === r);
  });
  drawWaveform();
}

// =========================================================================
// ファイルオープン（input[type=file]）
// =========================================================================

function openFileDialog() {
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.accept = ".sv,.v,.yaml,.yml";
  input.addEventListener("change", () => {
    const files = Array.from(input.files);
    if (!files.length) return;

    const svFiles = files.filter(f => f.name.endsWith(".sv") || f.name.endsWith(".v"));
    const specFiles = files.filter(f => f.name.endsWith(".yaml") || f.name.endsWith(".yml"));

    // ブラウザからはローカルパスが取れないためメッセージを表示
    log(
      "ブラウザからローカルファイルのパスを取得できません。" +
      "サンプルを使うか、コマンドラインから deltasim gui --open で起動してください。",
      "log-error"
    );
  });
  input.click();
}

// =========================================================================
// ドラッグ＆ドロップ（DnD）
// =========================================================================

document.addEventListener("dragover", e => {
  e.preventDefault();
  dom.dropOverlay.classList.add("visible");
});

document.addEventListener("dragleave", e => {
  if (!e.relatedTarget || e.relatedTarget === document.documentElement) {
    dom.dropOverlay.classList.remove("visible");
  }
});

document.addEventListener("drop", e => {
  e.preventDefault();
  dom.dropOverlay.classList.remove("visible");
  log(
    "DnD はブラウザのセキュリティ制限でローカルパスを取得できません。" +
    "サンプルから読み込んでください。",
    "log-error"
  );
});

// =========================================================================
// ユーティリティ
// =========================================================================

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// =========================================================================
// イベント接続
// =========================================================================

dom.btnLoadExamples?.addEventListener("click", loadExamplesList);
dom.btnOpenFiles?.addEventListener("click", openFileDialog);
dom.btnRunTest?.addEventListener("click", runTest);
dom.btnStop?.addEventListener("click", () => {
  // キャンセルトークンは M5 待ち
  log("停止は M5 の公開 API 実装後に対応します", "log-error");
});
dom.btnZoomIn?.addEventListener("click", zoomIn);
dom.btnZoomOut?.addEventListener("click", zoomOut);
dom.btnZoomFit?.addEventListener("click", zoomFit);

document.querySelectorAll(".radix-btn").forEach(btn => {
  btn.addEventListener("click", () => setRadix(btn.dataset.radix));
});

// リサイズ時に再描画
window.addEventListener("resize", () => {
  if (state.waveformData) drawWaveform();
});

// =========================================================================
// 初期化
// =========================================================================

(async function init() {
  setStatus("ready", "idle");
  dom.btnStop.disabled = true;
  log("deltasim GUI 起動", "log-info");
  log("「サンプル読み込み」ボタンを押してサンプルプロジェクトを選択してください。", "log-line");
  await loadExamplesList();
})();
