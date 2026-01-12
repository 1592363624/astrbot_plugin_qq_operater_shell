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
from datetime import datetime
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger


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
    async def get_client(self, event: AstrMessageEvent = None):
        """获取QQ客户端实例
        
        Args:
            self: 插件实例
            event: 消息事件对象
            
        Returns:
            客户端实例或None
        """
        # 如果有事件且不是MockEvent，优先从事件获取（适用于事件响应中）
        if event:
            try:
                if event.get_platform_name() == "aiocqhttp":
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    # 只有真正的AiocqhttpMessageEvent才使用event.bot
                    if isinstance(event, AiocqhttpMessageEvent):
                        return event.bot
            except AttributeError:
                # 如果是MockEvent或缺少方法，跳过从event获取
                pass
        
        # 否则动态获取平台实例并返回client
        for platform in self.context.platform_manager.platform_insts:
            platform_name = platform.meta().name
            if platform_name in ["aiocqhttp", "qq_official"]:
                # 如果是aiocqhttp平台，直接获取client，不进行类型检查
                if platform_name == "aiocqhttp":
                    return platform.get_client()
        return None
    
    @staticmethod
    async def handle_get_group_list(self, event: AstrMessageEvent):
        """处理获取群列表命令的逻辑
        
        Args:
            self: 插件实例
            event: 消息事件对象
        """
        if client := await QQOperaterService.get_client(self, event):
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
    
    @staticmethod
    async def handle_get_group_member_info(self, event: AstrMessageEvent):
        """处理获取群成员信息命令的逻辑
        
        Args:
            self: 插件实例
            event: 消息事件对象
        """
        if client := await QQOperaterService.get_client(self, event):
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
                        yield event.make_result().message(f"获取群成员信息失败：{ret.get('message', '未知错误')}")
                else:
                    yield event.make_result().message(f"获取群成员信息结果：{ret}")
            except ValueError:
                yield event.make_result().message("参数错误，请输入正确的数字类型群号和用户ID")
        else:
            yield event.make_result().message("当前平台不支持此命令")
    
    @staticmethod
    async def handle_imitate_user(self, event: AstrMessageEvent):
        """处理模仿用户命令的逻辑
        
        Args:
            self: 插件实例
            event: 消息事件对象
        """
        # 检查配置中是否指定了模仿目标
        config_imitate_target = self.config.get('imitate', '')
        if config_imitate_target:
            yield event.make_result().message(f"配置中已指定模仿目标用户 {config_imitate_target}，请先清空配置或使用停止模仿命令")
            return
        
        # 检查是否已在模仿其他用户
        if self.imitate_task:
            yield event.make_result().message("已在模仿其他用户，请先停止当前模仿")
            return
        
        # 获取群ID
        group_id = event.get_group_id()
        if not group_id:
            yield event.make_result().message("请在群聊中使用此命令")
            return
        
        # 解析命令，获取目标用户ID
        # 从消息中提取@mention的用户ID
        target_user_id = None
        
        # 尝试从消息链中提取@mention
        from astrbot.core.message.components import At
        for component in event.get_messages():
            if isinstance(component, At) and component.qq != "all":
                target_user_id = component.qq
                break
        
        # 如果没有@mention，尝试从命令参数中提取
        if not target_user_id:
            cmd_params = event.message_str.split()
            if len(cmd_params) >= 2:
                # 尝试解析参数为用户ID
                try:
                    target_user_id = int(cmd_params[1])
                except ValueError:
                    pass
        
        if not target_user_id:
            yield event.make_result().message("请@需要模仿的用户，或在命令后跟上用户ID")
            return
        
        # 保存目标信息到配置，格式：群号,QQ号
        self.config['imitate'] = f"{group_id},{target_user_id}"
        
        # 存储目标信息
        self.imitate_target = {
            'group_id': group_id,
            'user_id': target_user_id
        }
        
        # 创建模仿任务
        self.imitate_task = asyncio.create_task(
            QQOperaterService._imitate_monitor(self, event)
        )
        
        yield event.make_result().message(f"开始模仿用户 {target_user_id}，每 {self.config.get('imitate_interval', 10)} 分钟更新一次")
    
    @staticmethod
    async def handle_stop_imitate(self, event: AstrMessageEvent):
        """处理停止模仿命令的逻辑
        
        Args:
            self: 插件实例
            event: 消息事件对象
        """
        # 取消任务
        if self.imitate_task:
            self.imitate_task.cancel()
            self.imitate_task = None
            self.imitate_target = None
        
        # 清空配置中的模仿目标
        self.config['imitate'] = ''
        
        yield event.make_result().message("已停止模仿，并清空了配置中的模仿目标")
    
    @staticmethod
    async def _imitate_monitor(self, event: AstrMessageEvent):
        """模仿监控任务，周期性检测目标用户信息并更新
        
        Args:
            self: 插件实例
            event: 消息事件对象
        """
        try:
            client = await QQOperaterService.get_client(self, event)
            if not client:
                return
            
            while True:
                # 检查目标信息是否存在
                if not self.imitate_target:
                    break
                
                group_id = self.imitate_target['group_id']
                user_id = self.imitate_target['user_id']
                
                # 获取目标用户信息
                try:
                    member_info = await client.api.call_action(
                        'get_group_member_info',
                        group_id=group_id,
                        user_id=user_id,
                        no_cache=True
                    )
                    
                    # 处理API返回格式
                    if isinstance(member_info, dict):
                        if member_info.get('status') == 'ok' and 'data' in member_info:
                            member_info = member_info['data']
                    
                    # 获取目标用户的详细信息
                    target_nickname = member_info.get('nickname', '未知')
                    target_card_name = member_info.get('card') or target_nickname
                    
                    logger.info(f"模仿监控：开始处理目标用户 - 群: {group_id}, 用户ID: {user_id}")
                    logger.info(f"模仿监控：获取到目标用户信息 - 昵称: {target_nickname}, 群名片: {target_card_name}")
                    
                    if not target_card_name:
                        logger.warning(f"模仿监控：目标用户 {user_id} 没有昵称或群名片，跳过此次更新")
                        await asyncio.sleep(self.config.get('imitate_interval', 10) * 60)
                        continue
                    
                    # 生成目标用户头像URL
                    avatar_url = f"https://thirdqq.qlogo.cn/g?b=sdk&s=640&nk={user_id}"
                    logger.info(f"模仿监控：生成目标用户头像URL: {avatar_url}")
                    
                    # 更新机器人头像
                    logger.info(f"模仿监控：开始更新机器人头像")
                    try:
                        avatar_result = await client.api.call_action(
                            'set_qq_avatar',
                            file=avatar_url
                        )
                        logger.info(f"模仿监控：更新头像成功，API返回: {avatar_result}")
                    except Exception as e:
                        logger.error(f"模仿监控：更新头像失败: {e}")
                    
                    # 更新机器人群名片
                    logger.info(f"模仿监控：开始更新机器人群名片为: {target_card_name}")
                    try:
                        # 获取机器人自己的ID
                        bot_id = getattr(event, 'get_author_id', lambda: None)()
                        if bot_id:
                            logger.info(f"模仿监控：获取到机器人ID: {bot_id}")
                            card_result = await client.api.call_action(
                                'set_group_card',
                                group_id=group_id,
                                user_id=bot_id,  # 使用机器人自己的ID
                                card=target_card_name
                            )
                            logger.info(f"模仿监控：更新群名片成功，API返回: {card_result}")
                        else:
                            # 如果无法从event获取，尝试其他方式获取机器人ID
                            logger.warning(f"模仿监控：无法从事件获取机器人ID，尝试从客户端获取")
                            # 尝试获取机器人自身信息
                            try:
                                login_info = await client.api.call_action('get_login_info')
                                if isinstance(login_info, dict):
                                    if login_info.get('status') == 'ok' and 'data' in login_info:
                                        login_info = login_info['data']
                                    bot_id = login_info.get('user_id') or login_info.get('uin')
                                    if bot_id:
                                        logger.info(f"模仿监控：从客户端获取到机器人ID: {bot_id}")
                                        card_result = await client.api.call_action(
                                            'set_group_card',
                                            group_id=group_id,
                                            user_id=bot_id,
                                            card=target_card_name
                                        )
                                        logger.info(f"模仿监控：更新群名片成功，API返回: {card_result}")
                                    else:
                                        logger.error(f"模仿监控：无法获取机器人ID，跳过更新群名片")
                            except Exception as e:
                                logger.error(f"模仿监控：获取机器人ID失败: {e}")
                    except Exception as e:
                        logger.error(f"模仿监控：更新群名片失败: {e}")
                    
                except Exception as e:
                    logger.error(f"模仿监控出错: {e}")
                
                # 等待指定时间间隔
                await asyncio.sleep(self.config.get('imitate_interval', 10) * 60)
        
        except asyncio.CancelledError:
            # 任务被取消，正常退出
            pass
        except Exception as e:
            logger.error(f"模仿监控任务异常: {e}")
            # 清理任务状态
            self.imitate_task = None
            self.imitate_target = None
