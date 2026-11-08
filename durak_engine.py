# -*- coding: utf-8 -*-
"""
Durak Online Engine & Game State Machine
Supports Podkidnoy Durak with 2-6 players (humans + AI bots).
"""

import time
import random
import threading
import html
from typing import List, Dict, Optional, Tuple
import durak_ai

# Card constants
RANKS = ['6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
SUITS = ['S', 'H', 'D', 'C']
RANK_VALUES = {'6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
SUIT_SYMBOLS = {'S': '♠', 'H': '♥', 'D': '♦', 'C': '♣'}
SUIT_NAMES = {'S': 'Пики', 'H': 'Черви', 'D': 'Бубны', 'C': 'Трефы'}
RANK_NAMES = {'6': '6', '7': '7', '8': '8', '9': '9', '10': '10', 'J': 'Валет', 'Q': 'Дама', 'K': 'Король', 'A': 'Туз'}

# Bot personality presets
BOT_NAMES = [
    "🤖 Бот Валера", "🤖 Бот Саня", "🤖 Бот Кеша", "🤖 Бот Гоша", "🤖 Бот Маша"
]

DEFAULT_BOT_MODEL = "gemini-3.8-flash"

ROOMS: Dict[str, 'DurakRoom'] = {}
ROOMS_LOCK = threading.Lock()

def get_card_rank(card: str) -> str:
    return card[:-1]

def get_card_suit(card: str) -> str:
    return card[-1]

def get_card_value(card: str) -> int:
    return RANK_VALUES.get(get_card_rank(card), 0)

def card_display_name(card: str) -> str:
    if not card or len(card) < 2:
        return card
    r = get_card_rank(card)
    s = get_card_suit(card)
    return f"{RANK_NAMES.get(r, r)}{SUIT_SYMBOLS.get(s, s)}"

def can_beat(attack_card: str, defend_card: str, trump_suit: str) -> bool:
    a_suit, d_suit = get_card_suit(attack_card), get_card_suit(defend_card)
    a_val, d_val = get_card_value(attack_card), get_card_value(defend_card)

    if d_suit == a_suit:
        return d_val > a_val
    if d_suit == trump_suit and a_suit != trump_suit:
        return True
    return False

def create_full_deck() -> List[str]:
    deck = [f"{r}{s}" for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


class Player:
    def __init__(self, user_id: str, name: str, is_bot: bool = False, bot_model: str = DEFAULT_BOT_MODEL, avatar: str = None):
        self.id = str(user_id)
        self.name = name
        self.is_bot = is_bot
        self.bot_model = bot_model
        self.avatar = avatar or ""
        self.cards: List[str] = []
        self.has_passed = False
        self.took_cards = False
        self.status = "waiting" # "waiting", "active", "out" (won)
        self.win_place = 0
        self.last_active = time.time()

    def to_dict(self, hide_cards: bool = True) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "is_bot": self.is_bot,
            "bot_model": self.bot_model,
            "avatar": self.avatar,
            "card_count": len(self.cards),
            "cards": self.cards if not hide_cards else [],
            "has_passed": self.has_passed,
            "took_cards": self.took_cards,
            "status": self.status,
            "win_place": self.win_place
        }


class DurakRoom:
    def __init__(self, room_id: str, creator_id: str, creator_name: str, bot_model: str = DEFAULT_BOT_MODEL):
        self.room_id = room_id
        self.creator_id = str(creator_id)
        self.creator_name = creator_name
        self.bot_model = bot_model
        self.state = "waiting" # "waiting", "playing", "finished"
        self.players: List[Player] = []
        self.deck: List[str] = []
        self.trump_card: Optional[str] = None
        self.trump_suit: Optional[str] = None
        self.discard_pile: List[str] = []
        # table: list of dicts: {"attack": "6H", "defense": "10H" or None, "attacker_id": str, "defender_id": str}
        self.table: List[dict] = []
        self.attacker_idx: int = 0
        self.defender_idx: int = 1
        self.winner_order: List[str] = []
        self.loser_id: Optional[str] = None
        self.chat_messages: List[dict] = []
        self.history: List[str] = []
        self.version: int = 1
        self.created_at = time.time()
        self.last_action_time = time.time()
        self.lock = threading.RLock()
        self.bot_timer: Optional[threading.Timer] = None

    def add_player(self, user_id: str, name: str, is_bot: bool = False, bot_model: str = None, avatar: str = None) -> Tuple[bool, str]:
        with self.lock:
            user_id = str(user_id)
            for p in self.players:
                if p.id == user_id:
                    p.name = name
                    p.last_active = time.time()
                    return True, "Уже в комнате"

            if self.state != "waiting":
                return False, "Игра уже началась"
            if len(self.players) >= 6:
                return False, "Комната заполнена (максимум 6 игроков)"

            p = Player(
                user_id=user_id,
                name=name,
                is_bot=is_bot,
                bot_model=bot_model or self.bot_model,
                avatar=avatar
            )
            self.players.append(p)
            self.history.append(f"🟢 {name} присоединился к игре")
            self.version += 1
            return True, "Успешно"

    def remove_player(self, user_id: str) -> Tuple[bool, str]:
        with self.lock:
            user_id = str(user_id)
            idx = next((i for i, p in enumerate(self.players) if p.id == user_id), None)
            if idx is None:
                return False, "Игрок не найден"

            p = self.players[idx]
            if self.state == "waiting":
                self.players.pop(idx)
                self.history.append(f"🔴 {p.name} покинул комнату")
                self.version += 1
                return True, "Удален"
            else:
                # In game: turn player into bot
                p.is_bot = True
                p.name = f"🤖 {p.name} (Авто)"
                self.history.append(f"🤖 {p.name} отключился, за него играет бот")
                self.version += 1
                self.schedule_bot_turn()
                return True, "Заменен ботом"

    def add_bot(self, bot_model: str = None) -> Tuple[bool, str]:
        with self.lock:
            if len(self.players) >= 6:
                return False, "Максимум 6 игроков за столом"
            used_names = [p.name for p in self.players]
            bot_name = next((b for b in BOT_NAMES if b not in used_names), f"🤖 Бот #{len(self.players)+1}")
            bot_id = f"bot_{int(time.time()*1000)%100000}_{random.randint(10,99)}"
            m = bot_model or self.bot_model
            bot_name_full = f"{bot_name}"
            return self.add_player(bot_id, bot_name_full, is_bot=True, bot_model=m)

    def start_game(self) -> Tuple[bool, str]:
        with self.lock:
            if len(self.players) < 2:
                # If solo, automatically add a bot to start playing!
                self.add_bot()

            self.deck = create_full_deck()
            self.discard_pile = []
            self.table = []
            self.winner_order = []
            self.loser_id = None
            self.state = "playing"

            # Deal 6 cards to each player
            for p in self.players:
                p.cards = [self.deck.pop() for _ in range(6)]
                p.status = "active"
                p.has_passed = False
                p.took_cards = False
                p.win_place = 0
                # Sort cards: by suit, then rank
                p.cards.sort(key=lambda c: (c[-1], get_card_value(c)))

            # Trump card is at the bottom of the deck
            self.trump_card = self.deck[0]
            self.trump_suit = get_card_suit(self.trump_card)

            # Determine first attacker: player holding lowest trump
            min_trump_val = 999
            first_idx = 0
            for i, p in enumerate(self.players):
                for c in p.cards:
                    if get_card_suit(c) == self.trump_suit:
                        val = get_card_value(c)
                        if val < min_trump_val:
                            min_trump_val = val
                            first_idx = i

            self.attacker_idx = first_idx
            self.defender_idx = self.get_next_active_idx(first_idx)

            first_p = self.players[self.attacker_idx]
            def_p = self.players[self.defender_idx]
            self.history.append(f"🃏 Игра началась! Козырь: {SUIT_NAMES.get(self.trump_suit, self.trump_suit)} ({card_display_name(self.trump_card)}).")
            self.history.append(f"⚔️ Первый ход делает {first_p.name} против {def_p.name}.")
            self.version += 1

            self.schedule_bot_turn()
            return True, "Игра началась"

    def get_next_active_idx(self, current_idx: int) -> int:
        n = len(self.players)
        for step in range(1, n):
            idx = (current_idx + step) % n
            if self.players[idx].status == "active":
                return idx
        return current_idx

    def get_active_players_count(self) -> int:
        return sum(1 for p in self.players if p.status == "active")

    def play_card(self, user_id: str, card: str, target_attack_idx: Optional[int] = None) -> Tuple[bool, str]:
        with self.lock:
            if self.state != "playing":
                return False, "Игра не в процессе"

            user_id = str(user_id)
            player_idx = next((i for i, p in enumerate(self.players) if p.id == user_id), None)
            if player_idx is None:
                return False, "Игрок не найден"

            player = self.players[player_idx]
            if card not in player.cards:
                return False, "У вас нет этой карты"

            defender = self.players[self.defender_idx]
            attacker = self.players[self.attacker_idx]

            # 1. DEFENSE MOVE
            if player_idx == self.defender_idx:
                if not self.table:
                    return False, "На столе нет атакующих карт для защиты"
                if defender.took_cards:
                    return False, "Вы уже нажали «Беру» и не можете отбиваться"

                # Find which attack card to beat
                target_slot = None
                if target_attack_idx is not None and 0 <= target_attack_idx < len(self.table):
                    if self.table[target_attack_idx]["defense"] is None:
                        target_slot = target_attack_idx
                else:
                    # Find first unbeaten attack card
                    for i, slot in enumerate(self.table):
                        if slot["defense"] is None:
                            if can_beat(slot["attack"], card, self.trump_suit):
                                target_slot = i
                                break

                if target_slot is None:
                    return False, "Эта карта не бьет ни одну карту на столе"

                atk_card = self.table[target_slot]["attack"]
                if not can_beat(atk_card, card, self.trump_suit):
                    return False, f"{card_display_name(card)} не бьет {card_display_name(atk_card)}"

                # Play defense card
                player.cards.remove(card)
                self.table[target_slot]["defense"] = card
                self.table[target_slot]["defender_id"] = user_id
                self.history.append(f"🛡️ {player.name} побил {card_display_name(atk_card)} картой {card_display_name(card)}")

                # Reset passed flag for attackers since defense changed
                for p in self.players:
                    if p.status == "active" and p.id != defender.id:
                        p.has_passed = False

                self.version += 1
                self.check_auto_pass_or_round_end()
                self.schedule_bot_turn()
                return True, "Карта отбита"

            # 2. ATTACK MOVE (Primary Attacker or Tossing)
            else:
                # Defender cannot be attacked with more than their cards
                unbeaten_count = sum(1 for s in self.table if s["defense"] is None)
                if len(self.table) >= 6:
                    return False, "Достигнут максимум карт на столе (6 пар)"
                if unbeaten_count >= len(defender.cards):
                    return False, "У защищающегося игрока недостаточно карт в руке"

                # Check if move is valid
                if not self.table:
                    # Initial attack: only primary attacker can attack
                    if player_idx != self.attacker_idx:
                        return False, f"Сейчас ходит {attacker.name}"
                else:
                    # Tossing: card rank must already be on the table
                    table_ranks = set()
                    for slot in self.table:
                        table_ranks.add(get_card_rank(slot["attack"]))
                        if slot["defense"]:
                            table_ranks.add(get_card_rank(slot["defense"]))

                    if get_card_rank(card) not in table_ranks:
                        return False, "Подкидывать можно только карты того же достоинства, что уже на столе"

                player.cards.remove(card)
                player.has_passed = False
                self.table.append({
                    "attack": card,
                    "defense": None,
                    "attacker_id": user_id,
                    "defender_id": None
                })
                self.history.append(f"⚔️ {player.name} положил {card_display_name(card)}")

                # If defender previously clicked 'take', they will take this new tossed card as well
                self.version += 1
                self.schedule_bot_turn()
                return True, "Карта сыграна"

    def pass_turn(self, user_id: str) -> Tuple[bool, str]:
        """Player clicks 'Бито' (attacker pass)."""
        with self.lock:
            if self.state != "playing":
                return False, "Игра не в процессе"

            user_id = str(user_id)
            p = next((p for p in self.players if p.id == user_id), None)
            if not p:
                return False, "Игрок не найден"
            if p.id == self.players[self.defender_idx].id:
                return False, "Защищающийся игрок не может нажать «Бито»"
            if not self.table:
                return False, "На столе ещё нет карт"

            p.has_passed = True
            self.history.append(f"✋ {p.name}: «Бито»")
            self.version += 1

            self.check_round_resolution()
            self.schedule_bot_turn()
            return True, "Пас принят"

    def take_cards(self, user_id: str) -> Tuple[bool, str]:
        """Defender clicks 'Беру'."""
        with self.lock:
            if self.state != "playing":
                return False, "Игра не в процессе"

            user_id = str(user_id)
            defender = self.players[self.defender_idx]
            if user_id != defender.id:
                return False, "Только защищающийся игрок может взять карты"
            if not self.table:
                return False, "На столе нет карт"

            defender.took_cards = True
            self.history.append(f"📥 {defender.name} берет карты со стола!")
            self.version += 1

            # Give attackers a moment or if all passed, finish round
            self.check_round_resolution()
            self.schedule_bot_turn()
            return True, "Взял карты"

    def check_round_resolution(self):
        """Resolves bout if all attackers passed."""
        defender = self.players[self.defender_idx]
        active_attackers = [
            p for p in self.players
            if p.status == "active" and p.id != defender.id
        ]

        if not active_attackers:
            self.finish_round(defender_took=defender.took_cards)
            return

        all_passed = all(p.has_passed for p in active_attackers)

        if all_passed:
            all_beaten = all(slot["defense"] is not None for slot in self.table)
            if all_beaten and not defender.took_cards:
                self.finish_round(defender_took=False)
            elif defender.took_cards:
                self.finish_round(defender_took=True)

    def check_auto_pass_or_round_end(self):
        """Called after a defense move."""
        pass

    def finish_round(self, defender_took: bool):
        defender = self.players[self.defender_idx]

        if defender_took:
            # Defender takes all cards from table
            taken_cards = []
            for slot in self.table:
                taken_cards.append(slot["attack"])
                if slot["defense"]:
                    taken_cards.append(slot["defense"])
            defender.cards.extend(taken_cards)
            defender.cards.sort(key=lambda c: (c[-1], get_card_value(c)))
            self.history.append(f"📥 {defender.name} забрал {len(taken_cards)} карт.")
        else:
            # Cards go to discard pile
            discarded = []
            for slot in self.table:
                discarded.append(slot["attack"])
                if slot["defense"]:
                    discarded.append(slot["defense"])
            self.discard_pile.extend(discarded)
            self.history.append(f"✨ Бито! ({len(discarded)} карт ушло в сброс).")

        self.table = []

        # Deal cards from deck back up to 6
        # Deal order: primary attacker -> other attackers -> defender
        deal_order = [self.attacker_idx]
        curr = self.get_next_active_idx(self.attacker_idx)
        while curr != self.attacker_idx:
            if curr not in deal_order:
                deal_order.append(curr)
            curr = self.get_next_active_idx(curr)

        # Make sure defender is dealt last
        if self.defender_idx in deal_order:
            deal_order.remove(self.defender_idx)
            deal_order.append(self.defender_idx)

        for p_idx in deal_order:
            p = self.players[p_idx]
            if p.status == "active" and len(p.cards) < 6 and self.deck:
                need = 6 - len(p.cards)
                drawn = []
                for _ in range(need):
                    if self.deck:
                        drawn.append(self.deck.pop())
                p.cards.extend(drawn)
                p.cards.sort(key=lambda c: (c[-1], get_card_value(c)))

        # Check who won (cards == 0 and deck is empty)
        if not self.deck:
            for p in self.players:
                if p.status == "active" and len(p.cards) == 0:
                    p.status = "out"
                    self.winner_order.append(p.id)
                    p.win_place = len(self.winner_order)
                    self.history.append(f"👑 {p.name} вышел из игры! ({p.win_place}-е место)")

        # Reset states for next bout
        for p in self.players:
            p.has_passed = False
            p.took_cards = False

        # Determine next attacker and defender
        active_count = self.get_active_players_count()
        if active_count <= 1:
            self.finish_game()
            return

        if defender_took:
            # Defender skips attack turn. Attacker is player after defender.
            next_atk = self.get_next_active_idx(self.defender_idx)
        else:
            # Defender successfully beat cards: defender becomes next attacker!
            if defender.status == "active":
                next_atk = self.defender_idx
            else:
                next_atk = self.get_next_active_idx(self.defender_idx)

        self.attacker_idx = next_atk
        self.defender_idx = self.get_next_active_idx(next_atk)

        next_atk_p = self.players[self.attacker_idx]
        next_def_p = self.players[self.defender_idx]
        self.history.append(f"⚔️ Новый раунд! Ходит {next_atk_p.name} на {next_def_p.name}.")
        self.version += 1

        self.schedule_bot_turn()

    def finish_game(self):
        self.state = "finished"
        active_remaining = [p for p in self.players if p.status == "active"]
        if active_remaining:
            loser = active_remaining[0]
            self.loser_id = loser.id
            self.history.append(f"🃏 Игра окончена! Дурак партии: {loser.name}! 🤡")
            if loser.is_bot:
                durak_ai.generate_ai_commentary_async(self, loser, "lose")
        else:
            self.history.append("🤝 Ничья! Все игроки вышли одновременно.")
        self.version += 1

    def send_chat_message(self, user_id: str, sender_name: str, text: str):
        with self.lock:
            msg = {
                "id": len(self.chat_messages) + 1,
                "sender_id": str(user_id),
                "sender_name": sender_name,
                "text": text,
                "time": time.strftime("%H:%M:%S")
            }
            self.chat_messages.append(msg)
            if len(self.chat_messages) > 50:
                self.chat_messages.pop(0)
            self.version += 1

    def schedule_bot_turn(self):
        """Schedules bot AI to act after a short human-like delay."""
        if self.state != "playing":
            return

        with self.lock:
            if self.bot_timer and self.bot_timer.is_alive():
                return

            def _bot_worker():
                time.sleep(1.2)
                try:
                    self.execute_bot_action()
                except Exception as e:
                    print(f"⚠️ Ошибка в execute_bot_action: {e}")

            self.bot_timer = threading.Timer(0.1, _bot_worker)
            self.bot_timer.daemon = True
            self.bot_timer.start()

    def execute_bot_action(self):
        """Analyzes state and makes move for whichever bot needs to act."""
        if self.state != "playing":
            return

        with self.lock:
            defender = self.players[self.defender_idx]
            attacker = self.players[self.attacker_idx]

            # 1. Is Defender a Bot?
            if defender.is_bot and defender.status == "active" and not defender.took_cards:
                unbeaten = [i for i, s in enumerate(self.table) if s["defense"] is None]
                if unbeaten:
                    # Try to beat first unbeaten card
                    slot_idx = unbeaten[0]
                    atk_card = self.table[slot_idx]["attack"]
                    valid_moves = [
                        c for c in defender.cards
                        if can_beat(atk_card, c, self.trump_suit)
                    ]
                    if valid_moves:
                        # Pick lowest value beating card (prefer non-trump)
                        non_trumps = [c for c in valid_moves if get_card_suit(c) != self.trump_suit]
                        if non_trumps:
                            best_card = min(non_trumps, key=get_card_value)
                        else:
                            best_card = min(valid_moves, key=get_card_value)

                        self.play_card(defender.id, best_card, target_attack_idx=slot_idx)
                        if get_card_rank(atk_card) == 'A':
                            durak_ai.generate_ai_commentary_async(self, defender, "beat_ace")
                        return
                    else:
                        # Cannot beat: must take cards!
                        self.take_cards(defender.id)
                        durak_ai.generate_ai_commentary_async(self, defender, "take")
                        return

            # 2. Is Attacker a Bot and table is empty?
            if attacker.is_bot and attacker.status == "active" and not self.table:
                # Attack with lowest non-trump card
                non_trumps = [c for c in attacker.cards if get_card_suit(c) != self.trump_suit]
                if non_trumps:
                    best_card = min(non_trumps, key=get_card_value)
                else:
                    best_card = min(attacker.cards, key=get_card_value)

                self.play_card(attacker.id, best_card)
                return

            # 3. Are other Attackers Bots and can toss cards?
            for p in self.players:
                if p.is_bot and p.status == "active" and p.id != defender.id and not p.has_passed:
                    # Check if can toss
                    if self.table:
                        table_ranks = set()
                        for slot in self.table:
                            table_ranks.add(get_card_rank(slot["attack"]))
                            if slot["defense"]:
                                table_ranks.add(get_card_rank(slot["defense"]))

                        tossable = [c for c in p.cards if get_card_rank(c) in table_ranks]
                        unbeaten_cnt = sum(1 for s in self.table if s["defense"] is None)

                        # Only toss if defender can still take cards and table < 6
                        if tossable and len(self.table) < 6 and unbeaten_cnt < len(defender.cards):
                            # Don't toss trumps unnecessarily
                            non_trumps = [c for c in tossable if get_card_suit(c) != self.trump_suit]
                            if non_trumps:
                                best = min(non_trumps, key=get_card_value)
                                self.play_card(p.id, best)
                                durak_ai.generate_ai_commentary_async(self, p, "toss")
                                return
                            elif len(self.deck) == 0: # late game, toss trumps too
                                best = min(tossable, key=get_card_value)
                                self.play_card(p.id, best)
                                durak_ai.generate_ai_commentary_async(self, p, "toss")
                                return

                    # If table has beaten cards or defender took, and bot has nothing to toss -> Pass
                    all_beaten = all(s["defense"] is not None for s in self.table)
                    if self.table and (all_beaten or defender.took_cards):
                        self.pass_turn(p.id)
                        return

    def get_state(self, for_user_id: Optional[str] = None) -> dict:
        with self.lock:
            for_user_id = str(for_user_id) if for_user_id else None
            defender = self.players[self.defender_idx] if self.players else None
            attacker = self.players[self.attacker_idx] if self.players else None

            # Find user player
            user_player = next((p for p in self.players if p.id == for_user_id), None)
            user_cards = user_player.cards if user_player else []

            players_data = []
            for p in self.players:
                is_me = (for_user_id is not None and p.id == for_user_id)
                p_dict = p.to_dict(hide_cards=not is_me)
                p_dict["is_attacker"] = (attacker is not None and p.id == attacker.id)
                p_dict["is_defender"] = (defender is not None and p.id == defender.id)
                players_data.append(p_dict)

            return {
                "room_id": self.room_id,
                "creator_id": self.creator_id,
                "creator_name": self.creator_name,
                "state": self.state,
                "trump_card": self.trump_card,
                "trump_suit": self.trump_suit,
                "deck_count": len(self.deck),
                "discard_count": len(self.discard_pile),
                "table": self.table,
                "attacker_id": attacker.id if attacker else None,
                "defender_id": defender.id if defender else None,
                "players": players_data,
                "my_cards": user_cards,
                "my_id": for_user_id,
                "bot_model": self.bot_model,
                "history": self.history[-15:],
                "chat_messages": self.chat_messages[-25:],
                "version": self.version,
                "loser_id": self.loser_id,
                "winner_order": self.winner_order
            }


# --- ROOM MANAGER ---
def get_or_create_room(room_id: str, creator_id: str = "guest", creator_name: str = "Игрок", bot_model: str = DEFAULT_BOT_MODEL) -> DurakRoom:
    with ROOMS_LOCK:
        if room_id not in ROOMS:
            room = DurakRoom(room_id, creator_id, creator_name, bot_model=bot_model)
            if creator_id:
                room.add_player(creator_id, creator_name)
            ROOMS[room_id] = room
        return ROOMS[room_id]

def get_room(room_id: str) -> Optional[DurakRoom]:
    with ROOMS_LOCK:
        return ROOMS.get(room_id)
