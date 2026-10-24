const bridge = window.AstrBotPluginPage;

const state = {
  items: [],
  filter: "pending",
  selected: new Set(),
  busy: false,
};

const $ = (sel) => document.querySelector(sel);

function esc(text) {
  return String(text ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function fmtTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

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

function sourceText(item) {
  if (item.message_type === "group") {
    return `群 ${item.group_name || "未知群"}(${item.group_id || "?"})`;
  }
  return "私聊";
}

async function load() {
  if (state.busy) return;
  state.busy = true;
  try {
    const data = await bridge.apiGet("feedback_todo/list");
    state.items = (data.items || []).slice();
    state.selected.clear();
    render();
  } catch (e) {
    setStatus(`加载失败：${e.message}`, true);
  } finally {
    state.busy = false;
  }
}

function filteredItems() {
  if (state.filter === "all") return state.items;
  if (state.filter === "done") return state.items.filter((i) => i.status === "done");
  return state.items.filter((i) => i.status !== "done");
}

function render() {
  const items = filteredItems();
  const pending = state.items.filter((i) => i.status !== "done").length;
  $("#count-info").textContent = `共 ${state.items.length} 条 · 未完成 ${pending} 条`;

  const list = $("#list");
  list.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "当前没有待办";
    list.appendChild(empty);
  } else {
    for (const item of items) list.appendChild(buildCard(item));
  }
  renderBatchBar();
}

function renderBatchBar() {
  const bar = $("#batch-bar");
  bar.classList.toggle("visible", state.selected.size > 0);
  $("#selected-count").textContent = `已选中 ${state.selected.size} 项`;
}

function buildCard(item) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.id = item.id;

  const done = item.status === "done";

  // 头部：勾选框 + 编号 + 状态 + 来源 + 时间
  const head = document.createElement("div");
  head.className = "card-head";
  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = state.selected.has(item.id);
  check.addEventListener("change", () => {
    if (check.checked) state.selected.add(item.id);
    else state.selected.delete(item.id);
    renderBatchBar();
  });
  head.appendChild(check);

  const title = document.createElement("span");
  title.className = "card-title";
  title.textContent = `#${item.id}`;
  head.appendChild(title);

  const tag = document.createElement("span");
  tag.className = `tag ${done ? "done" : "pending"}`;
  tag.textContent = done ? "已完成" : "未完成";
  head.appendChild(tag);

  const source = document.createElement("span");
  source.textContent = sourceText(item);
  head.appendChild(source);

  const time = document.createElement("span");
  time.className = "card-meta";
  time.textContent = `登记于 ${fmtTime(item.created_time)}`;
  head.appendChild(time);
  card.appendChild(head);

  const meta = document.createElement("div");
  meta.className = "card-meta";
  meta.textContent = `报告人：${item.reporter_name || "?"}(${item.reporter_id || "?"})`;
  if (item.message_time) meta.textContent += ` · 反馈消息时间 ${fmtTime(item.message_time)}`;
  card.appendChild(meta);

  // AI 摘要（可编辑）
  const summaryField = document.createElement("div");
  summaryField.className = "field";
  const summaryLabel = document.createElement("label");
  summaryLabel.textContent = "AI 摘要（可编辑）";
  const summaryBox = document.createElement("textarea");
  summaryBox.value = item.llm_summary || "";
  summaryField.appendChild(summaryLabel);
  summaryField.appendChild(summaryBox);
  card.appendChild(summaryField);

  // 补充原因（可编辑）
  const reasonField = document.createElement("div");
  reasonField.className = "field";
  const reasonLabel = document.createElement("label");
  reasonLabel.textContent = "补充原因（可编辑）";
  const reasonBox = document.createElement("textarea");
  reasonBox.value = item.reason || "";
  reasonField.appendChild(reasonLabel);
  reasonField.appendChild(reasonBox);
  card.appendChild(reasonField);

  // 被引用消息
  if (item.target_text) {
    const target = document.createElement("div");
    target.className = "field";
    const label = document.createElement("label");
    label.textContent = "被引用消息";
    const pre = document.createElement("pre");
    pre.className = "context";
    pre.textContent = `${item.reporter_name || "?"}：${item.target_text}`;
    target.appendChild(label);
    target.appendChild(pre);
    card.appendChild(target);
  }

  // 上下文与附图（折叠）
  const detail = document.createElement("details");
  const summaryEl = document.createElement("summary");
  summaryEl.textContent = "聊天上下文与附图";
  detail.appendChild(summaryEl);
  if (item.context_text) {
    const pre = document.createElement("pre");
    pre.className = "context";
    pre.textContent = item.context_text;
    detail.appendChild(pre);
  }
  if (Array.isArray(item.context_images) && item.context_images.length) {
    const imgs = document.createElement("div");
    imgs.className = "img-links";
    imgs.textContent =
      `附图 ${item.context_images.length} 张（[图片N] 对应第N条）：\n` +
      item.context_images.map((u, i) => `图片${i + 1}：${u}`).join("\n");
    detail.appendChild(imgs);
  }
  if (detail.childNodes.length > 1) card.appendChild(detail);

  // 已完成信息
  if (done) {
    const resultField = document.createElement("div");
    resultField.className = "field";
    const label = document.createElement("label");
    label.textContent = `处理结果（完成于 ${fmtTime(item.closed_time)}）`;
    const pre = document.createElement("pre");
    pre.className = "context";
    pre.textContent = item.result || "";
    resultField.appendChild(label);
    resultField.appendChild(pre);
    card.appendChild(resultField);
  }

  // 操作按钮
  const actions = document.createElement("div");
  actions.className = "card-actions";

  const saveBtn = document.createElement("button");
  saveBtn.className = "btn";
  saveBtn.textContent = "保存修改";
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    try {
      await bridge.apiPost("feedback_todo/update", {
        id: item.id,
        llm_summary: summaryBox.value,
        reason: reasonBox.value,
      });
      setStatus(`待办 #${item.id} 已保存修改`);
      await load();
    } catch (e) {
      setStatus(`保存失败：${e.message}`, true);
    } finally {
      saveBtn.disabled = false;
    }
  });
  actions.appendChild(saveBtn);

  const resumBtn = document.createElement("button");
  resumBtn.className = "btn";
  resumBtn.textContent = "重新总结";
  resumBtn.addEventListener("click", async () => {
    resumBtn.disabled = true;
    resumBtn.textContent = "总结中…";
    setStatus(`正在重新总结待办 #${item.id}…`);
    try {
      const data = await bridge.apiPost("feedback_todo/resummarize", { id: item.id });
      summaryBox.value = data.summary || "";
      setStatus(`待办 #${item.id} 重新总结完成`);
    } catch (e) {
      setStatus(`重新总结失败：${e.message}`, true);
    } finally {
      resumBtn.disabled = false;
      resumBtn.textContent = "重新总结";
    }
  });
  actions.appendChild(resumBtn);

  const delBtn = document.createElement("button");
  delBtn.className = "btn danger";
  delBtn.textContent = "删除";
  delBtn.addEventListener("click", async () => {
    if (!confirm(`确定删除待办 #${item.id} 吗？此操作不可恢复。`)) return;
    delBtn.disabled = true;
    try {
      await bridge.apiPost("feedback_todo/delete", { ids: [item.id] });
      setStatus(`待办 #${item.id} 已删除`);
      await load();
    } catch (e) {
      setStatus(`删除失败：${e.message}`, true);
      delBtn.disabled = false;
    }
  });
  actions.appendChild(delBtn);
  card.appendChild(actions);

  // 未完成：处理结果输入 + 完成（会真实发送 QQ 引用回复）
  if (!done) {
    const area = document.createElement("div");
    area.className = "complete-area";
    const resultBox = document.createElement("textarea");
    resultBox.placeholder = "输入处理结果，完成后将引用回复给报告人…";
    const doneBtn = document.createElement("button");
    doneBtn.className = "btn primary";
    doneBtn.textContent = "完成并发送回复";
    doneBtn.addEventListener("click", async () => {
      const result = resultBox.value.trim();
      if (!result) {
        setStatus("请先填写处理结果", true);
        return;
      }
      const target = `${sourceText(item)} · ${item.reporter_name || "?"}(${item.reporter_id || "?"})`;
      if (!confirm(`将把处理结果引用回复给：${target}\n确认完成待办 #${item.id} 吗？`)) return;
      doneBtn.disabled = true;
      doneBtn.textContent = "发送中…";
      try {
        const data = await bridge.apiPost("feedback_todo/complete", {
          id: item.id,
          result,
        });
        setStatus(data.message || `待办 #${item.id} 已完成`);
        await load();
      } catch (e) {
        setStatus(`完成失败：${e.message}`, true);
        doneBtn.disabled = false;
        doneBtn.textContent = "完成并发送回复";
      }
    });
    area.appendChild(resultBox);
    area.appendChild(doneBtn);
    card.appendChild(area);
  }

  return card;
}

async function main() {
  const context = await bridge.ready();
  if (context?.displayName) {
    document.title = `反馈待办管理 - ${context.displayName}`;
  }

  $("#filters").addEventListener("click", (e) => {
    const btn = e.target.closest(".filter-btn");
    if (!btn) return;
    state.filter = btn.dataset.filter;
    document.querySelectorAll(".filter-btn").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    render();
  });

  $("#refresh-btn").addEventListener("click", () => {
    setStatus("");
    load();
  });

  $("#batch-delete-btn").addEventListener("click", async () => {
    const ids = [...state.selected];
    if (!ids.length) return;
    if (!confirm(`确定删除选中的 ${ids.length} 条待办吗？此操作不可恢复。`)) return;
    try {
      const data = await bridge.apiPost("feedback_todo/delete", { ids });
      setStatus(`已删除 ${data.deleted.length} 条待办` +
        (data.missing?.length ? `，${data.missing.length} 条不存在` : ""));
      await load();
    } catch (e) {
      setStatus(`批量删除失败：${e.message}`, true);
    }
  });

  await load();
}

main();
