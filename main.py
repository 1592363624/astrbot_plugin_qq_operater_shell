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
                imitate_config = self.config.get('imitate', '')
                if ',' in imitate_config:
                    group_id_str, user_id_str = imitate_config.split(',', 1)
                    group_id = int(group_id_str.strip())
                    user_id = int(user_id_str.strip())
                    
                    # 检查群是否存在
                    groups = await client.api.call_action('get_group_list')
                    if groups:
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
                            from .qq_operater_service import QQOperaterService
                            # 注意：这里需要一个事件对象，但初始化时可能没有事件
                            # 因此我们创建一个简单的事件模拟对象
                            class MockEvent:
                                def get_author_id(self):
                                    return None
                                
                                def get_platform_name(self):
                                    return "aiocqhttp"
                            
                            mock_event = MockEvent()
                            self.imitate_task = asyncio.create_task(
                                QQOperaterService._imitate_monitor(self, mock_event)
                            )
                        else:
                            logger.error(f"启动自动模仿任务失败：群 {group_id} 不存在")
            except Exception as e:
                logger.error(f"启动自动模仿任务失败: {e}")


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
