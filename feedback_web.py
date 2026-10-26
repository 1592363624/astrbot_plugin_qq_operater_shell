"""
网页反馈服务模块

给群友提供一个免登录的 H5 反馈表单，收集「问题描述 + 截图 + 发生时间」，
提交后直接进入与 /加待办 相同的待办列表，由管理员统一处理。

设计要点：
- 复用 AstrBot Dashboard 的 HTTP 端口，不额外监听新端口。Dashboard 的鉴权
  中间件只拦截 /api 前缀，因此 /feedback 属于公开路径，外部用户无需登录
  即可打开（页面本身靠访问令牌做轻量保护）。
- 路由必须插入到静态资源 catch-all 之前，否则会被 SPA 兜底路由吃掉。
- 图片以 base64 随 JSON 提交，落盘到待办附图目录，与 /加待办 的图片共用
  同一套存储与清理逻辑（WebUI 可直接查看）。
"""

import base64
import binascii
import re
import secrets
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .feedback_todo import FeedbackTodoService
from .qq_operater_service import QQOperaterService, get_conf


class FeedbackWebService:
    """网页反馈服务类"""

    # 访问令牌的 KV 键名（首次访问时自动生成）
    TOKEN_KEY = "feedback_web_token"
    # 页面与接口路由（均不以 /api 开头，因此不受 Dashboard 鉴权中间件拦截）
    PAGE_ROUTE = "/feedback"
    API_ROUTE = "/feedback/api"
    # 页面模板位置
    HTML_PATH = Path(__file__).resolve().parent / "web" / "feedback.html"

    # 问题类型文案（与待办展示共用同一份定义，避免两处维护）
    TYPE_LABELS = FeedbackTodoService.WEB_TYPE_LABELS

    MAX_DESC = 2000
    MAX_STEPS = 1200
    MAX_CONTACT = 80
    # 单 IP 限流：窗口期内最多提交次数
    RATE_WINDOW = 600
    RATE_MAX = 5
    # 单张图片送入 LLM 的体积上限（base64 膨胀后仍能通过多数模型网关）
    MAX_LLM_IMAGE_BYTES = 3 * 1024 * 1024

    _html_cache = None
    _html_mtime = 0.0
    _rate_buckets = {}

    # ==================== 配置读取 ====================

    @staticmethod
    def _max_images(plugin):
        """单次反馈允许上传的图片张数"""
        try:
            value = int(get_conf(plugin, "web_max_images", 6))
        except (TypeError, ValueError):
            value = 6
        return max(1, min(value, FeedbackTodoService.MAX_LLM_IMAGES))

    @staticmethod
    def _max_image_mb(plugin):
        """单张图片体积上限（MB）"""
        try:
            value = int(get_conf(plugin, "web_max_image_mb", 5))
        except (TypeError, ValueError):
            value = 5
        return max(1, min(value, 20))

    # ==================== 访问令牌 ====================

    @staticmethod
    async def ensure_token(plugin):
        """读取访问令牌，不存在时自动生成并持久化"""
        token = await plugin.get_kv_data(FeedbackWebService.TOKEN_KEY, None)
        if not isinstance(token, str) or len(token) < 16:
            token = secrets.token_urlsafe(18)
            try:
                await plugin.put_kv_data(FeedbackWebService.TOKEN_KEY, token)
            except Exception as e:
                logger.error(f"网页反馈：保存访问令牌失败: {e}")
        return token

    @staticmethod
    async def reset_token(plugin):
        """重置访问令牌，旧链接立即失效"""
        token = secrets.token_urlsafe(18)
        try:
            await plugin.put_kv_data(FeedbackWebService.TOKEN_KEY, token)
        except Exception as e:
            logger.error(f"网页反馈：重置访问令牌失败: {e}")
        return token

    @staticmethod
    async def verify_token(plugin, token):
        """校验访问令牌；配置关闭校验时一律放行"""
        if not get_conf(plugin, "web_require_token", True):
            return True
        if not token:
            return False
        expected = await FeedbackWebService.ensure_token(plugin)
        return secrets.compare_digest(str(token), str(expected))

    # ==================== 对外地址 ====================

    @staticmethod
    def _guess_base_url():
        """从 AstrBot Dashboard 配置推断本机访问地址

        Returns:
            str: 形如 http://127.0.0.1:6185，推断失败返回空字符串
        """
        try:
            from astrbot.dashboard import server as dashboard_server

            adapter = getattr(dashboard_server, "APP", None)
            dashboard = getattr(adapter, "_dashboard_server", None)
            config = getattr(dashboard, "config", None)
            if not config:
                return ""
            section = config.get("dashboard")
            if not isinstance(section, dict):
                return ""
            port = section.get("port") or 6185
            host = str(section.get("host") or "").strip()
            # 监听 0.0.0.0 / :: 时无法推断外部可访问的主机名，退化为本机回环
            if host in ("", "0.0.0.0", "::", "[::]"):
                host = "127.0.0.1"
            return f"http://{host}:{port}"
        except Exception as e:
            logger.debug(f"网页反馈：推断对外地址失败: {e}")
            return ""

    @staticmethod
    async def build_link(plugin):
        """生成带令牌的反馈地址

        Returns:
            tuple: (地址或空字符串, 提示信息)
        """
        token = await FeedbackWebService.ensure_token(plugin)
        base = str(get_conf(plugin, "web_base_url", "") or "").strip().rstrip("/")
        hint = ""
        if not base:
            base = FeedbackWebService._guess_base_url()
            if base:
                hint = (
                    "ℹ️ 该地址由面板配置推断得出，若外部访问不通，"
                    "请在插件配置「反馈页对外地址」中填写你的公网域名/IP。"
                )
        if not base:
            return "", "尚未配置「反馈页对外地址」，无法生成可访问的链接"

        if get_conf(plugin, "web_require_token", True):
            return f"{base}{FeedbackWebService.PAGE_ROUTE}?t={token}", hint
        return f"{base}{FeedbackWebService.PAGE_ROUTE}", hint

    # ==================== 路由注册 ====================

    @staticmethod
    def register(plugin):
        """把公开反馈页挂到 AstrBot Dashboard 的 ASGI 应用上

        Returns:
            bool: 是否注册成功
        """
        try:
            from astrbot.dashboard import server as dashboard_server
        except Exception as e:
            logger.warning(f"网页反馈：导入 Dashboard 模块失败，网页反馈不可用: {e}")
            return False

        adapter = getattr(dashboard_server, "APP", None)
        app = getattr(adapter, "_app", None)
        if app is None:
            logger.warning(
                "网页反馈：未找到 Dashboard 应用实例（可能未启用面板），网页反馈不可用"
            )
            return False

        try:
            page, meta, submit = FeedbackWebService._make_handlers(plugin)
            definitions = [
                (FeedbackWebService.PAGE_ROUTE, page, ["GET"]),
                (f"{FeedbackWebService.API_ROUTE}/meta", meta, ["GET"]),
                (f"{FeedbackWebService.API_ROUTE}/submit", submit, ["POST"]),
            ]
            paths = {item[0] for item in definitions}
            # 插件重载时先摘掉旧路由，避免重复注册、以及闭包仍指向已销毁的实例
            app.router.routes[:] = [
                route
                for route in app.router.routes
                if getattr(route, "path", None) not in paths
            ]
            for path, handler, methods in definitions:
                # 必须插到最前面：静态资源路由带 /{path:path} 兜底，排在它后面
                # 的 GET 路由永远不会被匹配到
                app.router.routes.insert(
                    0,
                    APIRoute(
                        path,
                        endpoint=handler,
                        methods=methods,
                        include_in_schema=False,
                    ),
                )
        except Exception as e:
            logger.error(f"网页反馈：注册路由失败: {e}")
            return False

        logger.info(f"网页反馈：已挂载公开反馈页 {FeedbackWebService.PAGE_ROUTE}")
        return True

    @staticmethod
    def unregister():
        """摘掉公开反馈页路由（插件卸载/禁用时调用，避免留下指向旧实例的闭包）"""
        try:
            from astrbot.dashboard import server as dashboard_server

            adapter = getattr(dashboard_server, "APP", None)
            app = getattr(adapter, "_app", None)
            if app is None:
                return
            paths = {
                FeedbackWebService.PAGE_ROUTE,
                f"{FeedbackWebService.API_ROUTE}/meta",
                f"{FeedbackWebService.API_ROUTE}/submit",
            }
            app.router.routes[:] = [
                route
                for route in app.router.routes
                if getattr(route, "path", None) not in paths
            ]
            logger.info("网页反馈：已摘除公开反馈页路由")
        except Exception as e:
            logger.debug(f"网页反馈：摘除路由失败: {e}")

    @staticmethod
    def _make_handlers(plugin):
        """构造三个 HTTP 处理函数（闭包持有插件实例，便于读取配置与 KV）"""

        async def page(request: Request):
            """反馈表单页面"""
            if not get_conf(plugin, "web_enabled", True):
                return PlainTextResponse("网页反馈功能已关闭", status_code=404)
            html = FeedbackWebService._load_html()
            if html is None:
                return PlainTextResponse(
                    "反馈页面模板缺失，请联系管理员检查插件文件", status_code=500
                )
            return HTMLResponse(
                html,
                headers={
                    "Cache-Control": "no-store",
                    "X-Robots-Tag": "noindex, nofollow",
                    "Referrer-Policy": "no-referrer",
                },
            )

        async def meta(request: Request):
            """页面初始化：校验令牌并下发表单约束"""
            if not get_conf(plugin, "web_enabled", True):
                return JSONResponse(
                    {"ok": False, "message": "网页反馈功能已关闭"}, status_code=403
                )
            token = request.query_params.get("t", "")
            if not await FeedbackWebService.verify_token(plugin, token):
                return JSONResponse(
                    {
                        "ok": False,
                        "message": "链接无效或已失效，请回到 QQ 群发送「/反馈」获取新地址",
                    },
                    status_code=403,
                )
            return JSONResponse(
                {
                    "ok": True,
                    "max_images": FeedbackWebService._max_images(plugin),
                    "max_image_mb": FeedbackWebService._max_image_mb(plugin),
                    "footer": "提交后会直接进入管理员待办列表，处理完成后会通知你。",
                }
            )

        async def submit(request: Request):
            """接收反馈内容"""
            if not get_conf(plugin, "web_enabled", True):
                return JSONResponse(
                    {"ok": False, "message": "网页反馈功能已关闭"}, status_code=403
                )

            # 先校验令牌再限流：令牌错误属于无效请求，不应占用提交配额
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "message": "请求格式错误"}, status_code=400
                )
            if not isinstance(payload, dict):
                return JSONResponse(
                    {"ok": False, "message": "请求格式错误"}, status_code=400
                )

            token = str(payload.get("token") or "")
            if not await FeedbackWebService.verify_token(plugin, token):
                return JSONResponse(
                    {"ok": False, "message": "链接无效或已失效，请重新获取反馈地址"},
                    status_code=403,
                )

            # 取真实来源 IP：反代场景下以 X-Forwarded-For 首个地址为准
            ip = (request.client.host if request.client else "") or ""
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                ip = forwarded.split(",", 1)[0].strip() or ip
            if not FeedbackWebService._allow(ip):
                return JSONResponse(
                    {"ok": False, "message": "提交过于频繁，请稍后再试"}, status_code=429
                )

            try:
                ok, message, todo_id = await FeedbackWebService.create_feedback(
                    plugin, payload
                )
            except Exception as e:
                logger.error(f"网页反馈：处理提交失败: {e}")
                return JSONResponse(
                    {"ok": False, "message": "服务端处理失败，请稍后重试或直接在群里反馈"},
                    status_code=500,
                )
            if not ok:
                return JSONResponse({"ok": False, "message": message}, status_code=400)
            return JSONResponse({"ok": True, "id": todo_id})

        return page, meta, submit

    @staticmethod
    def _load_html():
        """读取页面模板，按修改时间做内存缓存（改完模板无需重启）"""
        path = FeedbackWebService.HTML_PATH
        try:
            mtime = path.stat().st_mtime
        except OSError as e:
            logger.error(f"网页反馈：页面模板不存在: {e}")
            return None
        if (
            FeedbackWebService._html_cache is not None
            and FeedbackWebService._html_mtime == mtime
        ):
            return FeedbackWebService._html_cache
        try:
            html = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"网页反馈：读取页面模板失败: {e}")
            return None
        FeedbackWebService._html_cache = html
        FeedbackWebService._html_mtime = mtime
        return html

    # ==================== 限流 ====================

    @staticmethod
    def _allow(ip):
        """滑动窗口限流，返回是否放行"""
        key = ip or "-"
        now = time.time()
        bucket = FeedbackWebService._rate_buckets.setdefault(key, [])
        bucket[:] = [t for t in bucket if now - t < FeedbackWebService.RATE_WINDOW]
        if len(bucket) >= FeedbackWebService.RATE_MAX:
            return False
        bucket.append(now)

        # 顺手清理空桶，避免字典随访问 IP 无限增长
        if len(FeedbackWebService._rate_buckets) > 2000:
            for stale in [
                k for k, v in FeedbackWebService._rate_buckets.items() if not v
            ]:
                FeedbackWebService._rate_buckets.pop(stale, None)
        return True

    # ==================== 反馈落库 ====================

    @staticmethod
    def _save_images(plugin, todo_id, images):
        """把网页上传的图片写入待办附图目录

        Args:
            plugin: 插件实例
            todo_id: 待办编号（决定目录名）
            images: [(序号, mime, bytes)]

        Returns:
            list: 成功保存的文件名，如 ["1.jpg", "2.png"]
        """
        if not images:
            return []
        dir_path = FeedbackTodoService._todo_image_dir(plugin, todo_id)
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"网页反馈：创建附图目录失败: {e}")
            return []

        saved = []
        for index, mime, data in images:
            name = f"{index}{FeedbackTodoService.IMAGE_EXTS.get(mime, '.jpg')}"
            try:
                (dir_path / name).write_bytes(data)
                saved.append(name)
            except OSError as e:
                logger.warning(f"网页反馈：保存附图 {name} 失败: {e}")
        return saved

    @staticmethod
    def _parse_blocks(payload):
        """把请求体解析成有序的图文块

        优先读 content（[{"type": "text", "text": ...}, {"type": "image", "id": 1}]），
        缺失时回退到 description + images 的老格式。

        Returns:
            list: 文本块 {"type": "text", "text": str} 或图片块 {"type": "image", "id": int}
        """
        blocks = []
        raw = payload.get("content")
        if isinstance(raw, list):
            for block in raw[:400]:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = str(block.get("text") or "")
                    if text:
                        blocks.append({"type": "text", "text": text})
                elif btype == "image":
                    img_id = FeedbackTodoService._to_int(block.get("id"))
                    if img_id:
                        blocks.append({"type": "image", "id": img_id})
        if blocks:
            return blocks

        # 老格式：文字在前、图片整体在后，图片按数组下标引用 1..N
        desc = str(payload.get("description") or "")
        if desc:
            blocks.append({"type": "text", "text": desc})
        raw_images = payload.get("images")
        if isinstance(raw_images, list):
            for index in range(1, len(raw_images) + 1):
                blocks.append({"type": "image", "id": index})
        return blocks

    @staticmethod
    def _decode_images(payload, max_bytes):
        """解码并校验图片，返回 {id: (mime, bytes)}

        base64 解码后按文件头识别真实类型，改后缀伪装成图片的文件会被丢弃。
        """
        result = {}
        raw_images = payload.get("images")
        if not isinstance(raw_images, list):
            return result
        for index, raw in enumerate(raw_images):
            if not isinstance(raw, dict):
                continue
            img_id = FeedbackTodoService._to_int(raw.get("id")) or (index + 1)
            try:
                data = base64.b64decode(str(raw.get("data") or ""), validate=True)
            except (binascii.Error, ValueError):
                continue
            if not data or len(data) > max_bytes:
                continue
            mime = FeedbackTodoService._sniff_image_mime(data)
            if not mime:
                continue
            result[img_id] = (mime, data)
        return result

    @staticmethod
    async def create_feedback(plugin, payload):
        """校验请求内容并落库为一条反馈待办

        Returns:
            tuple: (是否成功, 提示消息, 待办编号或 None)
        """
        issue_type = str(payload.get("type") or "other").strip().lower()
        if issue_type not in FeedbackWebService.TYPE_LABELS:
            issue_type = "other"

        steps = str(payload.get("steps") or "").strip()[: FeedbackWebService.MAX_STEPS]
        contact = str(payload.get("contact") or "").strip()[
            : FeedbackWebService.MAX_CONTACT
        ]

        # 发生时间：0 表示未填，按提交时间记录；明显异常的值也归一到当前时间
        now = int(time.time())
        occurred = FeedbackTodoService._to_int(payload.get("occurred_at")) or 0
        if occurred <= 0 or occurred > now + 86400:
            occurred = now

        max_images = FeedbackWebService._max_images(plugin)
        max_bytes = FeedbackWebService._max_image_mb(plugin) * 1024 * 1024

        # 1. 解析图文块并校验文字长度
        blocks = FeedbackWebService._parse_blocks(payload)
        text_len = sum(len(b["text"]) for b in blocks if b["type"] == "text")
        if text_len > FeedbackWebService.MAX_DESC:
            return False, f"问题描述不能超过 {FeedbackWebService.MAX_DESC} 字", None
        if not text_len and not any(b["type"] == "image" for b in blocks):
            return False, "请填写问题描述", None

        # 2. 校验图片
        decoded = FeedbackWebService._decode_images(payload, max_bytes)

        # 3. 丢掉校验失败的图片并重新编号，保证 [图片N] 与实际落盘顺序一致
        final_blocks = []
        images = []
        for block in blocks:
            if block["type"] == "text":
                final_blocks.append(block)
                continue
            if len(images) >= max_images:
                continue
            entry = decoded.get(block["id"])
            if entry is None:
                continue
            seq = len(images) + 1
            images.append((seq, entry[0], entry[1]))
            final_blocks.append({"type": "image", "index": seq})

        # 4. 拼出带 [图片N] 占位的正文，图文顺序与用户填写时一致
        parts = []
        for block in final_blocks:
            if block["type"] == "text":
                parts.append(block["text"])
            else:
                parts.append(f"[图片{block['index']}]")
        full_text = "".join(parts).strip()
        if not full_text:
            return False, "请填写问题描述", None

        store = await FeedbackTodoService._load_store(plugin)
        todo_id = store["next_id"]
        store["next_id"] = todo_id + 1

        image_files = FeedbackWebService._save_images(plugin, todo_id, images)

        # 联系方式是纯数字时视为 QQ 号，处理完成后可直接私聊回复
        reporter_id = contact if re.fullmatch(r"\d{5,12}", contact) else ""
        reporter_name = contact or "网页用户"

        summary = ""
        if get_conf(plugin, "todo_use_llm", True):
            summary = await FeedbackWebService._summarize(
                plugin, issue_type, full_text, steps, occurred, contact, images
            )

        store["items"].append(
            {
                "id": todo_id,
                "status": "pending",
                "source": "web",
                "created_time": now,
                "message_type": "web",
                "group_id": "",
                "group_name": "",
                "reporter_id": reporter_id,
                "reporter_name": reporter_name,
                "message_id": "",
                "message_time": occurred,
                "issue_type": issue_type,
                "target_text": FeedbackTodoService._truncate(
                    full_text, FeedbackTodoService.MAX_CONTEXT_TEXT
                ),
                "content_blocks": final_blocks,
                "reason": "",
                "steps": steps,
                "contact": contact,
                "llm_summary": summary,
                "context_text": "",
                "context_images": [],
                "context_image_files": image_files,
                "closed_time": 0,
                "result": "",
            }
        )
        await FeedbackTodoService._save_store(plugin, store)

        logger.info(
            f"网页反馈：收到反馈 #{todo_id}"
            f"（{FeedbackWebService.TYPE_LABELS[issue_type]}，"
            f"{len(image_files)} 张图，来自 {reporter_name}）"
        )
        await FeedbackWebService._notify_owners(
            plugin, todo_id, summary or full_text, issue_type
        )
        return True, "", todo_id

    @staticmethod
    async def _summarize(plugin, issue_type, text, steps, occurred, contact, images):
        """调用 LLM 生成反馈摘要，失败返回空字符串

        text 里带 [图片N] 占位标记，与随请求附送的截图顺序一一对应；
        截图以 data URI 形式附送，模型不支持图片时由
        FeedbackTodoService._generate_summary 自动降级为纯文本重试。
        """
        provider_id = await FeedbackTodoService._resolve_llm_provider_id(plugin)
        if not provider_id:
            logger.warning("网页反馈：未配置可用的聊天模型，跳过 AI 摘要")
            return ""

        system_prompt = (
            "你是用户反馈整理助手。用户通过网页表单提交了一条问题反馈，"
            "请把它整理成一条简洁的待办摘要：\n"
            "1. 第一行给出反馈类型：Bug反馈 / 功能建议 / 求助 / 其他；\n"
            "2. 随后用1~3句话概括问题核心（谁遇到、什么现象、影响范围）；\n"
            "3. 如附图中有报错信息、界面状态等关键细节，务必结合图片实际内容说明，"
            "不要无视图片凭空猜测；\n"
            "4. 如有报错文本、复现步骤等关键信息请简要保留。\n"
            "总长度不超过150字，直接输出总结内容，不要输出标题、编号或客套话。"
        )
        prompt = "\n".join(
            [
                f"问题类型：{FeedbackWebService.TYPE_LABELS.get(issue_type, '其他')}",
                f"发生时间：{FeedbackTodoService._fmt_full_time(occurred)}",
                f"问题描述（[图片N] 表示该位置有截图，截图按 N 的顺序附送）：{text}",
                f"复现步骤：{steps or '未填写'}",
                f"联系方式：{contact or '未填写'}",
                f"附图数量：{len(images)} 张",
            ]
        )

        image_urls = []
        for _index, mime, data in images[:3]:
            if len(data) > FeedbackWebService.MAX_LLM_IMAGE_BYTES:
                continue
            image_urls.append(
                f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            )

        return await FeedbackTodoService._generate_summary(
            plugin, provider_id, system_prompt, prompt, image_urls
        )

    @staticmethod
    async def _notify_owners(plugin, todo_id, summary, issue_type):
        """给管理员（todo_owner_qqs）发私聊提醒，失败不影响提交结果"""
        if not get_conf(plugin, "web_notify_admins", True):
            return
        owners = FeedbackTodoService._parse_owner_qqs(plugin)
        if not owners:
            return
        try:
            client = await QQOperaterService.get_client(plugin, None)
            if not client:
                return
            text = "\n".join(
                [
                    f"📨 收到新的网页反馈 #{todo_id}",
                    f"类型：{FeedbackWebService.TYPE_LABELS.get(issue_type, '其他')}",
                    f"摘要：{FeedbackTodoService._truncate(summary, 120)}",
                    f"查看：/待办列表 {todo_id}",
                ]
            )
            for qq in owners[:3]:
                user_id = FeedbackTodoService._to_int(qq)
                if not user_id:
                    continue
                try:
                    await client.api.call_action(
                        "send_private_msg", user_id=user_id, message=text
                    )
                except Exception as e:
                    logger.warning(f"网页反馈：通知管理员 {qq} 失败: {e}")
        except Exception as e:
            logger.warning(f"网页反馈：发送管理员通知失败: {e}")

    # ==================== 指令处理 ====================

    @staticmethod
    async def handle_feedback_link(plugin, event: AstrMessageEvent):
        """处理 /反馈 指令：把反馈页地址发给用户（所有人可用）"""
        if not get_conf(plugin, "web_enabled", True):
            yield event.make_result().message("⚠️ 网页反馈功能未启用，请联系管理员")
            return

        link, hint = await FeedbackWebService.build_link(plugin)
        if not link:
            yield event.make_result().message(
                f"⚠️ {hint}\n"
                "请在插件配置 → 网页反馈 → 反馈页对外地址 中填写面板地址，"
                "例如 http://1.2.3.4:6185"
            )
            return

        lines = [
            "📝 反馈问题请打开这个页面填写：",
            link,
            "",
            "支持上传截图、填写问题发生时间；提交后会直接进入管理员待办列表。",
        ]
        if hint:
            lines.append(hint)
        yield event.make_result().message("\n".join(lines))

    @staticmethod
    async def handle_admin_link(plugin, event: AstrMessageEvent):
        """处理 /反馈链接 指令（管理员）：查看或重置反馈地址"""
        args = event.message_str.split()
        if len(args) >= 2 and args[1] in ("重置", "reset"):
            await FeedbackWebService.reset_token(plugin)
            link, _hint = await FeedbackWebService.build_link(plugin)
            yield event.make_result().message(
                "🔄 访问令牌已重置，之前发出去的链接全部失效。\n"
                f"新地址：\n{link or '（未配置对外地址，请先在插件配置中填写）'}"
            )
            return

        link, hint = await FeedbackWebService.build_link(plugin)
        if not link:
            yield event.make_result().message(
                f"⚠️ {hint}\n"
                "请在插件配置 → 网页反馈 → 反馈页对外地址 中填写面板地址，"
                "例如 http://1.2.3.4:6185"
            )
            return
        lines = [
            "🔗 当前反馈页地址：",
            link,
            "",
            "把上面这条发给群友，他们打开就能填反馈。",
            "重置令牌：/反馈链接 重置（旧链接会立即失效）",
        ]
        if hint:
            lines.append(hint)
        yield event.make_result().message("\n".join(lines))
