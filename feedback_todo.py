"""
反馈待办服务模块

用于收集群友反馈的 Bug 和建议，核心流程：
- 管理员（插件配置 todo_owner_qqs 中登记的QQ）在群里引用某条消息并回复
  "加待办 [原因]"（原因可选），插件会：
  1. 调用协议端接口抓取被引用消息前后各 N 条聊天记录；
  2. 调用 LLM 评估聊天记录，生成反馈摘要；
  3. 以自增编号存入待办列表（KV 持久化），并记录来源群、报告人、
     被引用消息ID 等信息，供后续定位回复。
- 通过 /待办列表 查看待办列表与详情（带编号查看单条详情）。
- 处理完成后通过 /完成待办 <编号> <处理结果> 关闭待办，机器人会以
  引用回复的方式把处理结果回复给报告人的原始消息。

注意：触发QQ需要与机器人登录的QQ不同（机器人自发消息不会进入消息事件）。
"""

import re
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .qq_operater_service import QQOperaterService


class FeedbackTodoService:
    """反馈待办服务类"""

    STORE_KEY = "feedback_todo_store"
    ADD_KEYWORD = "加待办"
    # 存储的聊天记录文本最大长度，避免单条待办过大
    MAX_CONTEXT_TEXT = 4000
    # 单次随请求送入 LLM 的最大图片数（目标消息的截图优先保留）
    MAX_LLM_IMAGES = 8

    # ==================== 存储相关 ====================

    @staticmethod
    async def _load_store(plugin):
        """读取待办存储，格式：{"next_id": 自增编号, "items": [待办dict]}"""
        store = await plugin.get_kv_data(FeedbackTodoService.STORE_KEY, None)
        if not isinstance(store, dict) or not isinstance(store.get("items"), list):
            store = {"next_id": 1, "items": []}
        if not isinstance(store.get("next_id"), int) or store["next_id"] < 1:
            store["next_id"] = 1
        return store

    @staticmethod
    async def _save_store(plugin, store):
        """保存待办存储；失败时只记录日志，不影响指令结果"""
        try:
            await plugin.put_kv_data(FeedbackTodoService.STORE_KEY, store)
        except Exception as e:
            logger.error(f"反馈待办：保存待办数据失败: {e}")

    @staticmethod
    def _parse_owner_qqs(plugin):
        """解析配置的触发QQ列表

        配置为字符串列表（WebUI 列表编辑器）；兼容旧版逗号分隔字符串，
        以及列表元素为数字的情况。
        """
        raw = plugin.config.get("todo_owner_qqs", [])
        if isinstance(raw, str):
            return [q.strip() for q in raw.replace("，", ",").split(",") if q.strip()]
        if isinstance(raw, list):
            return [str(q).strip() for q in raw if str(q).strip()]
        return []

    @staticmethod
    def _truncate(text, length):
        """截断文本并追加省略标记"""
        text = str(text or "")
        if len(text) <= length:
            return text
        return text[:length] + "…"

    # ==================== 工具方法 ====================

    @staticmethod
    def _fmt_short_time(timestamp):
        """格式化时间戳为 MM-DD HH:MM"""
        try:
            return datetime.fromtimestamp(int(timestamp)).strftime("%m-%d %H:%M")
        except (ValueError, OSError, TypeError):
            return "未知"

    @staticmethod
    def _fmt_full_time(timestamp):
        """格式化时间戳为完整日期时间"""
        try:
            return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, TypeError):
            return "未知"

    @staticmethod
    def _extract_msg_text(raw_msg):
        """从协议端返回的原始消息中提取纯文本，富媒体段替换为占位符"""
        if not isinstance(raw_msg, dict):
            return ""
        message = raw_msg.get("message")
        text = ""
        if isinstance(message, list):
            parts = []
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                seg_type = seg.get("type")
                data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
                if seg_type == "text":
                    parts.append(str(data.get("text", "")))
                elif seg_type == "image":
                    parts.append("[图片]")
                elif seg_type == "face":
                    parts.append("[表情]")
                elif seg_type == "at":
                    parts.append(f"@{data.get('qq', '')}")
                elif seg_type == "record":
                    parts.append("[语音]")
                elif seg_type == "video":
                    parts.append("[视频]")
                elif seg_type == "file":
                    parts.append("[文件]")
                elif seg_type == "forward":
                    parts.append("[合并转发]")
                elif seg_type == "reply":
                    parts.append("[引用]")
                else:
                    parts.append(f"[{seg_type}]")
            text = "".join(parts)
        elif isinstance(message, str):
            text = message
        if not text.strip():
            text = str(raw_msg.get("raw_message", "") or "")
        # 字符串形式的消息是 CQ 码文本，做一次统一替换
        text = re.sub(r"\[CQ:image,[^\]]*\]", "[图片]", text)
        text = re.sub(r"\[CQ:record,[^\]]*\]", "[语音]", text)
        text = re.sub(r"\[CQ:video,[^\]]*\]", "[视频]", text)
        text = re.sub(r"\[CQ:file,[^\]]*\]", "[文件]", text)
        text = re.sub(r"\[CQ:forward,[^\]]*\]", "[合并转发]", text)
        text = re.sub(r"\[CQ:face,[^\]]*\]", "[表情]", text)
        text = re.sub(r"\[CQ:at,qq=([^\],]*)[^\]]*\]", r"@\1", text)
        text = re.sub(r"\[CQ:reply,[^\]]*\]", "[引用]", text)
        return text.replace("\n", " ").strip()

    @staticmethod
    def _extract_msg_images(raw_msg):
        """提取消息中所有可访问的图片URL（http/https），供 LLM 识别图片内容"""
        if not isinstance(raw_msg, dict):
            return []
        urls = []
        message = raw_msg.get("message")

        def _add(url):
            url = str(url or "").strip().rstrip('`')
            if url.startswith("http") and url not in urls:
                urls.append(url)

        if isinstance(message, list):
            for seg in message:
                if not isinstance(seg, dict) or seg.get("type") != "image":
                    continue
                data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
                _add(data.get("url"))
                if str(data.get("file", "")).startswith("http"):
                    _add(data.get("file"))
        elif isinstance(message, str):
            for cq in re.finditer(r"\[CQ:image,[^\]]*\]", message):
                m = re.search(r"(?:,|^)url=([^,\]]+)", cq.group(0)) or re.search(
                    r"file=(https?://[^,\]]+)", cq.group(0)
                )
                if m:
                    _add(m.group(1))
        return urls

    @staticmethod
    def _msg_sender_name(raw_msg):
        """从原始消息中提取发送者显示名（群名片 > 昵称 > QQ号）"""
        sender = raw_msg.get("sender") if isinstance(raw_msg.get("sender"), dict) else {}
        user_id = sender.get("user_id") or raw_msg.get("user_id") or ""
        return sender.get("card") or sender.get("nickname") or str(user_id), str(user_id)

    @staticmethod
    def _parse_history_response(ret):
        """把 get_*_msg_history 的返回值解析为消息列表，兼容多种返回格式"""
        if isinstance(ret, dict):
            if isinstance(ret.get("messages"), list):
                return ret["messages"]
            data = ret.get("data")
            if isinstance(data, dict) and isinstance(data.get("messages"), list):
                return data["messages"]
        if isinstance(ret, list):
            return ret
        return []

    @staticmethod
    def _to_int(value):
        """尽力把值转为 int，失败返回 None"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # ==================== 上下文抓取 ====================

    @staticmethod
    async def _get_quoted_msg_info(client, message_id):
        """调用 get_msg 获取被引用消息的详细信息"""
        try:
            ret = await client.api.call_action("get_msg", message_id=message_id)
            if isinstance(ret, dict) and ret.get("status") == "ok" and "data" in ret:
                ret = ret["data"]
            return ret if isinstance(ret, dict) else None
        except Exception as e:
            logger.error(f"反馈待办：获取被引用消息失败: {e}")
            return None

    @staticmethod
    async def _get_group_name(client, group_id):
        """尽力获取群名称，失败返回空字符串"""
        group_id_int = FeedbackTodoService._to_int(group_id)
        if not group_id_int:
            return ""
        try:
            ret = await client.api.call_action(
                "get_group_info", group_id=group_id_int, no_cache=False
            )
            if isinstance(ret, dict):
                if ret.get("status") == "ok" and "data" in ret:
                    ret = ret["data"]
                return str(ret.get("group_name", "") or "")
        except Exception as e:
            logger.warning(f"反馈待办：获取群名称失败: {e}")
        return ""

    @staticmethod
    async def _fetch_context_messages(
        client, group_id, private_user_id, seq, target_message_id, size, target_info=None
    ):
        """抓取被引用消息前后的聊天记录

        Args:
            client: QQ客户端实例
            group_id: 群号（群聊场景，私聊传空）
            private_user_id: 私聊对象QQ（私聊场景）
            seq: 被引用消息的序号（message_seq/real_id），可能为 None
            target_message_id: 被引用消息ID
            size: 前后各抓取的条数
            target_info: get_msg 返回的被引用消息原始数据，历史记录中
                缺少目标消息本身时用于补齐

        Returns:
            list: 按时间排序、以目标消息为中心的原始消息列表
        """
        before, after = [], []

        if group_id:
            group_id_int = FeedbackTodoService._to_int(group_id)
            seq_int = FeedbackTodoService._to_int(seq)
            if group_id_int and seq_int is not None:
                try:
                    ret = await client.api.call_action(
                        "get_group_msg_history",
                        group_id=group_id_int,
                        message_seq=seq_int,
                        count=size + 1,
                    )
                    before = FeedbackTodoService._parse_history_response(ret)
                except Exception as e:
                    logger.warning(f"反馈待办：获取群历史消息失败: {e}")
                # 该接口为 NapCat/Lagrange 扩展，取目标消息之后的记录，不支持则忽略
                try:
                    ret = await client.api.call_action(
                        "get_group_msgs_after_seq",
                        group_id=group_id_int,
                        message_seq=seq_int,
                        count=size,
                    )
                    after = FeedbackTodoService._parse_history_response(ret)
                except Exception as e:
                    logger.warning(
                        f"反馈待办：获取目标消息之后的记录失败（协议端可能不支持）: {e}"
                    )
            elif group_id_int:
                # 没有序号信息时，退化为拉取最近消息再按消息ID定位
                try:
                    ret = await client.api.call_action(
                        "get_group_msg_history",
                        group_id=group_id_int,
                        message_seq=0,
                        count=size * 4 + 20,
                    )
                    before = FeedbackTodoService._parse_history_response(ret)
                except Exception as e:
                    logger.warning(f"反馈待办：获取群最近消息失败: {e}")
        elif private_user_id:
            user_id_int = FeedbackTodoService._to_int(private_user_id)
            if user_id_int:
                try:
                    ret = await client.api.call_action(
                        "get_friend_msg_history",
                        user_id=user_id_int,
                        message_seq=FeedbackTodoService._to_int(seq) or 0,
                        count=size + 1,
                    )
                    before = FeedbackTodoService._parse_history_response(ret)
                except Exception as e:
                    logger.warning(f"反馈待办：获取私聊历史消息失败: {e}")

        # 合并去重并按时间排序
        merged = {}
        for msg in before + after:
            if not isinstance(msg, dict):
                continue
            mid = str(msg.get("message_id", ""))
            if mid and mid not in merged:
                merged[mid] = msg

        target_mid = str(target_message_id)
        if target_mid not in merged:
            # 部分协议端的历史记录不含目标消息本身，用 get_msg 的数据补齐
            if not isinstance(target_info, dict):
                return []
            injected = dict(target_info)
            injected["message_id"] = target_mid
            merged[target_mid] = injected

        msgs = sorted(
            merged.values(),
            key=lambda m: (
                m.get("time", 0) or 0,
                FeedbackTodoService._to_int(m.get("real_id")) or 0,
            ),
        )

        target_idx = next(
            i for i, m in enumerate(msgs) if str(m.get("message_id")) == target_mid
        )
        return msgs[max(0, target_idx - size) : target_idx + size + 1]

    @staticmethod
    def _build_transcript(context_msgs, target_message_id):
        """把上下文消息格式化为聊天记录文本，目标消息以 ⭐ 标记

        消息中的图片渲染为带全局编号的 [图片N] 占位符，并返回对应的图片URL列表；
        目标消息的图片优先分配编号（编号从1开始），确保附图数量受限时
        被引用消息的截图优先送入 LLM。

        Returns:
            tuple: (聊天记录文本, 图片URL列表，第i项对应 [图片i+1])
        """
        target_mid = str(target_message_id)
        image_urls = []

        def render(msg):
            text = FeedbackTodoService._extract_msg_text(msg)
            images = FeedbackTodoService._extract_msg_images(msg)
            if images:
                iterator = iter(images)

                def sub(_match):
                    url = next(iterator, None)
                    if not url:
                        return "[图片]"
                    image_urls.append(url)
                    return f"[图片{len(image_urls)}]"

                text = re.sub(r"\[图片\]", sub, text)
            return text

        # 先渲染目标消息为其图片分配靠前的编号，再渲染其余消息
        rendered = {}
        for msg in context_msgs:
            mid = str(msg.get("message_id"))
            if mid == target_mid:
                rendered[mid] = render(msg)
        for msg in context_msgs:
            mid = str(msg.get("message_id"))
            if mid != target_mid:
                rendered[mid] = render(msg)

        lines = []
        for msg in context_msgs:
            mid = str(msg.get("message_id"))
            time_str = FeedbackTodoService._fmt_short_time(msg.get("time", 0))
            name, user_id = FeedbackTodoService._msg_sender_name(msg)
            marker = " ⭐" if mid == target_mid else ""
            lines.append(
                f"[{time_str}] {name}({user_id}): {rendered.get(mid, '[未知消息]')}{marker}"
            )
        return "\n".join(lines), image_urls

    # ==================== LLM 评估 ====================

    @staticmethod
    async def _resolve_llm_provider_id(plugin, event=None):
        """解析可用的聊天模型ID；优先取事件会话模型，其次取全局默认模型"""
        try:
            if event is not None:
                provider_id = await plugin.context.get_current_chat_provider_id(
                    event.unified_msg_origin
                )
                if provider_id:
                    return provider_id
        except Exception as e:
            logger.warning(f"反馈待办：获取会话聊天模型失败: {e}")
        try:
            prov = plugin.context.get_using_provider(None)
            return prov.meta().id if prov else None
        except Exception as e:
            logger.warning(f"反馈待办：获取默认聊天模型失败: {e}")
            return None

    @staticmethod
    def _build_summary_prompts(transcript, target_desc, reason):
        """构建反馈摘要的 system prompt 与用户 prompt"""
        system_prompt = (
            "你是QQ群反馈整理助手。群管理员引用了聊天中的一条消息并标记为反馈待办，"
            "请根据目标消息及其上下文聊天记录总结这条反馈：\n"
            "1. 第一行给出反馈类型：Bug反馈 / 功能建议 / 求助 / 其他；\n"
            "2. 随后用1~3句话概括反馈核心内容（谁、反馈了什么问题、关键细节）；\n"
            "3. 记录中的 [图片N] 标记对应随请求附送的图片，截图里可能有报错信息、"
            "界面状态、聊天记录等关键细节，务必结合图片实际内容一起分析，"
            "不要无视图片凭空猜测；\n"
            "4. 如有报错信息、复现步骤等关键细节请简要保留。\n"
            "总长度不超过150字，直接输出总结内容，不要输出标题、编号或客套话。"
        )
        prompt = "\n\n".join(
            [
                f"管理员补充说明：{reason or '无'}",
                f"目标消息（管理员所引用的）：\n{target_desc}",
                f"目标消息前后的聊天记录：\n{transcript or '（无上下文记录）'}",
            ]
        )
        return system_prompt, prompt

    @staticmethod
    async def _generate_summary(
        plugin, provider_id, system_prompt, prompt, image_urls=None
    ):
        """调用 LLM 生成摘要；附图失败自动降级纯文本重试，失败返回空字符串"""
        images = [str(u) for u in (image_urls or []) if str(u).startswith("http")]

        async def _generate(with_images):
            llm_resp = await plugin.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                image_urls=images if with_images else None,
            )
            return str(getattr(llm_resp, "completion_text", "") or "").strip()

        try:
            try:
                summary = await _generate(bool(images))
            except Exception as e:
                if not images:
                    raise
                logger.warning(f"反馈待办：附带图片请求失败，降级为纯文本重试: {e}")
                summary = await _generate(False)
            return FeedbackTodoService._truncate(summary, 500)
        except Exception as e:
            logger.error(f"反馈待办：LLM 分析失败: {e}")
            return ""

    @staticmethod
    async def _llm_summarize(
        plugin, event, transcript, target_desc, reason, image_urls=None
    ):
        """调用 LLM 评估聊天记录并生成反馈摘要，失败返回空字符串

        聊天记录中的截图以 [图片N] 标记，并随请求附送给 LLM 识别图片内容；
        若模型不支持图片等原因导致请求失败，自动降级为纯文本重试。
        """
        if not plugin.config.get("todo_use_llm", True):
            return ""
        provider_id = await FeedbackTodoService._resolve_llm_provider_id(plugin, event)
        if not provider_id:
            logger.warning("反馈待办：未配置可用的聊天模型，跳过 LLM 摘要")
            return ""
        system_prompt, prompt = FeedbackTodoService._build_summary_prompts(
            transcript, target_desc, reason
        )
        return await FeedbackTodoService._generate_summary(
            plugin, provider_id, system_prompt, prompt, image_urls
        )

    # ==================== 添加待办 ====================

    @staticmethod
    async def handle_add_todo_by_quote(plugin, event: AstrMessageEvent):
        """监听管理员引用消息回复"加待办 [原因]"添加反馈待办

        未命中触发条件时静默返回，不影响其他消息的正常处理。
        """
        from astrbot.core.message.components import Reply

        # 权限校验：仅配置中登记的QQ可用
        owner_qqs = FeedbackTodoService._parse_owner_qqs(plugin)
        if not owner_qqs:
            return
        sender_id = event.get_sender_id()
        if not sender_id or str(sender_id) not in owner_qqs:
            return

        # 必须是引用（回复）消息
        reply_comp = None
        for component in event.get_messages():
            if isinstance(component, Reply):
                reply_comp = component
                break
        if reply_comp is None or reply_comp.id is None:
            return

        # 匹配"加待办 [原因]"，容忍唤醒前缀（/ 或 .）
        # QQ 引用回复他人消息时，客户端会自动在文本前带上"@被引用人"（引用自己的
        # 消息则没有该前缀），需剥离后再匹配关键词，否则引用别人消息时永远无法触发
        text = event.message_str.strip()
        text = re.sub(r"^(?:@\S+\s*)+", "", text).strip()
        if text[:1] in ("/", ".", "、"):
            text = text[1:].strip()
        if not text.startswith(FeedbackTodoService.ADD_KEYWORD):
            logger.debug(
                f"反馈待办：收到管理员的引用消息但未匹配到"
                f"'{FeedbackTodoService.ADD_KEYWORD}'关键词，原文：{event.message_str!r}"
            )
            return
        reason = text[len(FeedbackTodoService.ADD_KEYWORD) :].strip()

        # 已确认触发，后续无论成败都终止事件传播，避免内容再进入 LLM 等流程
        # todo_reply_feedback 关闭时静默处理，仅通过日志记录结果
        reply_feedback = plugin.config.get("todo_reply_feedback", True)
        if reply_feedback:
            yield event.make_result().message("⏳ 正在抓取聊天上下文并分析反馈内容…")
        try:
            result_msg = await FeedbackTodoService._create_todo_from_quote(
                plugin, event, reply_comp, reason
            )
        except Exception as e:
            logger.error(f"反馈待办：添加待办异常: {e}")
            result_msg = f"❌ 添加反馈待办失败：{e}"
        logger.info(f"反馈待办：{result_msg}")
        if reply_feedback:
            yield event.make_result().message(result_msg)
        event.stop_event()

    @staticmethod
    async def _create_todo_from_quote(plugin, event, reply_comp, reason):
        """执行添加待办的完整流程，返回用于回复的确认文本"""
        client = await QQOperaterService.get_client(plugin, event)
        quoted_id = reply_comp.id

        quoted_info = None
        if client:
            quoted_info = await FeedbackTodoService._get_quoted_msg_info(client, quoted_id)

        # 被引用消息基本信息：优先取协议端返回，缺失时回退到 Reply 组件自带的数据
        if quoted_info:
            sender = quoted_info.get("sender") if isinstance(quoted_info.get("sender"), dict) else {}
            reporter_id = str(sender.get("user_id") or reply_comp.sender_id or "")
            reporter_name = (
                sender.get("card") or sender.get("nickname") or reply_comp.sender_nickname
            )
            if not reporter_name:
                reporter_name = reporter_id
            target_text = (
                FeedbackTodoService._extract_msg_text(quoted_info)
                or str(reply_comp.message_str or "")
            )
            seq = quoted_info.get("message_seq") or quoted_info.get("real_id")
            group_id = str(quoted_info.get("group_id") or "")
        else:
            reporter_id = str(reply_comp.sender_id or "")
            reporter_name = str(reply_comp.sender_nickname or "") or reporter_id
            target_text = str(reply_comp.message_str or "")
            seq = None
            group_id = ""
        if not group_id:
            group_id = str(event.get_group_id() or "")
        message_type = "group" if group_id else "private"

        try:
            size = int(plugin.config.get("todo_context_size", 10))
        except (TypeError, ValueError):
            size = 10
        size = max(1, min(size, 50))

        # 抓取上下文聊天记录
        context_msgs = []
        transcript = ""
        image_urls = []
        if client:
            context_msgs = await FeedbackTodoService._fetch_context_messages(
                client,
                group_id if message_type == "group" else "",
                reporter_id if message_type == "private" else "",
                seq,
                quoted_id,
                size,
                target_info=quoted_info,
            )
            transcript, image_urls = FeedbackTodoService._build_transcript(
                context_msgs, quoted_id
            )

        # 图片数量截断；目标消息的截图已优先编号，截断只影响靠后的图片
        llm_images = image_urls[: FeedbackTodoService.MAX_LLM_IMAGES]

        # LLM 评估
        target_desc = f"{reporter_name}({reporter_id})：{target_text or '[非文本消息]'}"
        llm_summary = ""
        if transcript:
            llm_summary = await FeedbackTodoService._llm_summarize(
                plugin, event, transcript, target_desc, reason, llm_images
            )

        # 群名称
        group_name = ""
        if client and group_id:
            group_name = await FeedbackTodoService._get_group_name(client, group_id)

        # 查重与入库
        store = await FeedbackTodoService._load_store(plugin)
        dup = next(
            (
                item
                for item in store["items"]
                if str(item.get("message_id")) == str(quoted_id)
                and item.get("status") == "pending"
            ),
            None,
        )
        if dup:
            return f"该消息已在待办 #{dup['id']} 中（未完成），无需重复添加"

        todo_id = store["next_id"]
        store["next_id"] = todo_id + 1
        store["items"].append(
            {
                "id": todo_id,
                "status": "pending",
                "created_time": int(time.time()),
                "message_type": message_type,
                "group_id": group_id,
                "group_name": group_name,
                "reporter_id": reporter_id,
                "reporter_name": reporter_name,
                "message_id": str(quoted_id),
                "message_time": int(quoted_info.get("time", 0) or 0)
                if quoted_info
                else 0,
                "target_text": FeedbackTodoService._truncate(target_text, 500),
                "reason": FeedbackTodoService._truncate(reason, 300),
                "llm_summary": llm_summary,
                "context_text": FeedbackTodoService._truncate(
                    transcript, FeedbackTodoService.MAX_CONTEXT_TEXT
                ),
                "context_images": llm_images,
                "closed_time": 0,
                "result": "",
            }
        )
        await FeedbackTodoService._save_store(plugin, store)

        # 组装确认消息
        summary_preview = llm_summary or target_text or "[非文本消息]"
        lines = [f"✅ 已添加反馈待办 #{todo_id}"]
        if message_type == "group":
            source = f"群 {group_name or '未知群'}({group_id})"
        else:
            source = "私聊"
        lines.append(f"📍 来源：{source} · {reporter_name}({reporter_id})")
        lines.append(f"📝 摘要：{FeedbackTodoService._truncate(summary_preview, 100)}")
        if llm_images:
            lines.append(f"📎 已附 {len(llm_images)} 张图片给 AI 识别")
        if reason:
            lines.append(f"🗒 补充原因：{reason}")
        if not context_msgs:
            lines.append("⚠️ 未能抓取到上下文记录，仅记录了被引用消息")
        lines.append(f"💡 处理完成后：/完成待办 {todo_id} <处理结果>")
        return "\n".join(lines)

    # ==================== 查询指令 ====================

    @staticmethod
    async def handle_todo_list(plugin, event: AstrMessageEvent):
        """处理 /待办列表 指令

        无参数列出全部未完成待办；参数为 全部/已完成 时按状态过滤；
        参数为编号时查看该条待办的详情（含聊天上下文与附图）。
        """
        cmd_params = event.message_str.split()
        if len(cmd_params) >= 2 and cmd_params[1].isdigit():
            async for result in FeedbackTodoService._query_todo_detail(
                plugin, event, int(cmd_params[1])
            ):
                yield result
            return

        status_filter = "pending"
        if len(cmd_params) >= 2:
            arg = cmd_params[1]
            if arg in ("全部", "所有", "all"):
                status_filter = "all"
            elif arg in ("已完成", "完成", "done", "已处理"):
                status_filter = "done"

        store = await FeedbackTodoService._load_store(plugin)
        items = sorted(store["items"], key=lambda x: x.get("id", 0))
        if status_filter != "all":
            items = [i for i in items if i.get("status") == status_filter]

        if not items:
            if status_filter == "pending":
                yield event.make_result().message("🎉 当前没有未完成的反馈待办")
            elif status_filter == "done":
                yield event.make_result().message("暂无已完成的待办")
            else:
                yield event.make_result().message("当前没有任何待办记录")
            return

        pending_count = sum(1 for i in store["items"] if i.get("status") == "pending")
        lines = [f"📋 反馈待办列表（未完成 {pending_count} 条）："]
        for item in items:
            status = "✅" if item.get("status") == "done" else "⏳"
            if item.get("message_type") == "group":
                source = f"{item.get('group_name') or '未知群'}({item.get('group_id')})"
            else:
                source = "私聊"
            summary = (
                item.get("llm_summary")
                or item.get("target_text")
                or item.get("reason")
                or "[无摘要]"
            )
            lines.append(
                f"#{item['id']} {status} {item.get('reporter_name', '?')} · {source} · "
                f"{FeedbackTodoService._fmt_short_time(item.get('created_time', 0))}"
            )
            lines.append(f"   {FeedbackTodoService._truncate(summary, 60)}")
        lines.append("查看详情：/待办列表 <编号>；完成并回复：/完成待办 <编号> <处理结果>")
        yield event.make_result().message("\n".join(lines))

    @staticmethod
    async def _query_todo_detail(plugin, event: AstrMessageEvent, todo_id):
        """输出单条待办的详情（含聊天上下文、附图链接、处理结果）"""
        store = await FeedbackTodoService._load_store(plugin)
        item = next((i for i in store["items"] if i.get("id") == todo_id), None)
        if not item:
            yield event.make_result().message(
                f"未找到待办 #{todo_id}，发送 /待办列表 可查看待办列表"
            )
            return

        status = "✅ 已完成" if item.get("status") == "done" else "⏳ 未完成"
        lines = [f"📋 反馈待办 #{todo_id}", f"状态：{status}"]
        lines.append(
            f"创建时间：{FeedbackTodoService._fmt_full_time(item.get('created_time', 0))}"
        )
        if item.get("message_type") == "group":
            lines.append(
                f"来源：群 {item.get('group_name') or '未知群'}({item.get('group_id')})"
            )
        else:
            lines.append("来源：私聊")
        lines.append(
            f"报告人：{item.get('reporter_name', '?')}({item.get('reporter_id', '?')})"
        )
        if item.get("message_time"):
            lines.append(
                f"被引用消息时间：{FeedbackTodoService._fmt_full_time(item.get('message_time'))}"
            )
        if item.get("reason"):
            lines.append(f"补充原因：{item['reason']}")
        if item.get("llm_summary"):
            lines.append(f"AI 摘要：{item['llm_summary']}")
        if item.get("target_text"):
            lines.append(f"被引用消息：{item.get('reporter_name', '?')}：{item['target_text']}")
        if item.get("context_text"):
            lines.append("────────────")
            lines.append("上下文聊天记录：")
            lines.append(item["context_text"])
        if item.get("context_images"):
            lines.append("────────────")
            lines.append(
                f"📎 附图 {len(item['context_images'])} 张（已随 AI 分析附送，[图片N] 对应下方第N条链接）："
            )
            for i, url in enumerate(item["context_images"], 1):
                lines.append(f"   图片{i}：{url}")
        if item.get("status") == "done":
            lines.append("────────────")
            lines.append(
                f"完成时间：{FeedbackTodoService._fmt_full_time(item.get('closed_time', 0))}"
            )
            lines.append(f"处理结果：{item.get('result', '')}")
        else:
            lines.append("────────────")
            lines.append(f"💡 处理完成后：/完成待办 {todo_id} <处理结果>")
        yield event.make_result().message("\n".join(lines))

    # ==================== 核心操作（无事件，供指令与 Web 页面复用） ====================

    @staticmethod
    async def complete_todo(plugin, todo_id, result_text, client=None):
        """完成待办并把处理结果引用回复给报告人

        Returns:
            tuple: (是否成功, 提示消息)
        """
        store = await FeedbackTodoService._load_store(plugin)
        item = next((i for i in store["items"] if i.get("id") == todo_id), None)
        if not item:
            return False, f"未找到待办 #{todo_id}，可用 /待办列表 查看"
        if item.get("status") == "done":
            return False, (
                f"待办 #{todo_id} 已完成"
                f"（{FeedbackTodoService._fmt_full_time(item.get('closed_time', 0))}），无需重复操作"
            )

        if client is None:
            client = await QQOperaterService.get_client(plugin, None)
        if not client:
            return False, "当前平台不支持发送回复消息，无法完成待办"

        # 构造引用回复消息：引用报告人的原始消息 + @报告人 + 处理结果
        reply_text = f"你之前反馈的问题已处理：\n{result_text}\n\n（反馈待办 #{todo_id}）"
        try:
            if item.get("message_type") == "group":
                group_id_int = FeedbackTodoService._to_int(item.get("group_id"))
                if not group_id_int:
                    raise ValueError("群号无效")
                chain = [{"type": "reply", "data": {"id": str(item.get("message_id"))}}]
                if item.get("reporter_id"):
                    chain.append(
                        {"type": "at", "data": {"qq": str(item["reporter_id"])}}
                    )
                    reply_text = " " + reply_text
                chain.append({"type": "text", "data": {"text": reply_text}})
                await client.api.call_action(
                    "send_group_msg", group_id=group_id_int, message=chain
                )
            else:
                reporter_int = FeedbackTodoService._to_int(item.get("reporter_id"))
                if not reporter_int:
                    raise ValueError("报告人QQ无效")
                chain = [
                    {"type": "reply", "data": {"id": str(item.get("message_id"))}},
                    {"type": "text", "data": {"text": reply_text}},
                ]
                await client.api.call_action(
                    "send_private_msg", user_id=reporter_int, message=chain
                )
        except Exception as e:
            logger.error(f"反馈待办：回复报告人失败: {e}")
            return False, (
                f"⚠️ 待办 #{todo_id} 回复发送失败：{e}\n"
                f"待办保持未完成状态，可稍后重试：/完成待办 {todo_id} <处理结果>"
            )

        # 回复成功后才标记完成
        item["status"] = "done"
        item["closed_time"] = int(time.time())
        item["result"] = FeedbackTodoService._truncate(result_text, 500)
        auto_deleted = False
        if plugin.config.get("todo_auto_delete_done", False):
            store["items"] = [
                i for i in store["items"] if i.get("id") != todo_id
            ]
            auto_deleted = True
        await FeedbackTodoService._save_store(plugin, store)

        if item.get("message_type") == "group":
            target = (
                f"群 {item.get('group_name') or '未知群'}({item.get('group_id')}) "
                f"中 {item.get('reporter_name', '?')}({item.get('reporter_id', '?')})"
            )
        else:
            target = f"{item.get('reporter_name', '?')}({item.get('reporter_id', '?')}) 的私聊"
        msg = f"✅ 待办 #{todo_id} 已完成，处理结果已引用回复给 {target}"
        if auto_deleted:
            msg += "（已按配置自动删除该待办）"
        return True, msg

    @staticmethod
    async def delete_todos(plugin, todo_ids):
        """批量删除待办

        Returns:
            tuple: (成功删除的编号列表, 不存在的编号列表)
        """
        id_set = {int(t) for t in todo_ids}
        store = await FeedbackTodoService._load_store(plugin)
        existing = {i.get("id") for i in store["items"]}
        to_delete = sorted(id_set & existing)
        missing = sorted(id_set - existing)
        if to_delete:
            delete_set = set(to_delete)
            store["items"] = [
                i for i in store["items"] if i.get("id") not in delete_set
            ]
            await FeedbackTodoService._save_store(plugin, store)
        return to_delete, missing

    @staticmethod
    async def update_todo_fields(plugin, todo_id, fields):
        """编辑待办字段（目前支持 AI 摘要与补充原因）

        Returns:
            tuple: (是否成功, 提示消息)
        """
        store = await FeedbackTodoService._load_store(plugin)
        item = next((i for i in store["items"] if i.get("id") == todo_id), None)
        if not item:
            return False, f"未找到待办 #{todo_id}"

        changed = []
        if "llm_summary" in fields:
            item["llm_summary"] = FeedbackTodoService._truncate(
                str(fields["llm_summary"] or ""), 2000
            )
            changed.append("摘要")
        if "reason" in fields:
            item["reason"] = FeedbackTodoService._truncate(
                str(fields["reason"] or ""), 500
            )
            changed.append("补充原因")
        if not changed:
            return False, "没有可更新的字段"
        await FeedbackTodoService._save_store(plugin, store)
        return True, f"待办 #{todo_id} 已更新：{'、'.join(changed)}"

    @staticmethod
    async def resummarize_todo(plugin, todo_id):
        """基于已存储的上下文重新调用 LLM 总结

        Returns:
            tuple: (是否成功, 新摘要或错误信息)
        """
        store = await FeedbackTodoService._load_store(plugin)
        item = next((i for i in store["items"] if i.get("id") == todo_id), None)
        if not item:
            return False, f"未找到待办 #{todo_id}"
        if not plugin.config.get("todo_use_llm", True):
            return False, "插件配置已关闭 AI 摘要（todo_use_llm）"

        provider_id = await FeedbackTodoService._resolve_llm_provider_id(plugin)
        if not provider_id:
            return False, "未配置可用的聊天模型"

        target_desc = (
            f"{item.get('reporter_name', '?')}({item.get('reporter_id', '?')}）："
            f"{item.get('target_text') or '[非文本消息]'}"
        )
        transcript = item.get("context_text") or target_desc
        system_prompt, prompt = FeedbackTodoService._build_summary_prompts(
            transcript, target_desc, item.get("reason", "")
        )
        summary = await FeedbackTodoService._generate_summary(
            plugin,
            provider_id,
            system_prompt,
            prompt,
            item.get("context_images"),
        )
        if not summary:
            return False, "LLM 总结失败，请检查模型配置或日志"
        item["llm_summary"] = summary
        await FeedbackTodoService._save_store(plugin, store)
        return True, summary

    # ==================== 完成与删除指令 ====================

    @staticmethod
    async def handle_complete_todo(plugin, event: AstrMessageEvent):
        """处理 /完成待办 <编号> <处理结果> 指令

        完成后会以引用回复的方式把处理结果回复给报告人的原始消息。
        """
        parts = event.message_str.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            yield event.make_result().message(
                "参数不足，请使用：/完成待办 <编号> <处理结果>"
            )
            return
        todo_id = int(parts[1])
        result_text = parts[2].strip()

        client = await QQOperaterService.get_client(plugin, event)
        ok, msg = await FeedbackTodoService.complete_todo(
            plugin, todo_id, result_text, client
        )
        if ok and not plugin.config.get("todo_reply_feedback", True):
            logger.info(f"反馈待办：{msg}")
            return
        yield event.make_result().message(msg)

    @staticmethod
    async def handle_delete_todo(plugin, event: AstrMessageEvent):
        """处理 /删除待办 指令，支持批量

        使用示例：
        /删除待办 3
        /删除待办 3 5 7
        /删除待办 3-7
        /删除待办 3,5,7
        /删除待办 已完成
        /删除待办 全部
        """
        args = event.message_str.split()[1:]
        if not args:
            yield event.make_result().message(
                "参数不足，请使用：/删除待办 <编号> [编号2 编号3 ...]，"
                "支持范围（3-7）、逗号分隔、已完成、全部"
            )
            return

        store = await FeedbackTodoService._load_store(plugin)
        if args == ["全部"]:
            ids = [i.get("id") for i in store["items"]]
            scope_desc = "全部待办"
        elif args == ["已完成"]:
            ids = [i.get("id") for i in store["items"] if i.get("status") == "done"]
            scope_desc = "所有已完成的待办"
        else:
            ids = []
            for token in " ".join(args).replace("，", ",").replace(",", " ").split():
                if "-" in token:
                    left, _, right = token.partition("-")
                    if left.isdigit() and right.isdigit() and int(left) <= int(right):
                        ids.extend(range(int(left), int(right) + 1))
                        continue
                    yield event.make_result().message(f"参数错误：{token}（范围格式应为 3-7）")
                    return
                if not token.isdigit():
                    yield event.make_result().message(
                        f"参数错误：{token}，编号必须是数字（也可用 已完成 / 全部）"
                    )
                    return
                ids.append(int(token))
            scope_desc = f"待办 {', '.join(f'#{i}' for i in ids)}"

        if not ids:
            yield event.make_result().message("没有符合条件的待办可删除")
            return

        deleted, missing = await FeedbackTodoService.delete_todos(plugin, ids)
        msg = f"🗑 已删除 {len(deleted)} 条待办（{scope_desc}）。编号不会复用，新待办继续顺延编号。"
        if missing:
            msg += f"\n以下编号不存在：{', '.join(f'#{i}' for i in missing)}"
        yield event.make_result().message(msg)
