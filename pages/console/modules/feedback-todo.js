// 反馈待办模块：列表 / 编辑摘要 / 重新总结 / 完成回复 / 批量删除
const bridge = window.AstrBotPluginPage;

// 网页反馈的问题类型（与后端 FeedbackTodoService.WEB_TYPE_LABELS 保持一致）
const WEB_TYPE_LABELS = {
  bug: "Bug 报错",
  feature: "功能建议",
  help: "使用求助",
  other: "其他",
};

function isWebItem(item) {
  return item.message_type === "web" || item.source === "web";
}

export const feedbackTodoModule = {
  id: "feedback-todo",
  title: "反馈待办",
  description: "收集群友反馈的 Bug 与建议：编辑 AI 摘要、一键重新总结、完成并发送回复、批量删除",

  async badge() {
    const data = await bridge.apiGet("feedback_todo/list");
    const count = (data.items || []).filter((i) => i.status !== "done").length;
    return count > 0 ? count : null;
  },

  async mount(root, shell) {
    const { setStatus } = shell;

    const state = {
      items: [],
      filter: "pending",
      selected: new Set(),
      busy: false,
    };

    // ---------- 工具栏 ----------
    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";

    const filters = document.createElement("div");
    filters.className = "filters";
    const filterDefs = [
      ["pending", "未完成"],
      ["done", "已完成"],
      ["all", "全部"],
    ];
    for (const [key, label] of filterDefs) {
      const btn = document.createElement("button");
      btn.className = "filter-btn" + (key === state.filter ? " active" : "");
      btn.dataset.filter = key;
      btn.textContent = label;
      btn.addEventListener("click", () => {
        state.filter = key;
        toolbar.querySelectorAll(".filter-btn").forEach((b) => {
          b.classList.toggle("active", b.dataset.filter === key);
        });
        render();
      });
      filters.appendChild(btn);
    }
    toolbar.appendChild(filters);

    const countInfo = document.createElement("span");
    countInfo.className = "count-info";
    toolbar.appendChild(countInfo);

    const refreshBtn = document.createElement("button");
    refreshBtn.className = "btn";
    refreshBtn.textContent = "刷新";
    refreshBtn.addEventListener("click", () => {
      setStatus("");
      load();
    });
    toolbar.appendChild(refreshBtn);
    root.appendChild(toolbar);

    const batchBar = document.createElement("div");
    batchBar.className = "toolbar batch-bar";
    const selectedCount = document.createElement("span");
    const batchDeleteBtn = document.createElement("button");
    batchDeleteBtn.className = "btn danger";
    batchDeleteBtn.textContent = "删除选中";
    batchDeleteBtn.addEventListener("click", () => {
      const ids = [...state.selected];
      if (!ids.length) return;
      twoStepConfirm(batchDeleteBtn, `确认删除${ids.length}条？`, async () => {
        try {
          const data = await bridge.apiPost("feedback_todo/delete", { ids });
          setStatus(
            `已删除 ${data.deleted.length} 条待办` +
              (data.missing?.length ? `，${data.missing.length} 条不存在` : ""),
          );
          await load();
        } catch (e) {
          setStatus(`批量删除失败：${e.message}`, true);
        }
      });
    });
    batchBar.appendChild(selectedCount);
    batchBar.appendChild(batchDeleteBtn);
    root.appendChild(batchBar);

    const list = document.createElement("div");
    list.className = "list";
    root.appendChild(list);

    // ---------- 数据与渲染 ----------
    async function load() {
      if (state.busy) return;
      state.busy = true;
      try {
        const data = await bridge.apiGet("feedback_todo/list");
        state.items = data.items || [];
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
      countInfo.textContent = `共 ${state.items.length} 条 · 未完成 ${pending} 条`;

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
      batchBar.classList.toggle("visible", state.selected.size > 0);
      selectedCount.textContent = `已选中 ${state.selected.size} 项`;
    }

    function sourceText(item) {
      if (item.message_type === "group") {
        return `群 ${item.group_name || "未知群"}(${item.group_id || "?"})`;
      }
      if (isWebItem(item)) return "网页反馈";
      return "私聊";
    }

    function fmtTime(ts) {
      if (!ts) return "";
      return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
    }

    // 页面运行在受限 iframe（无 allow-modals），window.confirm 会被静默拦截，
    // 因此危险操作使用两段式按钮确认：第一次点击进入待确认状态，3秒内再点执行
    function twoStepConfirm(btn, confirmText, action) {
      if (btn.dataset.armed === "1") {
        clearTimeout(Number(btn.dataset.timer || 0));
        const label = btn.dataset.label || btn.textContent;
        delete btn.dataset.armed;
        delete btn.dataset.timer;
        delete btn.dataset.label;
        btn.classList.remove("armed");
        btn.textContent = label;
        action();
        return;
      }
      btn.dataset.armed = "1";
      btn.dataset.label = btn.textContent;
      btn.textContent = confirmText;
      btn.classList.add("armed");
      btn.dataset.timer = String(
        setTimeout(() => {
          const original = btn.dataset.label;
          delete btn.dataset.armed;
          delete btn.dataset.timer;
          delete btn.dataset.label;
          btn.classList.remove("armed");
          if (original) btn.textContent = original;
        }, 3000),
      );
    }

    // 附图大图查看：iframe 沙箱无弹窗，用页内遮罩实现，点击任意处或按 Esc 关闭
    function openLightbox(src, caption) {
      const overlay = document.createElement("div");
      overlay.className = "img-lightbox";
      const img = document.createElement("img");
      img.src = src;
      const cap = document.createElement("div");
      cap.className = "img-lightbox-cap";
      cap.textContent = caption;
      overlay.appendChild(img);
      overlay.appendChild(cap);
      const close = () => {
        overlay.remove();
        document.removeEventListener("keydown", onKey);
      };
      const onKey = (e) => {
        if (e.key === "Escape") close();
      };
      overlay.addEventListener("click", close);
      document.addEventListener("keydown", onKey);
      document.body.appendChild(overlay);
    }

    // 懒加载待办附图缩略图（展开折叠区时才请求，避免列表页一次拉全部图片）
    async function loadGallery(container, item) {
      container.textContent = "正在加载附图…";
      const remote = Array.isArray(item.context_images) ? item.context_images : [];
      try {
        const data = await bridge.apiGet("feedback_todo/images", { id: item.id });
        const images = data.images || [];
        container.textContent = "";
        if (!images.length) {
          container.className = "img-links";
          container.textContent = remote.length
            ? `附图 ${remote.length} 张（本地文件已清理或转存失败，原始链接可能已过期）：\n` +
              remote.map((u, i) => `图片${i + 1}：${u}`).join("\n")
            : "附图文件已丢失";
          return;
        }
        const grid = document.createElement("div");
        grid.className = "img-grid";
        for (const img of images) {
          const n = parseInt(img.name, 10);
          const cell = document.createElement("figure");
          cell.className = "img-cell";
          const el = document.createElement("img");
          el.src = `data:${img.mime};base64,${img.data}`;
          el.alt = `图片${n}`;
          el.addEventListener("click", () =>
            openLightbox(el.src, `图片${n}（点击任意处关闭）`),
          );
          const cap = document.createElement("figcaption");
          cap.textContent = `图片${n}`;
          cell.appendChild(el);
          cell.appendChild(cap);
          grid.appendChild(cell);
        }
        container.appendChild(grid);
      } catch (e) {
        container.className = "img-links";
        container.textContent = remote.length
          ? `附图加载失败（${e.message}），原始链接：\n` +
            remote.map((u, i) => `图片${i + 1}：${u}`).join("\n")
          : `附图加载失败：${e.message}`;
      }
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

      const web = isWebItem(item);
      const meta = document.createElement("div");
      meta.className = "card-meta";
      const reporter = item.reporter_id
        ? `${item.reporter_name || "?"}(${item.reporter_id})`
        : item.reporter_name || "?";
      const typeLabel = web ? WEB_TYPE_LABELS[item.issue_type] || "其他" : "";
      meta.textContent = `报告人：${reporter}${typeLabel ? ` · ${typeLabel}` : ""}`;
      if (item.message_time)
        meta.textContent += `${web ? " · 发生时间 " : " · 反馈消息时间 "}${fmtTime(item.message_time)}`;
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

      // 被引用消息 / 问题描述（网页反馈按图文混排渲染，图片 chip 可点开附图）
      let detailRef = null;
      const blocks =
        web && Array.isArray(item.content_blocks) && item.content_blocks.length
          ? item.content_blocks
          : null;
      if (blocks) {
        const hasBody = blocks.some(
          (b) => b.type === "image" || String(b.text || "").trim(),
        );
        if (hasBody) {
          const contentField = document.createElement("div");
          contentField.className = "field";
          const label = document.createElement("label");
          label.textContent = "问题描述";
          contentField.appendChild(label);
          const pre = document.createElement("pre");
          pre.className = "context";
          for (const block of blocks) {
            if (block.type === "text") {
              pre.appendChild(document.createTextNode(String(block.text || "")));
            } else if (block.type === "image" && block.index) {
              const chip = document.createElement("button");
              chip.type = "button";
              chip.className = "img-chip";
              chip.textContent = `图片 ${block.index}`;
              chip.title = "点击展开附图";
              chip.addEventListener("click", () => {
                if (detailRef) {
                  detailRef.open = true;
                  detailRef.scrollIntoView({ behavior: "smooth", block: "nearest" });
                }
              });
              pre.appendChild(chip);
              pre.appendChild(document.createTextNode(" "));
            }
          }
          contentField.appendChild(pre);
          card.appendChild(contentField);
        }
      } else if (item.target_text) {
        const target = document.createElement("div");
        target.className = "field";
        const label = document.createElement("label");
        label.textContent = web ? "问题描述" : "被引用消息";
        const pre = document.createElement("pre");
        pre.className = "context";
        pre.textContent = web
          ? item.target_text
          : `${item.reporter_name || "?"}：${item.target_text}`;
        target.appendChild(label);
        target.appendChild(pre);
        card.appendChild(target);
      }

      // 网页反馈的复现步骤与联系方式
      if (item.steps) {
        const stepsField = document.createElement("div");
        stepsField.className = "field";
        const stepsLabel = document.createElement("label");
        stepsLabel.textContent = "复现步骤";
        const stepsPre = document.createElement("pre");
        stepsPre.className = "context";
        stepsPre.textContent = item.steps;
        stepsField.appendChild(stepsLabel);
        stepsField.appendChild(stepsPre);
        card.appendChild(stepsField);
      }
      if (item.contact) {
        const contactField = document.createElement("div");
        contactField.className = "field";
        const contactLabel = document.createElement("label");
        contactLabel.textContent = "联系方式";
        const contactPre = document.createElement("pre");
        contactPre.className = "context";
        contactPre.textContent = item.contact;
        contactField.appendChild(contactLabel);
        contactField.appendChild(contactPre);
        card.appendChild(contactField);
      }

      // 上下文与附图（折叠）
      const hasRemoteImages = Array.isArray(item.context_images) && item.context_images.length > 0;
      const hasLocalImages =
        Array.isArray(item.context_image_files) && item.context_image_files.length > 0;
      const detail = document.createElement("details");
      detailRef = detail;
      const detailSummary = document.createElement("summary");
      detailSummary.textContent = web ? "附图" : "聊天上下文与附图";
      detail.appendChild(detailSummary);
      if (item.context_text) {
        const pre = document.createElement("pre");
        pre.className = "context";
        pre.textContent = item.context_text;
        detail.appendChild(pre);
      }
      if (hasRemoteImages || hasLocalImages) {
        const gallery = document.createElement("div");
        gallery.className = "img-gallery";
        const total = (item.context_image_files || item.context_images || []).length;
        gallery.textContent = `附图 ${total} 张，展开加载缩略图…`;
        let galleryLoaded = false;
        detail.addEventListener("toggle", () => {
          if (!detail.open || galleryLoaded) return;
          galleryLoaded = true;
          loadGallery(gallery, item);
        });
        detail.appendChild(gallery);
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
          const data = await bridge.apiPost("feedback_todo/resummarize", {
            id: item.id,
          });
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
      delBtn.addEventListener("click", () => {
        twoStepConfirm(delBtn, "确认删除？", async () => {
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
        doneBtn.addEventListener("click", () => {
          const result = resultBox.value.trim();
          if (!result) {
            setStatus("请先填写处理结果", true);
            return;
          }
          twoStepConfirm(doneBtn, "确认发送？", async () => {
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
        });
        area.appendChild(resultBox);
        area.appendChild(doneBtn);
        card.appendChild(area);
      }

      return card;
    }

    await load();
    return () => {
      /* 切换模块时无需额外清理 */
    };
  },
};
