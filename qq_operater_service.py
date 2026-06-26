"""
QQ操作服务模块

包含QQ操作插件的核心逻辑处理，包括：
- 工具方法（时间格式化、性别格式化）
- 客户端获取方法
- 命令处理逻辑（获取群列表、获取群成员信息）
- 模仿功能（模仿指定用户的头像和群名片）
"""

import asyncio
import aiohttp
import hashlib
import re
from datetime import datetime
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)


class QQOperaterService:
    """QQ操作服务类，处理插件的核心逻辑"""

    @staticmethod
    def format_timestamp(timestamp):
        """格式化时间戳为可读日期时间字符串

        Args:
            timestamp: 时间戳（秒级）

        Returns:
            str: 格式化后的日期时间字符串，如"2023-01-01 12:00:00"，失败则返回"未知"
        """
        if not timestamp or not isinstance(timestamp, (int, float)):
            return "未知"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return "未知"

    @staticmethod
    def format_gender(gender):
        """格式化性别显示，返回带图标的性别字符串

        Args:
            gender: 性别字符串

        Returns:
            str: 带图标的性别字符串，如"👨 男"、"👩 女"、"❓ 未知"
        """
        if not gender or gender == "unknown":
            return "❓ 未知"
        elif gender == "male":
            return "👨 男"
        elif gender == "female":
            return "👩 女"
        return f"❓ {gender}"

    @staticmethod
    async def get_client(plugin, event: AstrMessageEvent = None):
        """获取QQ客户端实例

        Args:
            plugin: 插件实例
            event: 消息事件对象

        Returns:
            客户端实例或None
        """
        # 如果有事件且不是MockEvent，优先从事件获取（适用于事件响应中）
        if event:
            try:
                if event.get_platform_name() == "aiocqhttp":
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                        AiocqhttpMessageEvent,
                    )

                    # 只有真正的AiocqhttpMessageEvent才使用event.bot
                    if isinstance(event, AiocqhttpMessageEvent):
                        return event.bot
            except AttributeError:
                # 如果是MockEvent或缺少方法，跳过从event获取
                pass

        # 否则动态获取平台实例并返回client
        for platform in plugin.context.platform_manager.platform_insts:
            platform_name = platform.meta().name
            if platform_name in ["aiocqhttp", "qq_official"]:
                # 如果是aiocqhttp平台，直接获取client，不进行类型检查
                if platform_name == "aiocqhttp":
                    return platform.get_client()
        return None

    @staticmethod
    async def handle_get_group_list(plugin, event: AstrMessageEvent):
        """处理获取群列表命令的逻辑

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        if client := await QQOperaterService.get_client(plugin, event):
            # 调用get_group_list API，默认no_cache为false
            ret = await client.api.call_action("get_group_list", no_cache=False)
            # 格式化输出结果
            if isinstance(ret, list):
                # 直接返回群列表数组的情况
                groups = ret
                result = f"共获取到 {len(groups)} 个群：\n"
                for group in groups:
                    result += f"群号：{group.get('group_id')}，群名：{group.get('group_name')}\n"
                yield event.make_result().message(result)
            elif isinstance(ret, dict) and "data" in ret:
                # 兼容返回字典且包含data字段的情况
                groups = ret["data"]
                result = f"共获取到 {len(groups)} 个群：\n"
                for group in groups:
                    result += f"群号：{group.get('group_id')}，群名：{group.get('group_name')}\n"
                yield event.make_result().message(result)
            else:
                yield event.make_result().message(f"获取群列表结果：{ret}")
        else:
            yield event.make_result().message("当前平台不支持此命令")

    @staticmethod
    async def handle_get_group_member_info(plugin, event: AstrMessageEvent):
        """处理获取群成员信息命令的逻辑

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        if client := await QQOperaterService.get_client(plugin, event):
            # 解析命令参数，获取group_id和user_id
            cmd_params = event.message_str.split()
            if len(cmd_params) < 3:
                yield event.make_result().message(
                    "参数不足，请使用：/获取群成员信息 <群号> <用户ID>"
                )
                return

            try:
                group_id = int(cmd_params[1])
                user_id = int(cmd_params[2])
                # 可选参数no_cache，默认为false
                no_cache = False
                if len(cmd_params) > 3 and cmd_params[3].lower() in [
                    "true",
                    "1",
                    "yes",
                ]:
                    no_cache = True

                # 调用get_group_member_info API
                ret = await client.api.call_action(
                    "get_group_member_info",
                    group_id=group_id,
                    user_id=user_id,
                    no_cache=no_cache,
                )

                # 格式化输出结果
                if isinstance(ret, dict):
                    if ret.get("status") == "ok" and "data" in ret:
                        # 处理包含status和data字段的格式
                        member_info = ret["data"]
                    else:
                        # 检查是否直接返回成员信息字典（兼容不同API返回格式）
                        member_info = ret

                    # 验证是否是有效的成员信息（包含group_id和user_id）
                    if "group_id" in member_info and "user_id" in member_info:
                        result = "群成员信息：\n"
                        result += f"🏢 群号：{member_info.get('group_id')}\n"
                        result += f"🆔 用户ID：{member_info.get('user_id')}\n"
                        result += f"📛 昵称：{member_info.get('nickname')}\n"
                        result += f"💳 群名片：{member_info.get('card') or '无'}\n"
                        result += f"👤 性别：{QQOperaterService.format_gender(member_info.get('sex'))}\n"
                        result += f"📅 年龄：{member_info.get('age') or '未知'}\n"
                        result += f"📍 地区：{member_info.get('area') or '未知'}\n"
                        result += f"📌 加入时间：{QQOperaterService.format_timestamp(member_info.get('join_time'))}\n"
                        result += f"💬 最后发言时间：{QQOperaterService.format_timestamp(member_info.get('last_sent_time'))}\n"
                        result += f"👑 身份：{'群主' if member_info.get('role') == 'owner' else '管理员' if member_info.get('role') == 'admin' else '成员'}\n"
                        result += f"🏅 专属头衔：{member_info.get('title') or '无'}\n"
                        yield event.make_result().message(result)
                    else:
                        # 如果不是有效的成员信息，返回失败信息
                        yield event.make_result().message(
                            f"获取群成员信息失败：{ret.get('message', '未知错误')}"
                        )
                else:
                    yield event.make_result().message(f"获取群成员信息结果：{ret}")
            except ValueError:
                yield event.make_result().message(
                    "参数错误，请输入正确的数字类型群号和用户ID"
                )
        else:
            yield event.make_result().message("当前平台不支持此命令")

    @staticmethod
    async def _stop_current_imitate(plugin):
        """停止当前模仿任务

        Args:
            plugin: 插件实例
        """
        if plugin.imitate_task:
            plugin.imitate_task.cancel()
            plugin.imitate_task = None
            plugin.imitate_target = None

        plugin.imitate_cache = None
        plugin.config["imitate"] = ""

    @staticmethod
    async def _start_imitate_target(plugin, event, group_id, user_id):
        """开始模仿新目标

        Args:
            plugin: 插件实例
            event: 消息事件对象
            group_id: 群号
            user_id: 用户ID
        """
        # 保存目标信息到配置，格式：群号,QQ号
        plugin.config["imitate"] = f"{group_id},{user_id}"

        # 存储目标信息
        plugin.imitate_target = {"group_id": group_id, "user_id": user_id}

        # 创建模仿任务
        plugin.imitate_task = asyncio.create_task(
            QQOperaterService._imitate_monitor(plugin, event)
        )

    @staticmethod
    async def _replace_imitate_target(plugin, event, group_id, user_id):
        """替换模仿目标

        Args:
            plugin: 插件实例
            event: 消息事件对象
            group_id: 群号
            user_id: 用户ID
        """
        # 停止当前模仿任务
        await QQOperaterService._stop_current_imitate(plugin)

        # 开始模仿新目标
        await QQOperaterService._start_imitate_target(plugin, event, group_id, user_id)

        # 发送替换成功消息
        await event.send(
            event.make_result().message(
                f"已成功替换模仿目标，开始模仿用户 {user_id}，每 {plugin.config.get('imitate_interval', 10)} 分钟更新一次"
            )
        )

    @staticmethod
    async def handle_imitate_user(plugin, event: AstrMessageEvent):
        """处理模仿用户命令的逻辑

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        # 检查配置中是否指定了模仿目标或已在模仿其他用户
        has_existing_target = False
        existing_target_info = ""

        config_imitate_target = plugin.config.get("imitate", "")
        if config_imitate_target:
            has_existing_target = True
            existing_target_info = config_imitate_target
        elif plugin.imitate_task and plugin.imitate_target:
            has_existing_target = True
            existing_target_info = f"{plugin.imitate_target['group_id']},{plugin.imitate_target['user_id']}"

        # 解析新的目标用户ID
        # 从消息中提取@mention的用户ID
        new_target_user_id = None

        # 尝试从消息链中提取@mention
        from astrbot.core.message.components import At

        for component in event.get_messages():
            if isinstance(component, At) and component.qq != "all":
                new_target_user_id = component.qq
                break

        # 如果没有@mention，尝试从命令参数中提取
        if not new_target_user_id:
            cmd_params = event.message_str.split()
            if len(cmd_params) >= 2:
                # 尝试解析参数为用户ID
                try:
                    new_target_user_id = int(cmd_params[1])
                except ValueError:
                    pass

        if not new_target_user_id:
            yield event.make_result().message("请@需要模仿的用户，或在命令后跟上用户ID")
            return

        # 获取群ID
        group_id = event.get_group_id()
        if not group_id:
            yield event.make_result().message("请在群聊中使用此命令")
            return

        # 如果已有模仿目标，询问用户是否替换
        if has_existing_target:
            try:
                yield event.make_result().message(
                    f"当前已存在模仿目标用户 {existing_target_info}，是否替换为新目标？(是/否)"
                )

                # 定义会话处理函数
                @session_waiter(timeout=60, record_history_chains=False)
                async def imitate_confirm_waiter(
                    controller: SessionController, confirm_event: AstrMessageEvent
                ):
                    # 检查用户回复
                    user_reply = confirm_event.message_str.strip()

                    if user_reply in ["是", "是的", "Y", "y", "YES", "yes"]:
                        # 用户确认替换，执行替换逻辑
                        await QQOperaterService._replace_imitate_target(
                            plugin, confirm_event, group_id, new_target_user_id
                        )
                        controller.stop()
                    elif user_reply in ["否", "不是", "N", "n", "NO", "no"]:
                        # 用户取消替换
                        await confirm_event.send(
                            confirm_event.make_result().message("已取消替换模仿目标")
                        )
                        controller.stop()
                    else:
                        # 用户回复无效，提示重新输入
                        await confirm_event.send(
                            confirm_event.make_result().message("请回复'是'或'否'")
                        )
                        controller.keep(timeout=60, reset_timeout=True)

                try:
                    await imitate_confirm_waiter(event)
                except TimeoutError:
                    yield event.make_result().message("会话超时，已取消替换")
                except Exception as e:
                    yield event.make_result().message(f"会话处理错误：{str(e)}")
            except Exception as e:
                yield event.make_result().message(f"处理模仿命令失败：{str(e)}")
        else:
            # 没有现有模仿目标，直接开始模仿
            await QQOperaterService._start_imitate_target(
                plugin, event, group_id, new_target_user_id
            )
            yield event.make_result().message(
                f"开始模仿用户 {new_target_user_id}，每 {plugin.config.get('imitate_interval', 10)} 分钟更新一次"
            )

    @staticmethod
    async def handle_stop_imitate(plugin, event: AstrMessageEvent):
        """处理停止模仿命令的逻辑

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        # 调用辅助函数停止当前模仿任务
        await QQOperaterService._stop_current_imitate(plugin)

        yield event.make_result().message("已停止模仿，并清空了配置中的模仿目标")

    @staticmethod
    async def handle_update_avatar(plugin, event: AstrMessageEvent):
        """处理更新头像命令的逻辑

        使用示例：
        /更新头像
        （然后发送头像图片）

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """

        # 定义会话处理函数
        async def update_avatar_waiter(
            controller: SessionController, confirm_event: AstrMessageEvent
        ):
            # 添加日志，记录收到的消息
            logger.info(f"收到更新头像的回复消息: {confirm_event.message_str}")
            logger.info(f"消息组件数量: {len(confirm_event.get_messages())}")

            # 检查用户回复是否包含图片
            from astrbot.core.message.components import Image

            image_component = None
            for component in confirm_event.get_messages():
                logger.info(f"消息组件类型: {type(component).__name__}")
                if isinstance(component, Image):
                    image_component = component
                    logger.info(f"找到图片组件: {component.__dict__}")
                    break

            if image_component:
                # 获取客户端
                client = await QQOperaterService.get_client(plugin, confirm_event)
                if not client:
                    await confirm_event.send(
                        confirm_event.make_result().message("当前平台不支持此命令")
                    )
                    controller.stop()
                    return

                try:
                    # 获取图片属性
                    image_file = getattr(image_component, "file", None)
                    image_url = getattr(image_component, "url", None)
                    logger.info(f"图片file属性: {image_file}")
                    logger.info(f"图片url属性: {image_url}")

                    # 清理URL，移除所有反引号和空格
                    if isinstance(image_url, str):
                        # 移除所有反引号
                        image_url = image_url.replace("`", "")
                        # 移除前后空格
                        image_url = image_url.strip()
                        logger.info(f"清理后的图片url属性: {image_url}")

                    # 调用set_qq_avatar API，尝试不同的图片属性
                    success = False
                    error_msg = ""

                    # 尝试1: 使用清理后的url属性（优先）
                    try:
                        if image_url:
                            logger.info(f"尝试使用url属性更新头像: {image_url}")
                            result = await client.api.call_action(
                                "set_qq_avatar", file=image_url
                            )
                            await confirm_event.send(
                                confirm_event.make_result().message(
                                    f"更新头像成功，API返回: {result}"
                                )
                            )
                            success = True
                            controller.stop()  # 成功后停止会话
                            return
                    except Exception as e:
                        error_msg = f"url方式失败: {str(e)}"
                        logger.error(error_msg)

                    # 尝试2: 使用file属性
                    if not success and image_file:
                        try:
                            logger.info(f"尝试使用file属性更新头像: {image_file}")
                            result = await client.api.call_action(
                                "set_qq_avatar", file=image_file
                            )
                            await confirm_event.send(
                                confirm_event.make_result().message(
                                    f"更新头像成功，API返回: {result}"
                                )
                            )
                            success = True
                            controller.stop()  # 成功后停止会话
                            return
                        except Exception as e:
                            error_msg += f", file方式失败: {str(e)}"
                            logger.error(f"file方式失败: {str(e)}")

                    # 尝试3: 从原始消息提取CQ码
                    if not success:
                        try:
                            # 从原始消息中提取图片URL
                            raw_message = getattr(confirm_event, "raw_message", None)
                            if raw_message:
                                logger.info(f"尝试从原始消息提取CQ码: {raw_message}")
                                # 匹配CQ码中的图片URL
                                cq_code_match = re.search(
                                    r"\[CQ:image,.*?url=(https?://.*?)\]",
                                    str(raw_message),
                                )
                                if cq_code_match:
                                    image_url = cq_code_match.group(1)
                                    logger.info(f"提取到CQ码中的URL: {image_url}")
                                    result = await client.api.call_action(
                                        "set_qq_avatar", file=image_url
                                    )
                                    await confirm_event.send(
                                        confirm_event.make_result().message(
                                            f"更新头像成功，API返回: {result}"
                                        )
                                    )
                                    success = True
                                    controller.stop()  # 成功后停止会话
                                    return
                        except Exception as e:
                            error_msg += f", CQ码提取失败: {str(e)}"
                            logger.error(f"CQ码提取失败: {str(e)}")

                    # 如果所有尝试都失败
                    if not success:
                        await confirm_event.send(
                            confirm_event.make_result().message(
                                f"更新头像失败，请尝试使用URL方式：/更新头像URL <头像URL>"
                            )
                        )
                        controller.stop()  # 失败后也停止会话，避免无限等待
                        return
                except Exception as e:
                    # 捕获所有异常，确保给用户回复
                    logger.error(f"更新头像过程中出现异常: {str(e)}")
                    await confirm_event.send(
                        confirm_event.make_result().message(
                            f"更新头像失败，错误：{str(e)}"
                        )
                    )
                    controller.stop()  # 异常后停止会话
                    return
            else:
                # 用户没有发送图片，提示重新发送
                await confirm_event.send(
                    confirm_event.make_result().message("未检测到图片，请发送头像图片")
                )
                controller.keep(timeout=60, reset_timeout=True)

        # 发送初始提示消息
        yield event.make_result().message("请发送头像图片")

        # 注册会话控制器
        try:
            # 具体的会话控制器使用方法
            @session_waiter(
                timeout=60, record_history_chains=False
            )  # 注册一个会话控制器，设置超时时间为 60 秒，不记录历史消息链
            async def waiter(
                controller: SessionController, confirm_event: AstrMessageEvent
            ):
                await update_avatar_waiter(controller, confirm_event)

            await waiter(event)
        except TimeoutError:
            yield event.make_result().message("会话超时，已取消更新头像")
        except Exception as e:
            logger.error(f"会话处理错误：{str(e)}")
            yield event.make_result().message(f"会话处理错误：{str(e)}")

    @staticmethod
    async def handle_update_avatar_url(plugin, event: AstrMessageEvent):
        """处理通过URL更新头像命令的逻辑

        使用示例：
        /更新头像URL http://example.com/avatar.jpg

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        # 解析命令参数，获取头像URL
        cmd_params = event.message_str.split()
        if len(cmd_params) < 2:
            yield event.make_result().message(
                "参数不足，请使用：/更新头像URL <头像URL>"
            )
            return

        # 提取并清理URL，移除可能的反引号和空格
        avatar_url = cmd_params[1]
        if len(cmd_params) > 2:
            # 如果URL中包含空格，重新拼接
            avatar_url = " ".join(cmd_params[1:])

        # 清理URL
        avatar_url = avatar_url.strip().strip("`")
        logger.info(f"收到更新头像URL命令，清理后的URL: {avatar_url}")

        # 验证URL格式
        if not (avatar_url.startswith("http://") or avatar_url.startswith("https://")):
            yield event.make_result().message(
                "URL格式不正确，请输入完整的HTTP或HTTPS URL"
            )
            return

        # 获取客户端
        client = await QQOperaterService.get_client(plugin, event)
        if client:
            try:
                # 调用set_qq_avatar API
                result = await client.api.call_action("set_qq_avatar", file=avatar_url)
                yield event.make_result().message(f"更新头像成功，API返回: {result}")
            except Exception as e:
                logger.error(f"更新头像失败: {str(e)}")
                yield event.make_result().message(f"更新头像失败: {str(e)}")
        else:
            yield event.make_result().message("当前平台不支持此命令")

    @staticmethod
    async def handle_update_nickname(plugin, event: AstrMessageEvent):
        """处理更新昵称命令的逻辑

        使用示例：
        /更新昵称 新昵称

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        # 解析命令参数，获取新昵称
        cmd_params = event.message_str.split(maxsplit=1)
        if len(cmd_params) < 2:
            yield event.make_result().message("参数不足，请使用：/更新昵称 <新昵称>")
            return

        new_nickname = cmd_params[1].strip()
        logger.info(f"收到更新昵称命令，新昵称: {new_nickname}")

        # 验证昵称长度
        if not new_nickname:
            yield event.make_result().message("昵称不能为空")
            return

        # 获取客户端
        client = await QQOperaterService.get_client(plugin, event)
        if client:
            try:
                # 调用set_qq_profile API更新昵称
                result = await client.api.call_action(
                    "set_qq_profile", nickname=new_nickname
                )
                yield event.make_result().message(f"更新昵称成功，API返回: {result}")
            except Exception as e:
                logger.error(f"更新昵称失败: {str(e)}")
                yield event.make_result().message(f"更新昵称失败: {str(e)}")
        else:
            yield event.make_result().message("当前平台不支持此命令")

    @staticmethod
    async def _fetch_target_info(client, group_id, user_id):
        """获取目标用户信息

        Args:
            client: QQ客户端实例
            group_id: 群号
            user_id: 用户ID

        Returns:
            tuple: (target_nickname, target_card_name, avatar_url) 或 (None, None, None) if failed
        """
        try:
            member_info = await client.api.call_action(
                "get_group_member_info",
                group_id=group_id,
                user_id=user_id,
                no_cache=True,
            )

            # 处理API返回格式
            if isinstance(member_info, dict):
                if member_info.get("status") == "ok" and "data" in member_info:
                    member_info = member_info["data"]

            # 获取目标用户的详细信息
            target_nickname = member_info.get("nickname", "未知")
            target_card_name = member_info.get("card") or target_nickname

            # 生成目标用户头像URL
            avatar_url = f"https://thirdqq.qlogo.cn/g?b=sdk&s=640&nk={user_id}"

            return target_nickname, target_card_name, avatar_url
        except Exception as e:
            logger.error(f"获取目标用户信息失败: {e}")
            return None, None, None

    @staticmethod
    async def _download_avatar(session, avatar_url):
        """下载头像并计算哈希值

        Args:
            session: aiohttp.ClientSession实例
            avatar_url: 头像URL

        Returns:
            str: 头像哈希值，下载失败返回None
        """
        try:
            # 使用传入的session下载头像图片
            async with session.get(avatar_url) as resp:
                if resp.status == 200:
                    # 读取图片内容
                    image_data = await resp.read()
                    # 计算图片哈希值
                    current_avatar_hash = hashlib.md5(image_data).hexdigest()
                    logger.info(
                        f"模仿监控：获取到目标用户头像，MD5哈希值: {current_avatar_hash}"
                    )
                    return current_avatar_hash
                else:
                    logger.error(f"模仿监控：下载头像失败，HTTP状态码: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"模仿监控：下载头像或计算哈希值失败: {e}")
            return None

    @staticmethod
    def _check_need_update(
        plugin, target_nickname, target_card_name, current_avatar_hash
    ):
        """检查是否需要更新机器人信息

        Args:
            plugin: 插件实例
            target_nickname: 目标用户昵称
            target_card_name: 目标用户群名片
            current_avatar_hash: 当前头像哈希值

        Returns:
            bool: 是否需要更新
        """
        # 检查缓存，判断是否需要更新
        if plugin.imitate_cache and current_avatar_hash:
            # 检查昵称、群名片和头像哈希值
            if (
                plugin.imitate_cache["nickname"] == target_nickname
                and plugin.imitate_cache["card"] == target_card_name
                and plugin.imitate_cache["avatar_hash"] == current_avatar_hash
            ):
                return False
        return True

    @staticmethod
    async def _update_bot_avatar(client, avatar_url):
        """更新机器人头像

        Args:
            client: QQ客户端实例
            avatar_url: 头像URL
        """
        logger.info(f"模仿监控：开始更新机器人头像")
        try:
            avatar_result = await client.api.call_action(
                "set_qq_avatar", file=avatar_url
            )
            logger.info(f"模仿监控：更新头像成功，API返回: {avatar_result}")
        except Exception as e:
            logger.error(f"模仿监控：更新头像失败: {e}")

    @staticmethod
    async def _get_bot_id(client, event):
        """获取机器人ID

        Args:
            client: QQ客户端实例
            event: 消息事件对象

        Returns:
            str/int: 机器人ID，获取失败返回None
        """
        # 尝试从事件获取
        bot_id = getattr(event, "get_author_id", lambda: None)()
        if bot_id:
            logger.info(f"模仿监控：从事件获取到机器人ID: {bot_id}")
            return bot_id

        # 如果无法从event获取，尝试从客户端获取
        logger.warning(f"模仿监控：无法从事件获取机器人ID，尝试从客户端获取")
        try:
            login_info = await client.api.call_action("get_login_info")
            if isinstance(login_info, dict):
                if login_info.get("status") == "ok" and "data" in login_info:
                    login_info = login_info["data"]
                bot_id = login_info.get("user_id") or login_info.get("uin")
                if bot_id:
                    logger.info(f"模仿监控：从客户端获取到机器人ID: {bot_id}")
                    return bot_id
        except Exception as e:
            logger.error(f"模仿监控：获取机器人ID失败: {e}")

        logger.error(f"模仿监控：无法获取机器人ID")
        return None

    @staticmethod
    async def _update_bot_card(client, group_id, bot_id, target_card_name):
        """更新机器人群名片

        Args:
            client: QQ客户端实例
            group_id: 群号
            bot_id: 机器人ID
            target_card_name: 目标群名片
        """
        logger.info(f"模仿监控：开始更新机器人群名片为: {target_card_name}")
        try:
            card_result = await client.api.call_action(
                "set_group_card",
                group_id=group_id,
                user_id=bot_id,
                card=target_card_name,
            )
            logger.info(f"模仿监控：更新群名片成功，API返回: {card_result}")
        except Exception as e:
            logger.error(f"模仿监控：更新群名片失败: {e}")

    @staticmethod
    async def _imitate_monitor(plugin, event: AstrMessageEvent):
        """模仿监控任务，周期性检测目标用户信息并更新

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        try:
            client = await QQOperaterService.get_client(plugin, event)
            if not client:
                return

            # 在循环外创建共享的ClientSession，避免每次循环都创建新session
            async with aiohttp.ClientSession() as session:
                while True:
                    # 检查目标信息是否存在
                    if not plugin.imitate_target:
                        break

                    group_id = plugin.imitate_target["group_id"]
                    user_id = plugin.imitate_target["user_id"]

                    logger.info(
                        f"模仿监控：开始处理目标用户 - 群: {group_id}, 用户ID: {user_id}"
                    )

                    # 获取目标用户信息
                    (
                        target_nickname,
                        target_card_name,
                        avatar_url,
                    ) = await QQOperaterService._fetch_target_info(
                        client, group_id, user_id
                    )

                    if not target_card_name:
                        logger.warning(
                            f"模仿监控：目标用户 {user_id} 没有昵称或群名片，跳过此次更新"
                        )
                        await asyncio.sleep(
                            plugin.config.get("imitate_interval", 10) * 60
                        )
                        continue

                    logger.info(
                        f"模仿监控：获取到目标用户信息 - 昵称: {target_nickname}, 群名片: {target_card_name}"
                    )
                    logger.info(f"模仿监控：生成目标用户头像URL: {avatar_url}")

                    # 下载头像并计算哈希值，传递共享的session
                    current_avatar_hash = await QQOperaterService._download_avatar(
                        session, avatar_url
                    )

                    # 如果头像下载失败，跳过本次循环，避免后续逻辑混乱
                    if current_avatar_hash is None:
                        logger.warning(
                            f"模仿监控：目标用户 {user_id} 头像下载失败，跳过此次更新"
                        )
                        await asyncio.sleep(
                            plugin.config.get("imitate_interval", 10) * 60
                        )
                        continue

                    # 检查是否需要更新
                    need_update = QQOperaterService._check_need_update(
                        plugin, target_nickname, target_card_name, current_avatar_hash
                    )

                    if not need_update:
                        logger.info(
                            f"模仿监控：目标用户 {user_id} 昵称、群名片和头像均无变化，跳过更新"
                        )
                        await asyncio.sleep(
                            plugin.config.get("imitate_interval", 10) * 60
                        )
                        continue

                    logger.info(f"模仿监控：目标用户 {user_id} 信息有变化，开始更新")

                    # 更新机器人头像
                    await QQOperaterService._update_bot_avatar(client, avatar_url)

                    # 获取机器人ID并更新群名片
                    bot_id = await QQOperaterService._get_bot_id(client, event)
                    if bot_id:
                        await QQOperaterService._update_bot_card(
                            client, group_id, bot_id, target_card_name
                        )

                    # 更新缓存，记录此次模仿的信息
                    plugin.imitate_cache = {
                        "avatar_url": avatar_url,
                        "avatar_hash": current_avatar_hash,
                        "nickname": target_nickname,
                        "card": target_card_name,
                    }
                    logger.info(f"模仿监控：更新缓存成功，下次将对比当前信息")

                    # 等待指定时间间隔
                    await asyncio.sleep(plugin.config.get("imitate_interval", 10) * 60)

        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass
        except Exception as e:
            logger.error(f"模仿监控任务异常: {e}")
            # 清理任务状态
            plugin.imitate_task = None
            plugin.imitate_target = None

    @staticmethod
    async def handle_broadcast_message(plugin, event: AstrMessageEvent):
        """处理群发消息命令的逻辑

        使用示例：
        /群发消息 你好，这是测试消息
        /群发消息 你好 123456 789012
        /群发消息 图文消息（含图片） 123456

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        from astrbot.core.message.components import Image, Plain

        # 获取客户端
        client = await QQOperaterService.get_client(plugin, event)
        if not client:
            yield event.make_result().message("当前平台不支持此命令")
            return

        # 遍历消息组件链，提取命令后的消息内容（保留图片等富媒体组件）
        # 并解析末尾的群号参数
        message_chain = []
        target_groups = []
        first_text = True  # 标记是否还在处理命令前缀所在的第一个文本组件

        for component in event.get_messages():
            if isinstance(component, Plain):
                text = component.text
                if first_text:
                    first_text = False
                    # 去掉命令前缀"群发消息"
                    if text.startswith("群发消息"):
                        text = text[len("群发消息"):]
                    text = text.strip()
                    if not text:
                        continue

                # 检查最后一个文本组件是否包含群号（纯数字或末尾为数字）
                parts = text.split()
                if len(parts) > 1 and parts[-1].isdigit():
                    # 末尾数字作为群号，其余作为消息内容
                    target_groups.append(int(parts[-1]))
                    text = " ".join(parts[:-1])
                    if text:
                        message_chain.append({"type": "text", "data": {"text": text}})
                elif len(parts) == 1 and parts[0].isdigit() and message_chain:
                    # 纯数字且前面已有消息内容，视为群号
                    target_groups.append(int(parts[0]))
                else:
                    message_chain.append({"type": "text", "data": {"text": text}})

            elif isinstance(component, Image):
                # 保留图片组件，使用url属性（aiocqhttp send_group_msg 支持url格式）
                image_url = component.url or component.file
                if image_url:
                    message_chain.append({"type": "image", "data": {"file": image_url}})

        if not message_chain:
            yield event.make_result().message(
                "参数不足，请使用：/群发消息 <消息内容> [群号1] [群号2] ..."
            )
            return

        try:
            if target_groups:
                # 发送到指定群
                success_count = 0
                fail_count = 0
                fail_groups = []

                for group_id in target_groups:
                    try:
                        await client.api.call_action(
                            "send_group_msg", group_id=group_id, message=message_chain
                        )
                        success_count += 1
                    except Exception as e:
                        fail_count += 1
                        fail_groups.append(str(group_id))
                        logger.error(f"群发消息到群 {group_id} 失败: {e}")

                result_msg = f"群发消息完成：成功 {success_count} 个群"
                if fail_count > 0:
                    result_msg += (
                        f"，失败 {fail_count} 个群（群号：{', '.join(fail_groups)}）"
                    )
                yield event.make_result().message(result_msg)
            else:
                # 发送到所有群
                groups = await client.api.call_action("get_group_list", no_cache=False)
                if isinstance(groups, dict) and "data" in groups:
                    groups = groups["data"]

                if not groups:
                    yield event.make_result().message("获取群列表失败或没有群")
                    return

                success_count = 0
                fail_count = 0

                for group in groups:
                    group_id = group.get("group_id")
                    if group_id:
                        try:
                            await client.api.call_action(
                                "send_group_msg",
                                group_id=group_id,
                                message=message_chain,
                            )
                            success_count += 1
                        except Exception as e:
                            fail_count += 1
                            logger.error(f"群发消息到群 {group_id} 失败: {e}")

                yield event.make_result().message(
                    f"群发消息完成：成功 {success_count} 个群，失败 {fail_count} 个群"
                )
        except Exception as e:
            logger.error(f"群发消息异常: {e}")
            yield event.make_result().message(f"群发消息失败: {str(e)}")

    @staticmethod
    async def handle_group_mute(plugin, event: AstrMessageEvent):
        """处理群禁言命令的逻辑

        使用示例：
        /闭嘴 60
        机器人在当前群禁言60秒

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        cmd_params = event.message_str.split()
        if len(cmd_params) < 2:
            yield event.make_result().message("参数不足，请使用：/闭嘴 <秒数>")
            return

        try:
            duration = int(cmd_params[1])
            if duration <= 0:
                yield event.make_result().message("禁言时间必须大于0秒")
                return
        except ValueError:
            yield event.make_result().message("参数错误，请输入正确的秒数")
            return

        group_id = event.get_group_id()
        if not group_id:
            yield event.make_result().message("请在群聊中使用此命令")
            return

        import time
        end_time = time.time() + duration

        # 检查是否已在禁言中
        mute_groups = plugin.config.get("mute_groups", [])
        existing = next((m for m in mute_groups if m["group_id"] == group_id), None)

        if existing:
            existing["end_time"] = end_time
            logger.info(f"更新群禁言：群{group_id}，新时长{duration}秒")
        else:
            mute_groups.append({"group_id": group_id, "end_time": end_time})
            plugin.config["mute_groups"] = mute_groups
            logger.info(f"添加群禁言：群{group_id}，时长{duration}秒")

        yield event.make_result().message(f"已在本群禁言{duration}秒")

    @staticmethod
    async def handle_user_mute(plugin, event: AstrMessageEvent):
        """处理用户禁言命令的逻辑

        使用示例：
        /不回复 @用户 60
        机器人在60秒内不回复该用户

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        cmd_params = event.message_str.split()

        # 从消息中提取@用户
        target_user_id = None
        from astrbot.core.message.components import At

        for component in event.get_messages():
            if isinstance(component, At) and component.qq != "all":
                target_user_id = component.qq
                break

        # 如果没有@，尝试从参数提取
        if not target_user_id:
            yield event.make_result().message("请@需要禁言的用户")
            return

        # 提取时间参数（最后一个参数）
        if len(cmd_params) < 2:
            yield event.make_result().message("参数不足，请使用：/不回复 @用户 <秒数>")
            return

        try:
            duration = int(cmd_params[-1])
            if duration <= 0:
                yield event.make_result().message("禁言时间必须大于0秒")
                return
        except ValueError:
            yield event.make_result().message("参数错误，请输入正确的秒数")
            return

        group_id = event.get_group_id()
        if not group_id:
            yield event.make_result().message("请在群聊中使用此命令")
            return

        import time
        end_time = time.time() + duration

        # 检查是否已在禁言中
        mute_users = plugin.config.get("mute_users", [])
        existing = next(
            (m for m in mute_users if m["group_id"] == group_id and m["user_id"] == target_user_id),
            None,
        )

        if existing:
            existing["end_time"] = end_time
            logger.info(f"更新用户禁言：群{group_id}用户{target_user_id}，新时长{duration}秒")
        else:
            mute_users.append({
                "group_id": group_id,
                "user_id": target_user_id,
                "end_time": end_time,
            })
            plugin.config["mute_users"] = mute_users
            logger.info(f"添加用户禁言：群{group_id}用户{target_user_id}，时长{duration}秒")

        yield event.make_result().message(f"已禁言用户 {target_user_id} {duration}秒")

    @staticmethod
    async def handle_unmute(plugin, event: AstrMessageEvent):
        """处理恢复禁言命令的逻辑

        使用示例：
        /恢复
        恢复当前群的禁言状态

        /恢复 @用户
        恢复指定用户的禁言状态

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        group_id = event.get_group_id()
        if not group_id:
            yield event.make_result().message("请在群聊中使用此命令")
            return

        # 检查是否有@用户
        from astrbot.core.message.components import At

        target_user_id = None
        for component in event.get_messages():
            if isinstance(component, At) and component.qq != "all":
                target_user_id = component.qq
                break

        if target_user_id:
            # 恢复指定用户
            mute_users = plugin.config.get("mute_users", [])
            mute_users = [
                m for m in mute_users
                if not (m["group_id"] == group_id and m["user_id"] == target_user_id)
            ]
            plugin.config["mute_users"] = mute_users
            yield event.make_result().message(f"已恢复用户 {target_user_id}")
        else:
            # 恢复当前群
            mute_groups = plugin.config.get("mute_groups", [])
            mute_groups = [m for m in mute_groups if m["group_id"] != group_id]
            plugin.config["mute_groups"] = mute_groups
            yield event.make_result().message("已恢复本群禁言")

    @staticmethod
    async def handle_mute_list(plugin, event: AstrMessageEvent):
        """处理查看禁言列表命令的逻辑

        Args:
            plugin: 插件实例
            event: 消息事件对象
        """
        import time
        current_time = time.time()

        # 清理过期的群禁言
        mute_groups = plugin.config.get("mute_groups", [])
        active_groups = [m for m in mute_groups if m["end_time"] > current_time]
        if len(active_groups) != len(mute_groups):
            plugin.config["mute_groups"] = active_groups

        # 清理过期的用户禁言
        mute_users = plugin.config.get("mute_users", [])
        active_users = [m for m in mute_users if m["end_time"] > current_time]
        if len(active_users) != len(mute_users):
            plugin.config["mute_users"] = active_users

        result = "当前禁言状态：\n\n"

        if active_groups:
            result += "【群禁言】\n"
            for m in active_groups:
                remaining = int(m["end_time"] - current_time)
                result += f"  群{m['group_id']}：剩余{remaining}秒\n"
        else:
            result += "【群禁言】无\n"

        result += "\n"

        if active_users:
            result += "【用户禁言】\n"
            for m in active_users:
                remaining = int(m["end_time"] - current_time)
                result += f"  群{m['group_id']} 用户{m['user_id']}：剩余{remaining}秒\n"
        else:
            result += "【用户禁言】无\n"

        yield event.make_result().message(result)
