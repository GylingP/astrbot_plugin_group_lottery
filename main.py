import os
import json
import random
from datetime import datetime
from typing import List, Dict, Any, Optional
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

@register("群抽奖", "Gyling", "群抽奖插件 - 支持创建、参与、加权和匿名抽奖", "v1.4.0")
class GroupLotteryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig): 
        super().__init__(context)
        self.config = config
        self.data_dir = os.path.join("data", "plugins", "group_lottery")
        self.records_file = os.path.join(self.data_dir, "lottery_records.json")
        os.makedirs(self.data_dir, exist_ok=True)
        self.group_lotteries = self._load_records()
        logger.info("群抽奖插件已加载 - 存储逻辑：时间轴模式")

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
        if val is None: return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _is_name_taken(self, group_id: str, name: str) -> bool:
        return name in self.group_lotteries.get(str(group_id), {}).get("cur_lottery", {})

    def _get_draw_interval_count(self, group_id: str, user_id: str) -> int:
        """
        统计历史抽奖中按时间顺序排离现在最近的中奖间隔次数。
        """
        group_data = self.group_lotteries.get(str(group_id), {})
        past_records = group_data.get("past_lottery", {}) # 现在是一个 {timestamp: {name, winners}} 字典
        
        if not past_records:
            return -1

        # 按时间戳降序排列（最近的在前）
        sorted_timestamps = sorted(past_records.keys(), reverse=True)
        
        interval = 0
        found_winner = False
        
        for ts in sorted_timestamps:
            record = past_records[ts]
            winners = record.get("winners", [])
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

    async def _is_group_owner(self, event: AstrMessageEvent, group_id: int, user_id: int) -> bool:
        info = await event.bot.get_group_member_info(group_id=group_id, user_id=user_id, no_cache=True)
        return info["role"] in ["owner", "admin"]

    # --- 指令入口 ---

    @filter.command("新建抽奖")
    async def create_lottery(self, event: AstrMessageEvent, lottery_name: str = None, number_of_winners: Any = None, cooldown: Any = None, weighted: Any = None, anonymous: Any = None):
        group_id = str(event.get_group_id())
        user_id = event.get_sender_id()

        if not lottery_name:
            return event.plain_result("请输入抽奖名称！格式：/新建抽奖 [名称，用于确定哪个抽奖] [中奖人数] <每人中奖冷却次数，默认0> <按历史频率降权，0/1> <不记录历史0/1>")

        if not await self._is_group_owner(event, int(group_id), int(user_id)):
            return event.plain_result("只有群主或管理员才能创建抽奖！")

        n_winners = self._safe_int(number_of_winners, 1)
        c_down = self._safe_int(cooldown, 0)
        is_weighted = self._safe_int(weighted, 0)
        is_anon = self._safe_int(anonymous, 0)

        if None in [n_winners, c_down, is_weighted, is_anon]:
            return event.plain_result("数据非法！请确保人数、冷却等参数输入的是数字。")

        if self._is_name_taken(group_id, lottery_name):
            return event.plain_result(f"抽奖名'{lottery_name}'已存在！")

        group_data = self.group_lotteries.setdefault(group_id, {"cur_lottery": {}, "past_lottery": {}})
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
        if not lottery_name: return event.plain_result("请输入抽奖名称！")

        group_id = str(event.get_group_id())
        user_id = str(event.get_sender_id())

        lotteries = self.group_lotteries.get(group_id, {}).get("cur_lottery", {})
        if lottery_name not in lotteries:
            return event.plain_result(f"抽奖'{lottery_name}'不存在！")

        current_lottery = lotteries[lottery_name]
        cooldown_limit = current_lottery.get("cooldown", 0)

        if any(str(r["user_id"]) == user_id for r in current_lottery.get("records", [])):
            return event.plain_result(f"你已参与了本次'{lottery_name}'，请静候佳音。")

        if cooldown_limit > 0:
            current_interval = self._get_draw_interval_count(group_id, user_id)
            if current_interval > -1 and current_interval < cooldown_limit:
                return event.plain_result(f"❌ 冷却中！需间隔 {cooldown_limit} 次开奖，当前仅间隔 {current_interval} 次。")

        self._add_participant(group_id, user_id, lottery_name)
        event.set_result(MessageEventResult().message(f"📝 成功参与抽奖'{lottery_name}'！"))

    @filter.command("一键抽奖", alias={'一键开奖'})
    async def draw_lottery_cmd(self, event: AstrMessageEvent, lottery_name: str = None):
        if not lottery_name: yield event.plain_result("请输入开奖名称！"); return

        group_id = str(event.get_group_id())
        group_data = self.group_lotteries.get(group_id, {})
        lottery_info = group_data.get("cur_lottery", {}).get(lottery_name)
        
        if not await self._is_group_owner(event, int(group_id), int(event.get_sender_id())):
            yield event.plain_result("权限不足！"); return

        if not lottery_info:
            yield event.plain_result(f"未找到进行中的'{lottery_name}'！"); return

        records = lottery_info.get("records", [])
        if not records:
            yield event.plain_result(f"'{lottery_name}'目前无人参与，可直接删除抽奖。"); return

        num = lottery_info.get("number_of_winners", 1)
        is_weighted = lottery_info.get("weighted", False)
        unique_participants = list(set(str(r["user_id"]) for r in records))
        
        # --- 抽奖逻辑 ---
        if is_weighted:
            past_records = group_data.get("past_lottery", {})
            win_counts = {}
            # 从所有历史记录中筛选属于该名称的记录来计算权重
            for record in past_records.values():
                if record.get("name") == lottery_name:
                    for winner in record.get("winners", []):
                        w_id = str(winner)
                        win_counts[w_id] = win_counts.get(w_id, 0) + 1
            
            weights = [1.0 / (1.0 + win_counts.get(uid, 0)) for uid in unique_participants]
            sample_size = min(num, len(unique_participants))
            
            winners = []
            temp_p, temp_w = unique_participants.copy(), weights.copy()
            for _ in range(sample_size):
                if not temp_p: break
                pick = random.choices(temp_p, weights=temp_w, k=1)[0]
                winners.append(pick)
                idx = temp_p.index(pick)
                temp_p.pop(idx); temp_w.pop(idx)
        else:
            winners = random.sample(unique_participants, min(num, len(unique_participants)))

        # --- 存储历史 (新逻辑：时间戳为键) ---
        if not lottery_info.get("anonymous"):
            now_ts = datetime.now().isoformat()
            past_records = group_data.setdefault("past_lottery", {})
            past_records[now_ts] = {
                "name": lottery_name,
                "winners": winners
            }

        # 清除当前抽奖
        del self.group_lotteries[group_id]["cur_lottery"][lottery_name]
        self._save_records()
        
        # --- 构建消息 ---
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
        yield event.chain_result(chain)

    @filter.command("查询抽奖")
    async def query_lottery(self, event: AstrMessageEvent, lottery_name: str = None):
        if not lottery_name: return event.plain_result("请输入抽奖名称！")
        
        group_id = str(event.get_group_id())
        group_data = self.group_lotteries.get(group_id, {})
        
        # 1. 先查进行中
        cur_lotteries = group_data.get("cur_lottery", {})
        if lottery_name in cur_lotteries:
            info = cur_lotteries[lottery_name]
            records = info.get("records", [])
            u_count = len(set(r["user_id"] for r in records))
            msg = [
                f"🎲 抽奖进行中 - '{lottery_name}'",
                f"• 设奖人数：{info.get('number_of_winners')}",
                f"• 当前人数：{u_count}",
                f"• 降权开启：{info.get('weighted')}",
                "💡 使用 /一键抽奖 结束"
            ]
            return event.plain_result("\n".join(msg))
        
        # 2. 再查历史 (新扁平化结构查询)
        past_records = group_data.get("past_lottery", {})
        # 筛选出属于该名称的记录
        relevant_past = [
            {"ts": ts, **data} for ts, data in past_records.items() 
            if data.get("name") == lottery_name
        ]
        
        if relevant_past:
            # 按时间排序
            relevant_past.sort(key=lambda x: x['ts'], reverse=True)
            msg = [f"📜 历史记录 - '{lottery_name}'", f"开奖次数：{len(relevant_past)}", ""]
            for item in relevant_past[:5]: # 只显最近5次
                time_str = item['ts'].split('T')[0] + " " + item['ts'].split('T')[1][:5]
                msg.append(f"· [{time_str}] 中奖者: {', '.join(item['winners'])}")
            return event.plain_result("\n".join(msg))
        
        return event.plain_result(f"未找到抽奖'{lottery_name}'")

    @filter.command("抽奖列表")
    async def list_lotteries(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id())
        cur_lotteries = self.group_lotteries.get(group_id, {}).get("cur_lottery", {})
        if not cur_lotteries: return event.plain_result("目前没人在抽奖哦。")
        
        msg = ["🎯 进行中的抽奖："]
        for idx, (name, info) in enumerate(cur_lotteries.items(), 1):
            p_count = len(set(r["user_id"] for r in info.get("records", [])))
            msg.append(f"{idx}. {name} ({p_count}人参与)")
        return event.plain_result("\n".join(msg))

    @filter.command("删除抽奖")
    async def delete_lottery(self, event: AstrMessageEvent, lottery_name: str = None):
        if not lottery_name: return event.plain_result("请输入名称！")
        group_id = str(event.get_group_id())
        if not await self._is_group_owner(event, int(group_id), int(event.get_sender_id())):
            return event.plain_result("权限不足。")
        
        if lottery_name in self.group_lotteries.get(group_id, {}).get("cur_lottery", {}):
            del self.group_lotteries[group_id]["cur_lottery"][lottery_name]
            self._save_records()
            return event.plain_result(f"已删除'{lottery_name}'。")
        return event.plain_result("找不到该抽奖。")

    async def terminate(self):
        self._save_records()
    
    # @filter.command("重置抽奖数据")
    # async def reset_lottery_data(self, event: AstrMessageEvent):
    #     """调试专用：彻底删除记录文件并重置内存数据"""
    #     group_id = str(event.get_group_id())
    #     user_id = event.get_sender_id()
        
    #     # 这种危险操作，建议还是留个权限校验，或者你可以临时注释掉
    #     if not await self._is_group_owner(event, int(group_id), int(user_id)):
    #         return event.plain_result("❌ 只有管理员可以执行重置操作。")
            
    #     try:
    #         # 1. 清空内存中的对象
    #         self.group_lotteries = {}
            
    #         # 2. 尝试删除物理文件
    #         if os.path.exists(self.records_file):
    #             os.remove(self.records_file)
    #             message = "🚮 抽奖记录文件已物理删除，内存已同步重置。"
    #         else:
    #             message = "📭 文件本身就不存在，但内存数据已确保清空。"
            
    #         # 3. 顺手把文件夹补回来（防止下次写入报错，虽然save_records也会创建）
    #         os.makedirs(self.data_dir, exist_ok=True)
            
    #         return event.plain_result(message)
    #     except Exception as e:
    #         logger.error(f"重置失败: {e}")
    #         return event.plain_result(f"❌ 重置过程中发生错误: {e}")