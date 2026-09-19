/* xiaoai-llm admin console (vanilla JS, no framework) */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const PERSONA_TEMPLATES = {
  generic: "你是一个友善、准确的语音助手。请使用适合直接播报的简洁中文回答，控制在 100 字以内，不要使用 Markdown 表格，也不要读出网址。",
  study: "你是一位耐心的学习辅导老师，面向中小学生讲解知识点。请用通俗易懂的中文分步骤讲解，每次回答不超过 150 字，结尾可以留一个引导思考的小问题。",
  funny: "你是一个幽默风趣的朋友，说话轻松俏皮但不过分夸张。回答保持简短有趣，适合语音播报，避免低俗内容。",
  kid: "你是陪伴小朋友的语音伙伴，用词简单、语气温暖，回答不超过 80 字，注意内容安全，不涉及恐怖和暴力话题。",
  work: "你是一位务实的职场顾问，回答结构清晰、直击要点，先给结论再给 1-2 条可执行建议，总长度控制在 120 字以内。",
};

let cfg = null;
let discovered = [];
let logCursor = 0;
let logTimer = null;
let statusTimer = null;

/* ------------------------------------------------------------------ api */

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (res.status === 401) {
    showAuth("login");
    throw new Error("请先登录管理页面");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `请求失败 (${res.status})`);
  return data;
}

const GET = (path) => api(path);
const POST = (path, body) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });
const PUT = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body) });
const DEL = (path) => api(path, { method: "DELETE" });

function toast(message, type = "") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast ${type}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

/* ----------------------------------------------------------------- auth */

async function boot() {
  const status = await GET("/api/auth/status");
  if (!status.initialized) return showAuth("setup");
  if (!status.authenticated) return showAuth("login");
  await enterApp();
}

function showAuth(mode) {
  $("#app").classList.add("hidden");
  $("#auth-screen").classList.remove("hidden");
  $("#auth-title").textContent = mode === "setup" ? "设置管理密码" : "登录管理台";
  $("#auth-hint").textContent =
    mode === "setup" ? "首次使用请为管理页面设置访问密码。" : "请输入管理密码访问配置页面。";
  $("#auth-submit").textContent = mode === "setup" ? "设置并进入" : "登录";
  $("#auth-submit").dataset.mode = mode;
  $("#auth-setup-token").classList.toggle("hidden", mode !== "setup");
  // 仅登录模式显示"忘记密码"入口；setup 模式下未设置密码，应直接走 setup 流程
  $("#auth-reset-link").classList.toggle("hidden", mode !== "login");
  $("#auth-reset-panel").classList.add("hidden");
  $("#auth-reset-error").textContent = "";
  $("#reset-request-status").textContent = "";
}

async function submitAuth() {
  const password = $("#auth-password").value;
  if (password.length < 6) {
    $("#auth-error").textContent = "密码至少 6 位";
    return;
  }
  try {
    const mode = $("#auth-submit").dataset.mode;
    await POST(mode === "setup" ? "/api/auth/setup" : "/api/auth/login", {
      password,
      setup_token: mode === "setup" ? $("#auth-setup-token").value : "",
    });
    $("#auth-error").textContent = "";
    $("#auth-password").value = "";
    await enterApp();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  }
}

async function requestPasswordReset() {
  $("#auth-reset-error").textContent = "";
  $("#reset-request-status").textContent = "申请中…";
  try {
    const res = await POST("/api/auth/reset-password/request", {});
    $("#reset-request-status").textContent = res.message || "请到服务端日志查看令牌";
  } catch (err) {
    $("#reset-request-status").textContent = "";
    $("#auth-reset-error").textContent = err.message;
  }
}

async function confirmPasswordReset() {
  $("#auth-reset-error").textContent = "";
  const token = $("#auth-reset-token").value.trim();
  const newPassword = $("#auth-reset-new").value;
  if (!token) {
    $("#auth-reset-error").textContent = "请输入一次性令牌";
    return;
  }
  if (newPassword.length < 6) {
    $("#auth-reset-error").textContent = "新密码至少 6 位";
    return;
  }
  try {
    await POST("/api/auth/reset-password/confirm", {
      token,
      new_password: newPassword,
    });
    $("#auth-reset-token").value = "";
    $("#auth-reset-new").value = "";
    $("#auth-reset-panel").classList.add("hidden");
    $("#reset-request-status").textContent = "";
    $("#auth-error").textContent = "";
    // 切回登录模式让用户用新密码登录
    showAuth("login");
    $("#auth-hint").textContent = "密码已重置，请使用新密码登录。";
    toast("密码已重置，请用新密码登录", "success");
  } catch (err) {
    $("#auth-reset-error").textContent = err.message;
  }
}

async function enterApp() {
  $("#auth-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  await loadConfig();
  startStatusPolling();
  route();
}

/* --------------------------------------------------------------- config */

async function loadConfig() {
  cfg = await GET("/api/config");
}

async function saveConfig() {
  try {
    await PUT("/api/config", cfg);
    await loadConfig();
    toast("配置已保存，点击右上角“应用配置”生效", "ok");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function applyConfig() {
  const btn = $("#btn-apply");
  btn.disabled = true;
  btn.textContent = "应用中…";
  try {
    const result = await POST("/api/config/apply");
    toast(`配置已应用：${result.devices} 台设备监听中，米家：${result.mijia}`, "ok");
    refreshStatus();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "应用配置";
  }
}

/* ---------------------------------------------------------------- router */

const SCREENS = ["overview", "account", "devices", "llm", "persona", "tts", "web-search", "logs"];

function route() {
  const name = (location.hash.replace("#/", "") || "overview").split("?")[0];
  const target = SCREENS.includes(name) ? name : "overview";
  $$(".screen").forEach((s) => s.classList.toggle("hidden", s.dataset.screen !== target));
  $$("#nav a[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === target));
  stopLogPolling();
  const loaders = { overview: renderOverview, account: renderAccount, devices: renderDevicesScreen, llm: renderLLM, persona: renderPersona, tts: renderTTS, "web-search": renderWebSearch, logs: startLogPolling };
  loaders[target]?.();
}

window.addEventListener("hashchange", route);

/* -------------------------------------------------------------- overview */

async function refreshStatus() {
  try {
    const [status, mijia, accounts, allDevices] = await Promise.all([
      GET("/api/status"),
      GET("/api/mijia/status"),
      GET("/api/mijia/accounts").catch(() => []),
      GET("/api/devices").catch(() => []),
    ]);
    const accountsList = Array.isArray(accounts) ? accounts : [];
    const devices = Array.isArray(allDevices) ? allDevices : [];
    const dot = $("#status-dot");
    const text = $("#status-text");
    if (status.needs_relogin) {
      dot.className = "dot dot-warn";
      text.textContent = "米家需要重新登录";
    } else if (status.mijia_authenticated && status.listener_running) {
      dot.className = "dot dot-on";
      text.textContent = `运行中 · ${status.device_count} 台设备`;
    } else {
      dot.className = "dot dot-off";
      text.textContent = status.mijia_authenticated ? "监听未运行" : "米家未登录";
    }
    if (!$('[data-screen="overview"]').classList.contains("hidden")) {
      renderOverviewCards(status, mijia, accountsList, devices);
    }
    return { status, mijia, accounts: accountsList, devices };
  } catch {
    /* ignore transient errors */
  }
}

function startStatusPolling() {
  refreshStatus();
  clearInterval(statusTimer);
  statusTimer = setInterval(refreshStatus, 5000);
}

async function renderOverview() {
  refreshStatus();
}

function renderOverviewCards(status, mijia, accounts, deviceDetails) {
  const devices = status.devices || [];
  const okCount = devices.filter((d) => d.listening).length;
  const latencies = devices
    .map((d) => ({
      ms: typeof d.last_llm_ms === "number" ? d.last_llm_ms : null,
      at: typeof d.last_llm_at === "number" ? d.last_llm_at : null,
    }))
    .filter((x) => x.ms !== null);
  const now = Date.now() / 1000;
  const latencyMs = latencies.length ? Math.max(...latencies.map((x) => x.ms)) : null;
  const lastAt = latencies.length ? Math.max(...latencies.map((x) => x.at || 0)) : null;
  const latencyText =
    latencyMs === null
      ? "尚无调用"
      : lastAt
        ? `${latencyMs} ms · ${formatRelativeTime(now - lastAt)}`
        : `${latencyMs} ms`;
  const latencyCls = latencyMs === null ? "warn" : latencyMs > 1500 ? "warn" : "";

  // Build "米家账号" card value: list of "<登录用户> · 状态"
  const accountsList = accounts || [];
  const accountSummary = accountsList.length
    ? accountsList
        .map((a) => {
          const ident = a.username || a.user_id || a.name || a.id;
          const status = a.authenticated
            ? `<span class="badge full">已登录</span>`
            : a.needs_relogin
              ? `<span class="badge offline">需重新登录</span>`
              : `<span class="badge">未登录</span>`;
          return `<div class="acct-line"><strong>${escapeHtml(ident)}</strong>${status}</div>`;
        })
        .join("")
    : "未添加账号";

  const cards = [
    {
      k: "米家账号",
      v: accountsList.length
        ? `${accountsList.length} 个 · ${accountsList.filter((a) => a.authenticated).length} 已登录`
        : "未添加",
      cls: accountsList.some((a) => a.authenticated) ? "ok" : "warn",
      extra: accountSummary,
    },
    { k: "消息监听", v: status.listener_running ? "运行中" : "已停止", cls: status.listener_running ? "ok" : "bad" },
    { k: "绑定设备", v: `${okCount} / ${status.device_count} 在线监听`, cls: okCount ? "ok" : "warn" },
    { k: "最近 LLM 耗时", v: latencyText, cls: latencyCls },
    {
      k: "当前账号",
      v: mijia.username_masked ? mijia.username_masked : "未配置",
      cls: mijia.username_masked ? "" : "warn",
    },
  ];
  $("#overview-cards").innerHTML = cards
    .map(
      (c) =>
        `<div class="stat-card"><div class="k">${c.k}</div><div class="v ${c.cls}">${c.v}</div>${c.extra ? `<div class="acct-list">${c.extra}</div>` : ""}</div>`
    )
    .join("");

  // Build a lookup: did -> { name, account_label }
  const detailsByDid = new Map((deviceDetails || []).map((d) => [d.did, d]));
  const acctLabel = (id) => {
    const a = accountsList.find((x) => x.id === id);
    return a ? a.username || a.user_id || a.name || a.id : id || "—";
  };

  const rows = devices
    .map((d) => {
      const info = detailsByDid.get(d.did);
      const name = info?.override?.name || info?.effective?.name || d.did;
      const accountId = info?.override?.account_id || d.account_id || "";
      const accountLabel = escapeHtml(acctLabel(accountId));
      const llmCell =
        typeof d.last_llm_ms === "number"
          ? `${d.last_llm_ms} ms<div class="muted">${formatRelativeTime(now - d.last_llm_at)}</div>`
          : '<span class="muted">—</span>';
      return `<tr>
        <td><strong>${escapeHtml(name)}</strong><div class="muted">${escapeHtml(d.did)}</div></td>
        <td>${d.listening ? `监听中 · ${d.poll_mode === "active" ? "活跃" : "空闲"} ${d.poll_interval_seconds ?? "—"}s` : "停止"}</td>
        <td>${accountId ? `<span class="badge full">${accountLabel}</span>` : '<span class="badge offline">未关联</span>'}</td>
        <td>${d.processed ?? 0}</td>
        <td>${llmCell}</td>
        <td>${escapeHtml(d.last_question || "—")}</td>
        <td>${d.last_hit ? escapeHtml(d.last_hit.word) : "—"}</td>
        <td>${d.last_error ? `<span class="error">${escapeHtml(d.last_error)}</span>` : "—"}</td>
      </tr>`;
    })
    .join("");
  $("#overview-devices").innerHTML = rows
    ? `<table><thead><tr><th>设备</th><th>状态</th><th>关联账号</th><th>处理数</th><th>LLM 耗时</th><th>最近问句</th><th>命中词</th><th>最近错误</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="muted">暂无绑定设备，请先在“设备管理”页绑定。</p>';
}

function formatRelativeTime(seconds) {
  if (typeof seconds !== "number" || !isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)} 秒前`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`;
  return `${Math.round(seconds / 86400)} 天前`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

// miservice logs POST payloads via json.dumps (ensure_ascii=True), so
// Chinese in TTS/UBus messages arrives as literal `\uXXXX` or `\\uXXXX`.
// Decode these for human-readable display in the log panel.
function decodeEscapedUnicode(value) {
  if (typeof value !== "string" || !value.includes("u")) return value;
  let s = value;
  // Handle double-escaped \\uXXXX first, then single \uXXXX.
  s = s.replace(/\\\\u([0-9a-fA-F]{4})/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16)));
  s = s.replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16)));
  return s;
}

/* --------------------------------------------------------------- account */

let accounts = [];
let editingAccountId = null;

async function renderAccount() {
  $("#account-form-card").classList.add("hidden");
  editingAccountId = null;
  await refreshAccounts();
}

async function refreshAccounts() {
  try {
    accounts = await GET("/api/mijia/accounts");
  } catch (err) {
    $("#mijia-accounts").innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
    return;
  }
  if (!accounts.length) {
    $("#mijia-accounts").innerHTML = '<p class="muted">尚未添加任何米家账号，点击下方“新增账号”开始。</p>';
    return;
  }
  $("#mijia-accounts").innerHTML = accounts
    .map((a) => {
      const status = a.authenticated
        ? `<span class="badge full">已登录</span>`
        : a.needs_relogin
          ? `<span class="badge offline">需重新登录</span>`
          : `<span class="badge">未登录</span>`;
      const ident = a.username || a.user_id || a.id;
      return `<div class="account-row" data-id="${a.id}">
        <div class="info">
          <strong>${escapeHtml(a.name || a.id)}</strong>
          <span class="muted">${escapeHtml(ident)} · ${a.region} · ${a.login_type === "pass_token" ? "passToken" : "密码"}</span>
          ${status}
        </div>
        <div class="btn-row">
          <button class="btn primary" data-act="login">登录</button>
          <button class="btn" data-act="edit">编辑</button>
          <button class="btn danger" data-act="logout">退出</button>
          <button class="btn danger" data-act="delete">删除</button>
        </div>
      </div>`;
    })
    .join("");
  $$("#mijia-accounts .account-row").forEach((row) => {
    const id = row.dataset.id;
    $$("[data-act]", row).forEach((btn) =>
      btn.addEventListener("click", () => accountAction(id, btn.dataset.act))
    );
  });
}

function updateAccountFields() {
  const passTokenMode = $("#acct-login-type").value === "pass_token";
  $("#acct-password-fields").classList.toggle("hidden", passTokenMode);
  $("#acct-passtoken-fields").classList.toggle("hidden", !passTokenMode);
}

function showAccountForm(acct) {
  editingAccountId = acct?.id ?? null;
  $("#account-form-title").textContent = acct ? `编辑账号 ${acct.name || acct.id}` : "登录新账号";
  $("#account-form-card").classList.remove("hidden");
  $("#acct-name").value = acct?.name ?? "";
  $("#acct-id").value = acct?.id ?? "";
  $("#acct-id").disabled = !!acct;
  $("#acct-login-type").value = acct?.login_type ?? "pass_token";
  $("#acct-region").value = acct?.region ?? "cn";
  $("#acct-username").value = "";
  $("#acct-password").value = "";
  $("#acct-user-id").value = acct?.user_id ?? "";
  $("#acct-pass-token").value = "";
  updateAccountFields();
}

async function accountAction(id, action) {
  const acct = accounts.find((a) => a.id === id);
  if (!acct) return;
  if (action === "edit") {
    showAccountForm(acct);
    return;
  }
  if (action === "delete") {
    if (!confirm(`确定删除账号 ${acct.name || id}？关联该账号的设备绑定将一并解除。`)) return;
    try {
      await DEL(`/api/mijia/accounts/${id}`);
      toast("账号已删除", "ok");
      await refreshAccounts();
    } catch (err) {
      toast(err.message, "error");
    }
    return;
  }
  if (action === "logout") {
    try {
      await POST(`/api/mijia/accounts/${id}/logout`, {});
      toast("已退出账号", "ok");
      await refreshAccounts();
    } catch (err) {
      toast(err.message, "error");
    }
    return;
  }
  if (action === "login") {
    try {
      await POST(`/api/mijia/accounts/${id}/login`, {});
      toast("登录成功", "ok");
      await refreshAccounts();
    } catch (err) {
      toast(err.message, "error");
    }
  }
}

async function saveAccountForm() {
  const isEdit = !!editingAccountId;
  const body = {
    name: $("#acct-name").value.trim(),
    login_type: $("#acct-login-type").value,
    region: $("#acct-region").value,
  };
  if (!isEdit) {
    body.id = $("#acct-id").value.trim();
  }
  if (body.login_type === "pass_token") {
    body.user_id = $("#acct-user-id").value.trim();
    const token = $("#acct-pass-token").value;
    if (token) body.pass_token = token;
  } else {
    body.username = $("#acct-username").value.trim();
    const pw = $("#acct-password").value;
    if (pw) body.password = pw;
  }
  try {
    if (isEdit) {
      await PUT(`/api/mijia/accounts/${editingAccountId}`, body);
    } else {
      await POST("/api/mijia/accounts", body);
    }
    editingAccountId = null;
    $("#account-form-card").classList.add("hidden");
    toast("账号已保存，正在登录…", "ok");
    await refreshAccounts();
  } catch (err) {
    toast(err.message, "error");
  }
}

/* --------------------------------------------------------------- devices */

const LEVEL_LABEL = { full: "完整支持", partial: "部分支持", untested: "未验证", unsupported: "不支持" };
let selectedBindingAccount = "";

async function renderDevicesScreen() {
  try {
    window._models = await GET("/api/models");
  } catch {
    window._models = [];
  }
  await populateBindingAccounts();
  await renderDiscovery();
  renderListenerSettings();
  await renderBoundDevices();
}

async function populateBindingAccounts() {
  try {
    accounts = await GET("/api/mijia/accounts");
  } catch {
    accounts = [];
  }
  const sel = $("#device-account-id");
  const prev = sel.value || selectedBindingAccount;
  sel.innerHTML = accounts
    .map((a) => `<option value="${a.id}">${escapeHtml(a.name || a.id)}${a.authenticated ? "" : "（未登录）"}</option>`)
    .join("");
  if (!accounts.length) {
    sel.innerHTML = '<option value="">— 请先添加账号 —</option>';
    sel.disabled = true;
    $("#discover-list").innerHTML = '<p class="muted">请先在“米家账号”页添加并登录账号。</p>';
    return;
  }
  sel.disabled = false;
  if (prev && accounts.some((a) => a.id === prev)) {
    sel.value = prev;
  } else {
    const firstAuth = accounts.find((a) => a.authenticated);
    sel.value = firstAuth?.id || accounts[0].id;
  }
  selectedBindingAccount = sel.value;
  await renderDiscovery();
}

function renderListenerSettings() {
  $("#listen-idle-interval").value = cfg.mijia.idle_poll_interval_seconds ?? 10;
  $("#listen-active-interval").value = cfg.mijia.poll_interval_seconds ?? 2;
  $("#listen-active-window").value = cfg.mijia.active_window_seconds ?? 60;
  $("#listen-max-backoff").value = cfg.mijia.max_backoff_seconds ?? 60;
  $("#listen-source").value = cfg.mijia.poll_source || "userprofile";
  $("#listen-fallback").value = String(cfg.mijia.poll_fallback_to_ubus ?? true);
  $("#listen-native-answer").value = String(cfg.mijia.fetch_native_answer ?? false);
}

async function saveListenerSettings() {
  cfg.mijia.idle_poll_interval_seconds = Number($("#listen-idle-interval").value) || 10;
  cfg.mijia.poll_interval_seconds = Number($("#listen-active-interval").value) || 2;
  cfg.mijia.active_window_seconds = Number($("#listen-active-window").value) || 60;
  cfg.mijia.max_backoff_seconds = Number($("#listen-max-backoff").value) || 60;
  cfg.mijia.poll_source = $("#listen-source").value;
  cfg.mijia.poll_fallback_to_ubus = $("#listen-fallback").value === "true";
  cfg.mijia.fetch_native_answer = $("#listen-native-answer").value === "true";
  try {
    await PUT("/api/config", cfg);
    await loadConfig();
    const result = await POST("/api/config/apply");
    toast(`监听设置已应用：${result.devices} 台设备`, "ok");
    renderListenerSettings();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function renderDiscovery() {
  const box = $("#discover-list");
  const accountId = $("#device-account-id").value;
  if (!accountId) {
    box.innerHTML = '<p class="muted">请先选择米家账号。</p>';
    return;
  }
  try {
    discovered = await GET(`/api/mijia/accounts/${accountId}/devices`);
  } catch (err) {
    box.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
    return;
  }
  const selected = new Set(
    Object.entries(cfg.devices || {})
      .filter(([_, d]) => d.account_id === accountId)
      .map(([did]) => did)
  );
  if (!discovered.length) {
    box.innerHTML = '<p class="muted">该账号下没有发现小爱音箱设备。</p>';
    return;
  }
  box.innerHTML = discovered
    .map((d) => {
      const level = d.suggested_model ? LEVEL_LABEL[compatOf(d.suggested_model)] || "未验证" : "未验证";
      return `<label class="device-item">
        <input type="checkbox" value="${d.did}" ${selected.has(d.did) ? "checked" : ""}>
        <span class="name">${escapeHtml(d.name)}</span>
        <span class="badge">${escapeHtml(d.hardware || "未知型号")}</span>
        <span class="badge ${level === "完整支持" ? "full" : level === "部分支持" ? "partial" : "untested"}">${level}</span>
        ${d.online ? "" : '<span class="badge offline">离线</span>'}
        <span class="muted">${escapeHtml(d.did)}</span>
      </label>`;
    })
    .join("");
}

function compatOf(key) {
  const fallback = { key: "custom", level: "untested" };
  if (!window._models) return fallback.level;
  return window._models.find((m) => m.key === key)?.level || fallback.level;
}

async function saveBindings() {
  const accountId = $("#device-account-id").value;
  if (!accountId) return toast("请先选择米家账号", "error");
  const dids = $$("#discover-list input:checked").map((i) => i.value);
  if (!dids.length && !confirm("确定解绑该账号下的全部设备？")) return;
  try {
    const result = await PUT("/api/devices/bindings", { account_id: accountId, dids });
    toast(result.message, "ok");
    await loadConfig();
    await renderDevicesScreen();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function renderBoundDevices() {
  const box = $("#bound-devices");
  const models = await GET("/api/models");
  window._models = models;
  const devices = await GET("/api/devices");
  if (!devices.length) {
    box.innerHTML = '<p class="muted">还没有绑定设备。</p>';
    return;
  }
  const profiles = cfg.llm_profiles.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
  const accountList = cfg.mijia_accounts || [];
  const acctLabel = (id) => {
    const a = accountList.find((x) => x.id === id);
    return a ? (a.name || a.id) : id || "—";
  };
  box.innerHTML = devices
    .map((d) => {
      const o = d.override;
      const eff = d.effective || {};
      const compat = d.compatibility;
      const acctBadge = o.account_id
        ? `<span class="badge full">${escapeHtml(acctLabel(o.account_id))}</span>`
        : '<span class="badge offline">未关联账号</span>';
      return `<details class="device-config" data-did="${d.did}">
        <summary>${escapeHtml(o.name || d.did)} · ${escapeHtml(compat.name || "未知型号")} ${acctBadge}
          <span class="badge ${compat.level}">${LEVEL_LABEL[compat.level] || compat.level}</span>
          <span class="muted">${o.enabled ? "监听中" : "已停用"}</span>
        </summary>
        <div class="form-grid">
          <label>设备名称<input data-f="name" value="${escapeHtml(o.name || "")}"></label>
          <label>型号（兼容配置）
            <select data-f="compatibility_key">
              ${models.map((m) => `<option value="${m.key}" ${o.compatibility_key === m.key ? "selected" : ""}>${m.name}</option>`).join("")}
            </select>
          </label>
          <label>启用监听
            <select data-f="enabled"><option value="true" ${o.enabled ? "selected" : ""}>启用</option><option value="false" ${!o.enabled ? "selected" : ""}>停用</option></select>
          </label>
          <label>LLM 预设（留空=全局）
            <select data-f="llm_profile_id"><option value="">继承全局</option>${cfg.llm_profiles.map((p) => `<option value="${p.id}" ${o.llm_profile_id === p.id ? "selected" : ""}>${escapeHtml(p.name)}</option>`).join("")}</select>
          </label>
          <label>TTS 方式（留空=全局）
            <select data-f="tts_provider"><option value="">继承全局</option><option value="mijia" ${o.tts_provider === "mijia" ? "selected" : ""}>米家 TTS</option><option value="edge_tts" ${o.tts_provider === "edge_tts" ? "selected" : ""}>EdgeTTS</option></select>
          </label>
          <label>EdgeTTS 音色（留空=全局）<input data-f="edge_voice" value="${escapeHtml(o.edge_voice || "")}" placeholder="zh-CN-XiaoxiaoNeural"></label>
          <label>唤醒词（留空=全局，逗号分隔）<input data-f="wake_words" value="${escapeHtml((o.wake_words || []).join(","))}" placeholder="AI助手,问AI"></label>
          <label>唤醒词匹配（留空=全局）
            <select data-f="wake_word_mode"><option value="">继承全局</option><option value="prefix" ${o.wake_word_mode === "prefix" ? "selected" : ""}>开头匹配</option><option value="contains" ${o.wake_word_mode === "contains" ? "selected" : ""}>任意位置</option></select>
          </label>
          <label>上下文（留空=全局）
            <select data-f="context_enabled"><option value="">继承全局</option><option value="true" ${o.context_enabled === true ? "selected" : ""}>开启</option><option value="false" ${o.context_enabled === false ? "selected" : ""}>关闭</option></select>
          </label>
          <label>网络搜索（留空=全局）
            <select data-f="web_search_enabled"><option value="">继承全局</option><option value="true" ${o.web_search_enabled === true ? "selected" : ""}>开启</option><option value="false" ${o.web_search_enabled === false ? "selected" : ""}>关闭</option></select>
          </label>
          <label>会话超时（分钟，留空=全局）<input data-f="session_timeout_minutes" type="number" value="${o.session_timeout_minutes ?? ""}"></label>
          <label>最大上下文 Token（留空=全局）<input data-f="max_context_tokens" type="number" value="${o.max_context_tokens ?? ""}"></label>
          <label>高级：TTS 指令（如 5-1）<input data-f="tts_command" value="${escapeHtml(o.tts_command || "")}"></label>
          <label>高级：唤醒指令<input data-f="wake_command" value="${escapeHtml(o.wake_command || "")}"></label>
          <label>高级：播放状态指令<input data-f="playing_command" value="${escapeHtml(o.playing_command || "")}"></label>
        </div>
        <label>人设（留空=全局）<textarea data-f="persona" rows="3">${escapeHtml(o.persona || "")}</textarea></label>
        <p class="muted">当前生效：预设 ${escapeHtml(eff.llm_profile_id || "—")} · TTS ${escapeHtml(eff.tts_provider || "—")} · 网络搜索 ${eff.web_search_enabled ? "开启" : "关闭"} · 唤醒词 ${(eff.wake_words || []).join("、") || "—"}</p>
        <div class="btn-row">
          <button class="btn primary" data-act="save-device">保存此设备</button>
          <button class="btn" data-act="test-tts">测试米家 TTS</button>
          <button class="btn" data-act="test-audio">测试音频播放</button>
          <button class="btn" data-act="test-mute">测试暂停</button>
          <button class="btn danger" data-act="clear-context">清除上下文</button>
        </div>
        <p class="error" data-role="err"></p>
      </details>`;
  })
    .join("");

  $$("#bound-devices .device-config").forEach((card) => {
    const did = card.dataset.did;
    $$("[data-f]", card).forEach((input) => {
      input.addEventListener("change", () => collectDeviceOverride(card, did));
      if (input.tagName === "TEXTAREA") input.addEventListener("input", () => collectDeviceOverride(card, did));
    });
    $$("[data-act]", card).forEach((btn) => btn.addEventListener("click", () => deviceAction(card, did, btn.dataset.act)));
  });
}

function collectDeviceOverride(card, did) {
  const o = cfg.devices[did] || {};
  const convert = (name, value) => {
    if (["enabled", "context_enabled", "web_search_enabled"].includes(name)) return value === "" ? null : value === "true";
    if (["session_timeout_minutes", "max_context_tokens"].includes(name)) return value === "" ? null : Number(value);
    if (name === "wake_words") return value.trim() ? value.split(/[,，]/).map((w) => w.trim()).filter(Boolean) : null;
    if (value === "") return null;
    return value;
  };
  $$("[data-f]", card).forEach((input) => {
    const name = input.dataset.f;
    const converted = convert(name, input.value);
    if (converted === null) delete o[name];
    else o[name] = converted;
  });
  const persona = $("[data-f='persona']", card).value;
  if (persona.trim()) o.persona = persona;
  else delete o.persona;
  cfg.devices[did] = o;
}

async function deviceAction(card, did, action) {
  const errEl = $("[data-role='err']", card);
  errEl.textContent = "";
  try {
    if (action === "save-device") {
      collectDeviceOverride(card, did);
      await PUT(`/api/devices/${did}`, cfg.devices[did] || {});
      await loadConfig();
      toast("设备配置已保存并应用", "ok");
    } else if (action === "clear-context") {
      const r = await DEL(`/api/devices/${did}/context`);
      toast(r.message, "ok");
    } else if (action === "test-mute") {
      const r = await POST(`/api/devices/${did}/test/mute`, {});
      toast(r.message, "ok");
    } else {
      const text = prompt("播报内容", "你好，这是一条测试播报");
      if (!text) return;
      const r = await POST(`/api/devices/${did}/${action.replace("test-", "test/")}`, { text });
      toast(r.message, "ok");
    }
  } catch (err) {
    errEl.textContent = err.message;
  }
}

/* ------------------------------------------------------------------- llm */

async function renderLLM() {
  const box = $("#llm-profiles");
  box.innerHTML = cfg.llm_profiles
    .map((p, idx) => {
      return `<div class="profile-card" data-idx="${idx}">
        <h4>预设：${escapeHtml(p.name)} <button class="btn danger" data-act="del">删除</button></h4>
        <div class="form-grid">
          <label>预设名称<input data-f="name" value="${escapeHtml(p.name)}"></label>
          <label>预设 ID（英文，唯一）<input data-f="id" value="${escapeHtml(p.id)}"></label>
          <label>API Base URL<input data-f="api_base" value="${escapeHtml(p.api_base)}"></label>
          <label>API Key（留空=保持不变）<input data-f="api_key" type="password" placeholder="${p.api_key_configured ? "已配置" : "未配置"}"></label>
          <label>模型 ID<input data-f="model" value="${escapeHtml(p.model)}"></label>
          <label>超时（秒）<input data-f="timeout_seconds" type="number" value="${p.timeout_seconds}"></label>
          <label>流式请求
            <select data-f="stream"><option value="false" ${!p.stream ? "selected" : ""}>否</option><option value="true" ${p.stream ? "selected" : ""}>是</option></select>
          </label>
          <label>Temperature<input data-f="temperature" type="number" step="0.1" value="${p.temperature}"></label>
          <label>Top P<input data-f="top_p" type="number" step="0.1" value="${p.top_p}"></label>
          <label>最大输出 Token<input data-f="max_output_tokens" type="number" value="${p.max_output_tokens}"></label>
        </div>
        <div class="btn-row"><button class="btn" data-act="test">测试连接</button></div>
        <p class="error" data-role="err"></p>
      </div>`;
    })
    .join("");

  $$("#llm-profiles .profile-card").forEach((card) => {
    const idx = Number(card.dataset.idx);
    $$("[data-f]", card).forEach((input) => {
      const handler = () => {
        const p = cfg.llm_profiles[idx];
        const name = input.dataset.f;
        if (name === "api_key") {
          if (input.value) p.api_key = input.value;
        } else if (["timeout_seconds", "temperature", "top_p", "max_output_tokens"].includes(name)) {
          p[name] = Number(input.value);
        } else if (name === "stream") {
          p[name] = input.value === "true";
        } else {
          p[name] = input.value;
        }
      };
      input.addEventListener("input", handler);
      input.addEventListener("change", handler);
    });
    $$("[data-act]", card).forEach((btn) =>
      btn.addEventListener("click", async () => {
        const errEl = $("[data-role='err']", card);
        errEl.textContent = "";
        const idx2 = Number(card.dataset.idx);
        if (btn.dataset.act === "del") {
          if (cfg.llm_profiles.length <= 1) return toast("至少保留一个预设", "error");
          cfg.llm_profiles.splice(idx2, 1);
          renderLLM();
          return;
        }
        try {
          const p = cfg.llm_profiles[idx2];
          const r = await POST(`/api/llm/profiles/${encodeURIComponent(p.id)}/test`, {
            profile_id: p.id,
            api_base: p.api_base,
            api_key: p.api_key || undefined,
            model: p.model,
          });
          errEl.classList.remove("error");
          errEl.textContent = `模型回复：${r.message}`;
        } catch (err) {
          errEl.classList.add("error");
          errEl.textContent = err.message;
        }
      })
    );
  });
}

/* --------------------------------------------------------------- persona */

function renderPersona() {
  $("#persona-text").value = cfg.defaults.persona || "";
  $("#persona-template").value = "";
}

/* ------------------------------------------------------------------- tts */

function renderTTS() {
  $("#tts-provider").value = cfg.tts.default_provider;
  $("#tts-edge-voice").value = cfg.tts.edge_voice;
  $("#tts-timeout").value = cfg.tts.timeout_seconds;
  $("#tts-fallback").value = String(cfg.tts.fallback_to_mijia);
  $("#tts-error-message").value = cfg.tts.error_message;
  $("#tts-thinking-message").value = cfg.tts.thinking_message || "";
  $("#web-public-base-url").value = cfg.web.public_base_url || "";
}

/* ------------------------------------------------------------ web search */

function renderWebSearch() {
  const s = cfg.web_search;
  $("#web-search-enabled").value = String(s.enabled);
  $("#web-search-url").value = s.search_url;
  $("#web-search-max-results").value = s.max_results;
  $("#web-fetch-top-results").value = s.fetch_top_results;
  $("#web-search-timeout").value = s.timeout_seconds;
  $("#web-fetch-max-chars").value = s.max_page_chars;
  $("#web-search-max-chars").value = s.max_result_chars;
}

function collectWebSearch() {
  cfg.web_search.enabled = $("#web-search-enabled").value === "true";
  cfg.web_search.search_url = $("#web-search-url").value.trim() || "https://www.sogou.com/web";
  cfg.web_search.max_results = Number($("#web-search-max-results").value) || 5;
  cfg.web_search.fetch_top_results = Number($("#web-fetch-top-results").value);
  cfg.web_search.timeout_seconds = Number($("#web-search-timeout").value) || 15;
  cfg.web_search.max_page_chars = Number($("#web-fetch-max-chars").value) || 6000;
  cfg.web_search.max_result_chars = Number($("#web-search-max-chars").value) || 12000;
}

/* ------------------------------------------------------------------ logs */

async function startLogPolling() {
  logCursor = 0;
  $("#log-view").innerHTML = "";
  await pullLogs();
  clearInterval(logTimer);
  logTimer = setInterval(() => {
    if (!document.hidden) pullLogs();
  }, 2000);
}

function stopLogPolling() {
  clearInterval(logTimer);
  logTimer = null;
}

async function pullLogs() {
  const level = $("#log-level").value;
  const items = await GET(`/api/logs?after=${logCursor}&limit=200${level ? `&level=${level}` : ""}`);
  if (!items.length) return;
  const view = $("#log-view");
  const follow = $("#log-follow").checked;
  const nearBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 60;
  for (const item of items) {
    logCursor = Math.max(logCursor, item.id);
    const line = document.createElement("div");
    line.className = `log-line log-${item.level}`;
    const meta = document.createElement("span");
    meta.className = "log-meta";
    meta.textContent = `${item.time.slice(11, 19)} ${item.level.padEnd(7)} ${item.device ? `[${item.device}] ` : ""}`;
    line.appendChild(meta);
    line.appendChild(document.createTextNode(decodeEscapedUnicode(item.message)));
    view.appendChild(line);
  }
  while (view.children.length > 1000) view.removeChild(view.firstChild);
  if (follow && nearBottom) view.scrollTop = view.scrollHeight;
}

/* ----------------------------------------------------------------- bind */

function bindEvents() {
  $("#auth-submit").addEventListener("click", submitAuth);
  $("#auth-password").addEventListener("keydown", (e) => e.key === "Enter" && submitAuth());
  $("#btn-show-reset").addEventListener("click", (e) => {
    e.preventDefault();
    $("#auth-reset-panel").classList.toggle("hidden");
    $("#auth-reset-error").textContent = "";
  });
  $("#btn-request-reset").addEventListener("click", requestPasswordReset);
  $("#btn-confirm-reset").addEventListener("click", confirmPasswordReset);
  $("#auth-reset-token").addEventListener("keydown", (e) => e.key === "Enter" && confirmPasswordReset());
  $("#auth-reset-new").addEventListener("keydown", (e) => e.key === "Enter" && confirmPasswordReset());
  $("#btn-logout").addEventListener("click", async (e) => {
    e.preventDefault();
    await POST("/api/auth/logout");
    location.reload();
  });
  $("#btn-apply").addEventListener("click", applyConfig);

  $("#btn-add-account").addEventListener("click", () => showAccountForm(null));
  $("#btn-refresh-accounts").addEventListener("click", refreshAccounts);
  $("#btn-save-account").addEventListener("click", saveAccountForm);
  $("#btn-cancel-account").addEventListener("click", () => {
    editingAccountId = null;
    $("#account-form-card").classList.add("hidden");
  });
  $("#acct-login-type").addEventListener("change", updateAccountFields);

  $("#device-account-id").addEventListener("change", (e) => {
    selectedBindingAccount = e.target.value;
    renderDiscovery();
  });
  $("#btn-refresh-devices").addEventListener("click", renderDiscovery);
  $("#btn-save-bindings").addEventListener("click", saveBindings);
  $("#btn-save-listener").addEventListener("click", saveListenerSettings);

  $("#btn-add-profile").addEventListener("click", () => {
    const id = `profile-${Date.now().toString(36)}`;
    cfg.llm_profiles.push({
      id, name: "新预设", api_base: "https://api.openai.com/v1", api_key: "", model: "gpt-4o-mini",
      timeout_seconds: 200, stream: false, temperature: 0.7, top_p: 1, max_output_tokens: 800,
      extra_body: {},
    });
    renderLLM();
  });
  $("#btn-save-llm").addEventListener("click", saveConfig);

  $("#persona-template").addEventListener("change", () => {
    const tpl = PERSONA_TEMPLATES[$("#persona-template").value];
    if (tpl) $("#persona-text").value = tpl;
  });
  $("#btn-save-persona").addEventListener("click", () => {
    cfg.defaults.persona = $("#persona-text").value.trim();
    saveConfig();
  });

  $("#btn-save-tts").addEventListener("click", () => {
    cfg.tts.default_provider = $("#tts-provider").value;
    cfg.tts.edge_voice = $("#tts-edge-voice").value.trim() || "zh-CN-XiaoxiaoNeural";
    cfg.tts.timeout_seconds = Number($("#tts-timeout").value) || 30;
    cfg.tts.fallback_to_mijia = $("#tts-fallback").value === "true";
    cfg.tts.error_message = $("#tts-error-message").value.trim() || "网络异常，请稍后再试";
    cfg.tts.thinking_message = $("#tts-thinking-message").value.trim();
    cfg.web.public_base_url = $("#web-public-base-url").value.trim();
    saveConfig();
  });

  $("#btn-save-web-search").addEventListener("click", () => {
    collectWebSearch();
    saveConfig();
  });
  $("#btn-test-web-search").addEventListener("click", async () => {
    try {
      collectWebSearch();
      await PUT("/api/config", cfg);
      await loadConfig();
      const r = await POST("/api/web-search/test", { query: $("#web-search-test-query").value.trim() || undefined });
      toast(`网络搜索测试成功：${r.message}`, "ok");
    } catch (err) {
      toast(err.message, "error");
    }
  });

  $("#log-level").addEventListener("change", startLogPolling);
  $("#btn-clear-logs").addEventListener("click", async () => {
    await DEL("/api/logs");
    logCursor = 0;
    $("#log-view").innerHTML = "";
    toast("日志已清空", "ok");
  });
}

bindEvents();
boot();
