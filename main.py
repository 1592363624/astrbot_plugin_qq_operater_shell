"""
QQ操作插件

支持群发机器人所在的所有群、指定群号群发、指定好友QQ号群发,提供任务管理、发送历史查询、统计信息等功能。
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.star import Star
from datetime import datetime

class QQOperaterPlugin(Star):
    """QQ操作插件主类"""
    # 存储平台实例和客户端的全局变量
    qq_platform = None
    qq_client = None
    
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
    
@filter.command("获取群成员信息")
async def get_group_member_info(self, event: AstrMessageEvent):
    """获取群成员信息
    使用示例：
    /获取群成员信息 722252868 1592363624
    """
    if client := await self.get_client(event):
        # 解析命令参数，获取group_id和user_id
        cmd_params = event.message_str.split()
        if len(cmd_params) < 3:
            yield event.make_result().message("参数不足，请使用：/获取群成员信息 <群号> <用户ID>")
            return
        
        try:
            group_id = int(cmd_params[1])
            user_id = int(cmd_params[2])
            # 可选参数no_cache，默认为false
            no_cache = False
            if len(cmd_params) > 3 and cmd_params[3].lower() in ["true", "1", "yes"]:
                no_cache = True
            
            # 调用get_group_member_info API
            ret = await client.api.call_action(
                'get_group_member_info',
                group_id=group_id,
                user_id=user_id,
                no_cache=no_cache
            )
            
            # 格式化输出结果
            if isinstance(ret, dict):
                if ret.get('status') == 'ok' and 'data' in ret:
                    # 处理包含status和data字段的格式
                    member_info = ret['data']
                else:
                    # 检查是否直接返回成员信息字典（兼容不同API返回格式）
                    member_info = ret
                
                # 验证是否是有效的成员信息（包含group_id和user_id）
                if 'group_id' in member_info and 'user_id' in member_info:
                    result = "群成员信息：\n"
                    result += f"🏢 群号：{member_info.get('group_id')}\n"
                    result += f"🆔 用户ID：{member_info.get('user_id')}\n"
                    result += f"📛 昵称：{member_info.get('nickname')}\n"
                    result += f"💳 群名片：{member_info.get('card') or '无'}\n"
                    result += f"👤 性别：{self.format_gender(member_info.get('sex'))}\n"
                    result += f"📅 年龄：{member_info.get('age') or '未知'}\n"
                    result += f"📍 地区：{member_info.get('area') or '未知'}\n"
                    result += f"📌 加入时间：{self.format_timestamp(member_info.get('join_time'))}\n"
                    result += f"💬 最后发言时间：{self.format_timestamp(member_info.get('last_sent_time'))}\n"
                    result += f"👑 身份：{'群主' if member_info.get('role') == 'owner' else '管理员' if member_info.get('role') == 'admin' else '成员'}\n"
                    result += f"🏅 专属头衔：{member_info.get('title') or '无'}\n"
                    yield event.make_result().message(result)
                else:
                    # 如果不是有效的成员信息，返回失败信息
                    yield event.make_result().message(f"获取群成员信息失败：{ret.get('message', '未知错误')}")
            else:
                yield event.make_result().message(f"获取群成员信息结果：{ret}")
        except ValueError:
            yield event.make_result().message("参数错误，请输入正确的数字类型群号和用户ID")
    else:
        yield event.make_result().message("当前平台不支持此命令")