"""
QQ操作插件

【仅QQ】调用QQ接口来实现QQ群操作管理等行为。
"""

import asyncio
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.star import Star
from astrbot.api import AstrBotConfig, logger
from astrbot.core.star.context import Context
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
        self.imitate_cache = None  # 存储上一次模仿的信息，用于对比 {avatar_url, nickname, card}
    
    async def initialize(self):
        """插件初始化完成后调用"""
        await super().initialize()
        # 检查配置中是否有模仿目标
        imitate_target = self.config.get('imitate', '')
        if imitate_target:
            # 启动自动模仿任务
            await self.start_auto_imitate()
    
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
                imitate_config = self.config.get('imitate', '').strip()
                if not imitate_config:
                    logger.info("配置为空, 不启动自动模仿任务")
                    return
                    
                # 验证配置格式
                if ',' not in imitate_config:
                    logger.error(f"启动自动模仿任务失败：配置格式错误，应为'群号,QQ号'，实际为'{imitate_config}'")
                    return
                    
                # 分割并清理配置项
                parts = imitate_config.split(',', 1)
                if len(parts) != 2:
                    logger.error(f"启动自动模仿任务失败：配置格式错误，应为'群号,QQ号'，实际为'{imitate_config}'")
                    return
                    
                group_id_str = parts[0].strip()
                user_id_str = parts[1].strip()
                
                # 验证群号和QQ号是否为有效数字
                if not group_id_str.isdigit() or not user_id_str.isdigit():
                    logger.error(f"启动自动模仿任务失败：群号或QQ号必须为数字，实际为'{group_id_str},{user_id_str}'")
                    return
                    
                group_id = int(group_id_str)
                user_id = int(user_id_str)
                
                # 检查群是否存在
                groups = await client.api.call_action('get_group_list')
                if not groups:
                    logger.error("启动自动模仿任务失败：获取群列表失败")
                    return
                    
                if isinstance(groups, dict) and 'data' in groups:
                    groups = groups['data']
                
                # 检查群是否存在
                group_exists = any(group['group_id'] == group_id for group in groups)
                if group_exists:
                    # 启动模仿任务
                    self.imitate_target = {
                        'group_id': group_id,
                        'user_id': user_id
                    }
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


@filter.command("获取群列表")
async def get_group_list(self, event: AstrMessageEvent):
    """获取群列表"""
    async for result in QQOperaterService.handle_get_group_list(self, event):
        yield result
    
@filter.command("获取群成员信息")
async def get_group_member_info(self, event: AstrMessageEvent):
    """获取群成员信息
    使用示例：
    /获取群成员信息 722252868 1592363624
    """
    async for result in QQOperaterService.handle_get_group_member_info(self, event):
        yield result


@filter.command("模仿")
async def imitate_user(self, event: AstrMessageEvent):
    """模仿指定用户的头像和群名片
    使用示例：
    /模仿 @目标用户
    /模仿 123456789
    """
    async for result in QQOperaterService.handle_imitate_user(self, event):
        yield result


@filter.command("停止模仿")
async def stop_imitate(self, event: AstrMessageEvent):
    """停止当前模仿任务
    使用示例：
    /停止模仿
    """
    async for result in QQOperaterService.handle_stop_imitate(self, event):
        yield result
