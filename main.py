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
    
    
@filter.command("获取群列表")
async def get_group_list(self, event: AstrMessageEvent):
    """获取群列表"""
    if client := await self.get_client(event):
        # 调用get_group_list API，默认no_cache为false
        ret = await client.api.call_action('get_group_list', no_cache=False)
        # 格式化输出结果
        if isinstance(ret, list):
            # 直接返回群列表数组的情况
            groups = ret
            result = f"共获取到 {len(groups)} 个群：\n"
            for group in groups:
                result += f"群号：{group.get('group_id')}，群名：{group.get('group_name')}\n"
            yield event.make_result().message(result)
        elif isinstance(ret, dict) and 'data' in ret:
            # 兼容返回字典且包含data字段的情况
            groups = ret['data']
            result = f"共获取到 {len(groups)} 个群：\n"
            for group in groups:
                result += f"群号：{group.get('group_id')}，群名：{group.get('group_name')}\n"
            yield event.make_result().message(result)
        else:
            yield event.make_result().message(f"获取群列表结果：{ret}")
    else:
        yield event.make_result().message("当前平台不支持此命令")