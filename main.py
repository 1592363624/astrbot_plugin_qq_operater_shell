"""
QQ操作插件

【仅QQ】调用QQ接口来实现QQ群操作管理等行为。
"""

import asyncio
import time
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star import Star
from astrbot.api import AstrBotConfig, logger
from astrbot.core.star.context import Context
from astrbot.core.message.components import At
from .qq_operater_service import QQOperaterService


class MockEvent:
    """模拟事件对象，用于初始化时调用服务方法"""

    def get_author_id(self):
        return None

    def get_platform_name(self):
        return "aiocqhttp"


class QQOperaterPlugin(Star):
    """QQ操作插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        """初始化插件"""
        super().__init__(context)
        self.config = config
        # 存储平台实例和客户端的全局变量
        self.qq_platform = None
        self.qq_client = None
        # 模仿功能相关变量
        self.imitate_task = None  # 存储模仿任务
        self.imitate_target = None  # 存储目标用户信息 {group_id, user_id}
        self.imitate_cache = (
            None  # 存储上一次模仿的信息，用于对比 {avatar_url, nickname, card}
        )

    async def initialize(self):
        """插件初始化完成后调用"""
        await super().initialize()
        # 检查配置中是否有模仿目标
        imitate_target = self.config.get("imitate", "")
        if imitate_target:
            # 启动自动模仿任务
            await self.start_auto_imitate()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=100)
    async def on_group_message_mute_filter(self, event: AstrMessageEvent):
        """群消息禁言过滤器，高优先级执行

        在消息到达其他处理逻辑前，检查是否处于禁言状态
        """
        group_id = event.get_group_id()
        if not group_id:
            return

        sender_id = event.get_sender_id()
        current_time = time.time()

        logger.info(f"消息过滤器：群ID={group_id}, sender_id={sender_id}, 类型={type(sender_id)}")

        # 检查群是否被禁言
        mute_groups = self.config.get("mute_groups", [])
        logger.info(f"消息过滤器：mute_groups={mute_groups}")
        for m in mute_groups:
            logger.info(f"消息过滤器：群对比 group_id={m['group_id']}(类型{type(m['group_id'])}) == {group_id}(类型{type(group_id)})?")
            if str(m["group_id"]) == str(group_id) and m["end_time"] > current_time:
                logger.info(f"消息过滤：群{group_id}处于禁言状态，忽略消息")
                event.stop_event()
                return

        # 检查用户是否被禁言
        mute_users = self.config.get("mute_users", [])
        logger.info(f"消息过滤器：mute_users={mute_users}")
        for m in mute_users:
            logger.info(f"消息过滤器：用户对比 group_id={m['group_id']}(类型{type(m['group_id'])}) == {group_id}(类型{type(group_id)}), user_id={m['user_id']}(类型{type(m['user_id'])}) == {sender_id}(类型{type(sender_id)})?")
            if str(m["group_id"]) == str(group_id) and str(m["user_id"]) == str(sender_id) and m["end_time"] > current_time:
                logger.info(f"消息过滤：用户{sender_id}在群{group_id}被禁言，忽略消息")
                event.stop_event()
                return

        # 检查是否启用真实禁言检测
        if self.config.get("enable_real_mute_check", False):
            # 检查缓存，避免频繁调用 API
            cache_key = f"real_mute_cache_{group_id}"
            cache_data = self.config.get(cache_key, {})
            cache_time = cache_data.get("time", 0)
            cache_interval = 60  # 缓存 60 秒

            # 如果缓存未过期，使用缓存数据
            if current_time - cache_time < cache_interval:
                muted_users = cache_data.get("muted_users", [])
                if sender_id in muted_users:
                    logger.info(f"消息过滤（真实禁言）：用户{sender_id}在群{group_id}被真实禁言，忽略消息")
                    event.stop_event()
                    return
            else:
                # 缓存过期，调用 API 检测真实禁言状态
                try:
                    client = await QQOperaterService.get_client(self, event)
                    if client:
                        shut_list = await client.api.call_action("get_group_shut_list", group_id=group_id)
                        muted_users = []

                        # 解析禁言列表
                        if isinstance(shut_list, list):
                            for user_info in shut_list:
                                user_id = str(user_info.get("user_id", ""))
                                muted_users.append(user_id)
                        elif isinstance(shut_list, dict) and "data" in shut_list:
                            shut_list_data = shut_list["data"]
                            if isinstance(shut_list_data, list):
                                for user_info in shut_list_data:
                                    user_id = str(user_info.get("user_id", ""))
                                    muted_users.append(user_id)

                        # 更新缓存
                        self.config[cache_key] = {
                            "time": current_time,
                            "muted_users": muted_users
                        }

                        # 检查发送者是否被禁言
                        if sender_id in muted_users:
                            logger.info(f"消息过滤（真实禁言）：用户{sender_id}在群{group_id}被真实禁言，忽略消息")
                            event.stop_event()
                            return
                except Exception as e:
                    logger.error(f"真实禁言检测失败：{e}")
                    # 检测失败不影响正常流程，继续处理消息

    async def start_auto_imitate(self):
        """启动自动模仿任务"""
        # 获取QQ客户端
        client = None
        for platform in self.context.platform_manager.platform_insts:
            platform_name = platform.meta().name
            if platform_name in ["aiocqhttp", "qq_official"]:
                if platform_name == "aiocqhttp":
                    client = platform.get_client()
                    break

        if client:
            try:
                # 解析配置中的模仿目标：群号,QQ号
                imitate_config = self.config.get("imitate", "").strip()
                if not imitate_config:
                    logger.info("配置为空, 不启动自动模仿任务")
                    return

                # 验证配置格式
                if "," not in imitate_config:
                    logger.error(
                        f"启动自动模仿任务失败：配置格式错误，应为'群号,QQ号'，实际为'{imitate_config}'"
                    )
                    return

                # 分割并清理配置项
                parts = imitate_config.split(",", 1)
                if len(parts) != 2:
                    logger.error(
                        f"启动自动模仿任务失败：配置格式错误，应为'群号,QQ号'，实际为'{imitate_config}'"
                    )
                    return

                group_id_str = parts[0].strip()
                user_id_str = parts[1].strip()

                # 验证群号和QQ号是否为有效数字
                if not group_id_str.isdigit() or not user_id_str.isdigit():
                    logger.error(
                        f"启动自动模仿任务失败：群号或QQ号必须为数字，实际为'{group_id_str},{user_id_str}'"
                    )
                    return

                group_id = int(group_id_str)
                user_id = int(user_id_str)

                # 检查群是否存在
                groups = await client.api.call_action("get_group_list")
                if not groups:
                    logger.error("启动自动模仿任务失败：获取群列表失败")
                    return

                if isinstance(groups, dict) and "data" in groups:
                    groups = groups["data"]

                # 检查群是否存在
                group_exists = any(group["group_id"] == group_id for group in groups)
                if group_exists:
                    # 启动模仿任务
                    self.imitate_target = {"group_id": group_id, "user_id": user_id}
                    # 创建模仿任务
                    mock_event = MockEvent()
                    self.imitate_task = asyncio.create_task(
                        QQOperaterService._imitate_monitor(self, mock_event)
                    )
                    logger.info(f"自动模仿任务已启动，目标：群{group_id}用户{user_id}")
                else:
                    logger.error(f"启动自动模仿任务失败：群 {group_id} 不存在")
            except ValueError as e:
                logger.error(f"启动自动模仿任务失败：配置值转换错误 - {e}")
            except Exception as e:
                logger.error(f"启动自动模仿任务失败：未知错误 - {e}")


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("获取群列表")
async def get_group_list(self, event: AstrMessageEvent):
    """获取群列表"""
    async for result in QQOperaterService.handle_get_group_list(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("获取群成员信息")
async def get_group_member_info(self, event: AstrMessageEvent):
    """获取群成员信息
    使用示例：
    /获取群成员信息 722252868 1592363624
    """
    async for result in QQOperaterService.handle_get_group_member_info(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("模仿")
async def imitate_user(self, event: AstrMessageEvent):
    """模仿指定用户的头像和群名片
    使用示例：
    /模仿 @目标用户
    /模仿 123456789
    """
    async for result in QQOperaterService.handle_imitate_user(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("停止模仿")
async def stop_imitate(self, event: AstrMessageEvent):
    """停止当前模仿任务
    使用示例：
    /停止模仿
    """
    async for result in QQOperaterService.handle_stop_imitate(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("更新头像")
async def update_avatar(self, event: AstrMessageEvent):
    """更新头像
    使用示例：
    /更新头像
    （然后发送头像图片）
    """
    async for result in QQOperaterService.handle_update_avatar(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("更新头像URL")
async def update_avatar_url(self, event: AstrMessageEvent):
    """通过URL更新头像
    使用示例：
    /更新头像URL http://example.com/avatar.jpg
    """
    async for result in QQOperaterService.handle_update_avatar_url(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("更新昵称")
async def update_nickname(self, event: AstrMessageEvent):
    """更新昵称
    使用示例：
    /更新昵称 新昵称
    """
    async for result in QQOperaterService.handle_update_nickname(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("群发消息")
async def broadcast_message(self, event: AstrMessageEvent):
    """群发消息到所有群或指定群
    使用示例：
    /群发消息 你好，这是测试消息
    /群发消息 你好 123456 789012
    """
    async for result in QQOperaterService.handle_broadcast_message(self, event):
        yield result


# ==================== 禁言功能指令 ====================


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("闭嘴")
async def group_mute(self, event: AstrMessageEvent):
    """群禁言指令
    使用示例：
    /闭嘴 60
    机器人在当前群禁言60秒，期间无视任何用户消息（但可执行指令）
    """
    async for result in QQOperaterService.handle_group_mute(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("不回复")
async def user_mute(self, event: AstrMessageEvent):
    """用户禁言指令
    使用示例：
    /不回复 @用户 60
    机器人在60秒内不回复该用户的消息
    """
    async for result in QQOperaterService.handle_user_mute(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("恢复")
async def unmute(self, event: AstrMessageEvent):
    """恢复禁言指令
    使用示例：
    /恢复
    恢复当前群的禁言状态
    /恢复 @用户
    恢复指定用户的禁言状态
    """
    async for result in QQOperaterService.handle_unmute(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("禁言列表")
async def mute_list(self, event: AstrMessageEvent):
    """查看禁言列表
    使用示例：
    /禁言列表
    """
    async for result in QQOperaterService.handle_mute_list(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("被禁言群列表")
async def muted_group_list(self, event: AstrMessageEvent):
    """获取被禁言的群列表
    使用示例：
    /被禁言群列表
    返回所有被禁言的群列表信息（调用 NapCat API）
    检测全员禁言和机器人单独禁言两种状态
    """
    async for result in QQOperaterService.handle_muted_group_list(self, event):
        yield result


@filter.permission_type(filter.PermissionType.ADMIN)
@filter.command("退出被禁言群")
async def leave_muted_groups(self, event: AstrMessageEvent):
    """一键退出被禁言的群
    使用示例：
    /退出被禁言群
    自动检测并退出所有被禁言的群（全员禁言或机器人被单独禁言）
    """
    async for result in QQOperaterService.handle_leave_muted_groups(self, event):
        yield result



