// 管理中心壳：模块注册 + 侧边栏导航 + hash 路由
// 新功能只需在 modules/ 下新建模块文件并在 MODULES 中注册。
import { feedbackTodoModule } from "./modules/feedback-todo.js";

const bridge = window.AstrBotPluginPage;

const MODULES = [feedbackTodoModule];

const state = {
  current: null,
  cleanup: null,
};

const $ = (sel) => document.querySelector(sel);

function setStatus(text, isError = false) {
  const el = $("#status-line");
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function currentModuleId() {
  const hash = location.hash.replace(/^#\/?/, "");
  return MODULES.some((m) => m.id === hash) ? hash : MODULES[0].id;
}

async function loadBadges() {
  const badges = {};
  for (const m of MODULES) {
    if (typeof m.badge !== "function") continue;
    try {
      const value = await m.badge();
      if (value !== null && value !== undefined) badges[m.id] = value;
    } catch (e) {
      // 单个模块的角标失败不影响整体
    }
  }
  return badges;
}

function renderNav(badges) {
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const mod of MODULES) {
    const item = document.createElement("button");
    item.className = "nav-item";
    item.dataset.module = mod.id;
    const label = document.createElement("span");
    label.textContent = mod.title;
    item.appendChild(label);
    if (badges && badges[mod.id] !== undefined) {
      const badge = document.createElement("span");
      badge.className = "nav-badge";
      badge.textContent = badges[mod.id];
      item.appendChild(badge);
    }
    if (mod.id === state.current) item.classList.add("active");
    item.addEventListener("click", () => {
      location.hash = `/${mod.id}`;
    });
    nav.appendChild(item);
  }
}

async function mountModule(id) {
  const mod = MODULES.find((m) => m.id === id);
  if (!mod) return;
  if (state.cleanup) {
    try {
      state.cleanup();
    } catch (e) {
      /* 忽略清理异常 */
    }
    state.cleanup = null;
  }
  state.current = id;
  setStatus("");
  $("#module-title").textContent = mod.title;
  $("#module-desc").textContent = mod.description || "";
  const root = $("#module-root");
  root.innerHTML = "";

  const badges = await loadBadges();
  renderNav(badges);

  state.cleanup = (await mod.mount(root, { setStatus, bridge })) || null;
}

async function main() {
  const context = await bridge.ready();
  if (context?.displayName) {
    $("#plugin-name").textContent = context.displayName;
  }

  await mountModule(currentModuleId());

  window.addEventListener("hashchange", () => {
    const id = currentModuleId();
    if (id !== state.current) mountModule(id);
  });
}

main();
