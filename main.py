import os
import json
import random
from datetime import datetime
from typing import List, Dict, Any, Optional
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
import astrbot.api.message_components as Comp

@register("群抽奖", "Gyling", "群抽奖插件 - 支持创建、参与、加权和匿名抽奖", "v1.3.1")
class GroupLotteryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig): 
        super().__init__(context)
        self.config = config
        self.data_dir = os.path.join("data", "plugins", "group_lottery")
        self.records_file = os.path.join(self.data_dir, "lottery_records.json")
        os.makedirs(self.data_dir, exist_ok=True)
        self.group_lotteries = self._load_records()
        logger.info("群抽奖插件已加载")

    # --- 辅助方法 ---
    def _load_records(self) -> Dict[str, Any]: 
        try: 
            if os.path.exists(self.records_file):
                with open(self.records_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"加载记录文件失败: {e}")
            return {}

    def _save_records(self):
        try:
            with open(self.records_file, 'w', encoding='utf-8') as f:
                json.dump(self.group_lotteries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存记录文件失败: {e}")

    def _safe_int(self, val: Any, default: int = 0) -> Optional[int]:
        """安全转换整数，失败返回 None 表示数据非法"""
        if val is None: return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _is_name_taken(self, group_id: str, name: str) -> bool:
        return name in self.group_lotteries.get(str(group_id), {}).get("cur_lottery", {})

    def _get_draw_interval_count(self, group_id: str, user_id: str, lottery_name: str) -> int:
            """
            统计历史抽奖中按时间顺序排离现在最近的中奖间隔次数。
            如果从未中奖，返回 0。
            """
            group_data = self.group_lotteries.get(str(group_id), {})
            past_records = group_data.get("past_lottery", {}).get(lottery_name, [])
            
            if not past_records:
                return 0

            interval = 0
            found_winner = False
            
            # 从最近的历史开奖记录向回溯
            for record in reversed(past_records):
                winners = record.get("winners", [])
                # 检查用户是否在中奖名单中
                if str(user_id) in [str(w) for w in winners]:
                    found_winner = True
                    break
                interval += 1
                
            return interval if found_winner else -1

    def _add_participant(self, group_id: str, user_id: str, lottery_name: str):
        group_data = self.group_lotteries.setdefault(str(group_id), {"cur_lottery": {}, "past_lottery": {}})
        lottery_data = group_data["cur_lottery"].setdefault(lottery_name, {"records": []})
        lottery_data["records"].append({"user_id": user_id, "timestamp": datetime.now().isoformat()})
        self._save_records()

    async def _is_group_owner(self, event: AstrMessageEvent,group_id:int,user_id:int) -> bool:
        info = await event.bot.get_group_member_info(
                group_id=group_id, user_id=user_id, no_cache=True
            )
        return info["role"] == "owner" or info["role"] == "admin" 

    # --- 指令入口 ---

    @filter.command("新建抽奖")
    async def create_lottery(self, event: AstrMessageEvent, lottery_name: str = None, number_of_winners: Any = None, cooldown: Any = None, weighted: Any = None, anonymous: Any = None):
        group_id = event.get_group_id()
        user_id = event.get_sender_id()

        # 1. 必需参数判断
        if not lottery_name:
            return event.plain_result("请输入抽奖名称！格式：/新建抽奖 [名称，用于确定哪个抽奖] [中奖人数] <每人中奖冷却次数，默认0> <按历史频率降权，0/1> <不记录历史0/1>")

        # 2. 权限判断
        if not await self._is_group_owner(event , int(group_id) , int(user_id) ):
            return event.plain_result("只有群主或管理员才能创建抽奖！")

        # 3. 数据合法性校验与转换
        n_winners = self._safe_int(number_of_winners, 1)
        c_down = self._safe_int(cooldown, 0)
        is_weighted = self._safe_int(weighted, 0)
        is_anon = self._safe_int(anonymous, 0)

        if None in [n_winners, c_down, is_weighted, is_anon]:
            return event.plain_result("数据非法！人数、冷却、布尔值请确保输入的是数字。")

        if self._is_name_taken(group_id, lottery_name):
            return event.plain_result(f"抽奖名'{lottery_name}'已存在，请更换名称！")

        # 4. 存储逻辑
        group_data = self.group_lotteries.setdefault(str(group_id), {"cur_lottery": {}, "past_lottery": {}})
        group_data["cur_lottery"][lottery_name] = {
            "number_of_winners": n_winners,
            "cooldown": c_down,
            "weighted": bool(is_weighted),
            "anonymous": bool(is_anon),
            "records": []
        }
        self._save_records()
        event.set_result(MessageEventResult().message(f"抽奖'{lottery_name}'已创建！\n每次抽取: {n_winners}人\n每人中奖冷却期: {c_down}次\n{'已' if is_weighted else '未'}开启按频次降频，{'未' if is_anon else '已'}开启记录历史"))

    @filter.command("参与抽奖", alias={'p'})
    async def participate_lottery(self, event: AstrMessageEvent, lottery_name: str = None):
        if not lottery_name:
            return event.plain_result("请输入要参与的抽奖名称！")

        group_id = str(event.get_group_id())
        user_id = str(event.get_sender_id())

        lotteries = self.group_lotteries.get(group_id, {}).get("cur_lottery", {})
        if lottery_name not in lotteries:
            return event.plain_result(f"抽奖'{lottery_name}'不存在！")

        current_lottery = lotteries[lottery_name]
        cooldown_limit = current_lottery.get("cooldown", 0)

        # 1. 检查当前轮次是否已报名（防止单次开奖重复报名）
        if any(str(r["user_id"]) == user_id for r in current_lottery.get("records", [])):
            return event.plain_result(f"你已经参与了本次'{lottery_name}'，请等待开奖。")

        if cooldown_limit > 0 :
            # 2. 冷却期逻辑
            current_interval = self._get_draw_interval_count(group_id, user_id, lottery_name)
             
            if current_interval > -1 and  current_interval < cooldown_limit:
                return event.plain_result(
                    f"你尚在冷却期内！\n"
                    f"需要间隔: {cooldown_limit} 次开奖\n"
                    f"当前间隔: {current_interval} 次"
                )

        # 3. 记录参与
        self._add_participant(group_id, user_id, lottery_name)
        event.set_result(MessageEventResult().message(f"你已成功参与抽奖'{lottery_name}'！"))

    @filter.command("一键抽奖", alias={'一键开奖'})
    async def draw_lottery_cmd(self, event: AstrMessageEvent, lottery_name: str = None):
        if not lottery_name:
            yield event.plain_result("请输入要执行开奖的抽奖名称！")
            return

        group_id = str(event.get_group_id())
        group_data = self.group_lotteries.get(group_id, {})
        lottery_info = group_data.get("cur_lottery", {}).get(lottery_name)
        
        if not await self._is_group_owner(event, int(event.get_group_id()), int(event.get_sender_id())):
            yield event.plain_result("只有群主或管理员才能进行抽奖！")
            return

        if not lottery_info:
            yield event.plain_result(f"未找到抽奖'{lottery_name}'！")
            return

        records = lottery_info.get("records", [])
        if not records:
            yield event.plain_result(f"抽奖'{lottery_name}'目前还没有人参与哦。")
            return

        # 1. 准备抽奖池
        num = lottery_info.get("number_of_winners", 1)
        is_weighted = lottery_info.get("weighted", False)
        
        # 获取当前所有唯一的参与者 UID
        unique_participants = list(set(str(r["user_id"]) for r in records))
        
        # 2. 计算权重 (如果开启了高频降权)
        if is_weighted:
            past_records = group_data.get("past_lottery", {}).get(lottery_name, [])
            
            # 统计每个人的历史中奖次数
            win_counts = {}
            for record in past_records:
                for winner in record.get("winners", []):
                    w_id = str(winner)
                    win_counts[w_id] = win_counts.get(w_id, 0) + 1
            
            # 核心算法：权重 = 1 / (1 + 历史中奖次数)
            weights = []
            for uid in unique_participants:
                count = win_counts.get(uid, 0)
                # 即使中奖多次，权重也不会归零，只是极低
                weights.append(1.0 / (1.0 + count))
            
            # 按照计算出的权重抽取
            # k 需要取 参与人数和需求人数的最小值，防止报错
            sample_size = min(num, len(unique_participants))
            
            # 注意：random.choices 是有放回抽样，我们需要不放回地抽取指定人数
            winners = []
            temp_participants = unique_participants.copy()
            temp_weights = weights.copy()
            
            for _ in range(sample_size):
                if not temp_participants: break
                pick = random.choices(temp_participants, weights=temp_weights, k=1)[0]
                winners.append(pick)
                # 抽中后移除，实现不放回抽样
                idx = temp_participants.index(pick)
                temp_participants.pop(idx)
                temp_weights.pop(idx)
        else:
            # 传统等概率随机抽取
            winners = random.sample(unique_participants, min(num, len(unique_participants)))

        # 3. 记录历史 (如果不是匿名抽奖)
        if not lottery_info.get("anonymous"):
            past_data = group_data.setdefault("past_lottery", {}).setdefault(lottery_name, [])
            past_data.append({"winners": winners, "timestamp": datetime.now().isoformat()})

        self._save_records()
        
        chain = [
            Comp.Plain(f"🎉 抽奖 '{lottery_name}' 开奖啦！\n\u200b"), 
            Comp.Plain("中奖名单如下：") 
        ]

        # 循环中奖者名单
        for winner_id in winners:
            chain.append(Comp.Plain("\u200b\n\u200b")) 
            chain.append(Comp.Plain("\u200b - \u200b"))
            chain.append(Comp.Plain(winner_id))
            chain.append(Comp.At(qq=winner_id))
                
        del self.group_lotteries[group_id]["cur_lottery"][lottery_name]
        # 使用 chain_result 发送完整消息链
        yield event.chain_result(chain)
    @filter.command("删除抽奖")
    async def delete_lottery(self, event: AstrMessageEvent, lottery_name: str = None):
        if not lottery_name:
            return event.plain_result("请输入要删除的抽奖名称！")

        group_id = str(event.get_group_id())
        if not await self._is_group_owner(event, int(event.get_group_id()), int(event.get_sender_id())):
            return event.plain_result("只有群主或管理员才能删除抽奖！")

        if lottery_name not in self.group_lotteries.get(group_id, {}).get("cur_lottery", {}):
            return event.plain_result(f"不存在名为'{lottery_name}'的抽奖。")

        del self.group_lotteries[group_id]["cur_lottery"][lottery_name]
        self._save_records()
        event.set_result(MessageEventResult().message(f"抽奖'{lottery_name}'已成功删除。"))

    async def terminate(self):
        self._save_records()
        
    @filter.command("查询抽奖")
    async def query_lottery(self, event: AstrMessageEvent, lottery_name: str = None):
        """查询抽奖状态 - 显示抽奖的详细参数和参与情况"""
        if not lottery_name:
            return event.plain_result("请输入要查询的抽奖名称！格式：/查询抽奖 [抽奖名称]")
        
        group_id = str(event.get_group_id())
        group_data = self.group_lotteries.get(group_id, {})
        cur_lotteries = group_data.get("cur_lottery", {})
        
        if lottery_name not in cur_lotteries:
            # 检查是否是历史抽奖
            past_lotteries = group_data.get("past_lottery", {})
            if lottery_name in past_lotteries:
                return await self._query_past_lottery(event, lottery_name, past_lotteries[lottery_name])
            else:
                return event.plain_result(f"未找到名为'{lottery_name}'的抽奖（包括进行中和已结束的）")
        
        lottery_info = cur_lotteries[lottery_name]
        records = lottery_info.get("records", [])
        
        # 统计参与信息
        participants = {}
        for record in records:
            user_id = record["user_id"]
            participants[user_id] = participants.get(user_id, 0) + 1
        
        # 获取唯一参与人数
        unique_participants = len(participants)
        total_participations = len(records)
        
        # 准备返回信息
        status_msg = [
            f"🎲 抽奖状态查询 - '{lottery_name}'",
            "=" * 30,
            f"📊 抽奖参数：",
            f"  • 中奖人数：{lottery_info.get('number_of_winners', 1)} 人",
            f"  • 冷却期：{lottery_info.get('cooldown', 0)} 次",
            f"  • 按频次降权：{'✅ 是' if lottery_info.get('weighted', False) else '❌ 否'}",
            f"  • 不记录历史：{'✅ 是' if lottery_info.get('anonymous', False) else '❌ 否'}",
            "",
            f"👥 参与情况：",
            f"  • 总参与人次：{total_participations}",
            f"  • 唯一参与人数：{unique_participants}",
            ""
        ]
        
        # 显示参与详情（最多显示20个）
        if participants:
            status_msg.append("📝 参与详情：")
            # 按参与次数排序
            sorted_participants = sorted(participants.items(), key=lambda x: x[1], reverse=True)
            
            for idx, (user_id, count) in enumerate(sorted_participants[:20], 1):
                status_msg.append(f"  {idx}. 用户{user_id}：{count} 次")
            
            if len(sorted_participants) > 20:
                status_msg.append(f"  ... 还有 {len(sorted_participants) - 20} 人未显示")
        else:
            status_msg.append("📝 暂无参与者")
        
        # 显示抽奖状态
        status_msg.extend([
            "",
            "🔮 抽奖状态：进行中 ✅",
            "💡 提示：可使用 /一键抽奖 开奖"
        ])
        
        return event.plain_result("\n".join(status_msg))

    async def _query_past_lottery(self, event: AstrMessageEvent, lottery_name: str, past_records: list):
        """查询历史抽奖记录"""
        if not past_records:
            return event.plain_result(f"抽奖'{lottery_name}'没有历史记录")
        
        status_msg = [
            f"📜 历史抽奖查询 - '{lottery_name}'",
            "=" * 30,
            f"📊 开奖次数：{len(past_records)} 次",
            ""
        ]
        
        # 显示最近5次开奖记录
        recent_records = past_records[-5:] if len(past_records) > 5 else past_records
        status_msg.append("🕒 最近开奖记录：")
        
        for idx, record in enumerate(reversed(recent_records), 1):
            timestamp = record.get("timestamp", "未知时间")
            winners = record.get("winners", [])
            # 格式化时间显示
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%m-%d %H:%M")
            except:
                time_str = timestamp[:16] if len(timestamp) > 16 else timestamp
            
            status_msg.append(f"\n  {idx}. 开奖时间：{time_str}")
            status_msg.append(f"     中奖者：{', '.join(winners)}")
        
        if len(past_records) > 5:
            status_msg.append(f"\n  ... 还有 {len(past_records) - 5} 次历史开奖记录")
        
        status_msg.append("\n💡 提示：抽奖已结束，以上为历史记录")
        
        return event.plain_result("\n".join(status_msg))
    
    @filter.command("抽奖列表")
    async def list_lotteries(self, event: AstrMessageEvent):
        """列出当前群组所有进行中的抽奖"""
        group_id = str(event.get_group_id())
        group_data = self.group_lotteries.get(group_id, {})
        cur_lotteries = group_data.get("cur_lottery", {})
        
        if not cur_lotteries:
            return event.plain_result("当前群组没有进行中的抽奖")
        
        status_msg = [
            f"🎯 当前群组进行中的抽奖列表",
            "=" * 30,
            ""
        ]
        
        for idx, (name, info) in enumerate(cur_lotteries.items(), 1):
            participants_count = len(set(r["user_id"] for r in info.get("records", [])))
            status_msg.append(f"{idx}. 【{name}】")
            status_msg.append(f"   • 中奖人数：{info.get('number_of_winners', 1)}")
            status_msg.append(f"   • 参与人数：{participants_count}")
            status_msg.append(f"   • 冷却期：{info.get('cooldown', 0)}")
            status_msg.append("")
        
        status_msg.append("💡 提示：使用 /查询抽奖 [名称] 查看详细信息")
        
        return event.plain_result("\n".join(status_msg))