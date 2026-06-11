<div align="center">

![:shell](https://count.getloli.com/@github_monitor_shell?name=github_monitor_shell&theme=minecraft&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)


[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-Shell-blue)](https://github.com/1592363624)

</div>

## 功能说明

本插件仅支持QQ平台（aiocqhttp），提供QQ群管理操作功能。所有命令需要**管理员权限**。

### 命令列表

| 命令 | 说明 | 使用示例 |
|------|------|----------|
| `/获取群列表` | 获取机器人所在的所有QQ群列表 | `/获取群列表` |
| `/获取群成员信息` | 获取指定群中特定成员的详细信息 | `/获取群成员信息 722252868 1592363624` |
| `/模仿` | 让机器人模仿指定用户的头像和群名片 | `/模仿 @目标用户` 或 `/模仿 123456789` |
| `/停止模仿` | 停止当前模仿任务 | `/停止模仿` |
| `/更新头像` | 通过发送图片更新机器人头像 | `/更新头像`（然后发送图片） |
| `/更新头像URL` | 通过URL更新机器人头像 | `/更新头像URL http://example.com/avatar.jpg` |
| `/更新昵称` | 更改机器人的QQ昵称 | `/更新昵称 新昵称` |
| `/群发消息` | 群发消息到所有群或指定群 | `/群发消息 你好` 或 `/群发消息 你好 123456 789012` |

### 功能详情

#### 获取群列表
列出机器人所在的所有QQ群，显示群号和群名。

#### 获取群成员信息
查看指定群中特定成员的详细信息，包括：昵称、群名片、性别、年龄、地区、加入时间、最后发言时间、身份（群主/管理员/成员）、专属头衔。

#### 模仿功能
让机器人自动模仿指定用户的头像和群名片。支持：
- 通过@用户或QQ号指定模仿目标
- 定时检查目标用户信息变化并同步更新（默认每10分钟检查一次）
- 如果已有模仿目标，会询问是否替换
- 目标用户信息变化时自动更新机器人头像和群名片
- 使用MD5哈希检测头像变化，避免重复更新

#### 更新头像
通过发送图片或URL更新机器人头像。支持多种方式：
- 直接发送图片
- 通过URL更新
- 自动处理CQ码中的图片

#### 更新昵称
更改机器人的QQ昵称。

#### 群发消息
将消息发送到机器人所在的所有群或指定群：
- `/群发消息 消息内容` - 发送到所有群
- `/群发消息 消息内容 群号1 群号2 ...` - 发送到指定群

自动统计并返回成功/失败数量。

## 🐔 联系作者

- **反馈**：欢迎在 [GitHub Issues](https://github.com/1592363624/astrbot_plugin_qq_operater_shell/issues) 提交问题或建议
QQ群:91219736
telegram:[巅峰阁](https://t.me/ShellDFG)
