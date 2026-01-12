"""
QQ操作插件

支持群发机器人所在的所有群、指定群号群发、指定好友QQ号群发,提供任务管理、发送历史查询、统计信息等功能。
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.star import Star

class QQOperaterPlugin(Star):
    """QQ操作插件主类"""
    # 存储平台实例和客户端的全局变量
    qq_platform = None
    qq_client = None
    
    async def initialize(self):
        """当插件被激活时会调用这个方法"""
        # 初始化时不获取平台实例，改为在需要时动态获取
        pass
    
    async def get_client(self, event: AstrMessageEvent = None):
        """获取QQ客户端实例"""
        # 如果有事件，优先从事件获取（适用于事件响应中）
        if event and event.get_platform_name() == "aiocqhttp":
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            assert isinstance(event, AiocqhttpMessageEvent)
            return event.bot
        # 否则动态获取平台实例并返回client
        for platform in self.context.platform_manager.platform_insts:
            platform_name = platform.meta().name
            if platform_name in ["aiocqhttp", "qq_official"]:
                # 如果是aiocqhttp平台，直接获取client，不进行类型检查
                if platform_name == "aiocqhttp":
                    return platform.get_client()
        return None
    
    @filter.command("test_api")
    async def test_api(self, event: AstrMessageEvent):
        """测试调用QQ API"""
        if client := await self.get_client():
            ret = await client.api.call_action('get_login_info')
            yield event.make_result().message(f"调用API结果：{ret}")
        else:
            yield event.make_result().message("未找到QQ客户端实例")
    
    @filter.command("test_event_api")
    async def test_event_api(self, event: AstrMessageEvent):
        """通过事件获取平台实例调用API"""
        if client := await self.get_client(event):
            ret = await client.api.call_action('get_login_info')
            yield event.make_result().message(f"通过事件调用API结果：{ret}")
        else:
            yield event.make_result().message("当前平台不支持此命令")
 