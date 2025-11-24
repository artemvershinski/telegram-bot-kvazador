import random
import string
from typing import Dict, List

class LiarsBarGame:
    def __init__(self, game_id: str, chat_id: int):
        self.game_id = game_id
        self.chat_id = chat_id
        self.players = []
        self.game_state = "waiting"
        self.theme = None
        self.table_cards = []
        self.current_player_index = 0
        self.player_hands = {}
        self.player_revolvers = {}
        self.deck = []
        
        self.create_deck()
    
    def create_deck(self):
        self.deck = []
        self.deck.extend(['queen'] * 6)
        self.deck.extend(['king'] * 6)
        self.deck.extend(['ace'] * 6)
        self.deck.extend(['joker'] * 2)
        random.shuffle(self.deck)
    
    def add_player(self, player_id: int):
        if player_id not in self.players:
            self.players.append(player_id)
            return True
        return False
    
    def start_game(self):
        if len(self.players) < 4:
            return False, "Недостаточно игроков"
        
        self.game_state = "playing"
        
        for player_id in self.players:
            self.player_revolvers[player_id] = {
                'chamber': random.randint(0, 5),
                'current_position': 0
            }
        
        self.theme = random.choice(['queen', 'king', 'ace'])
        self.deal_cards()
        
        return True, "Игра началась"
    
    def deal_cards(self):
        random.shuffle(self.deck)
        cards_per_player = 5
        
        for i, player_id in enumerate(self.players):
            start_index = i * cards_per_player
            end_index = start_index + cards_per_player
            self.player_hands[player_id] = self.deck[start_index:end_index]
    
    def play_cards(self, player_id: int, card_count: int):
        if self.players[self.current_player_index] != player_id:
            return False, "Не ваш ход"
        
        if card_count < 1 or card_count > 5:
            return False, "Можно положить от 1 до 5 карт"
        
        self.table_cards.append({
            'player_id': player_id,
            'card_count': card_count,
            'actual_cards': []
        })
        
        self.current_player_index = (self.current_player_index + 1) % len(self.players)
        
        return True, "Карты положены на стол"
    
    def challenge_previous_player(self, player_id: int):
        if not self.table_cards:
            return False, "Нечего проверять"
        
        previous_move = self.table_cards[-1]
        previous_player_id = previous_move['player_id']
        
        has_theme_cards = self.check_player_has_theme_cards(previous_player_id)
        
        if has_theme_cards:
            shooter_id = player_id
        else:
            shooter_id = previous_player_id
        
        result = self.fire_revolver(shooter_id)
        self.table_cards = []
        
        return True, {
            'shooter': shooter_id,
            'survived': result
        }
    
    def check_player_has_theme_cards(self, player_id: int):
        hand = self.player_hands.get(player_id, [])
        return any(card in [self.theme, 'joker'] for card in hand)
    
    def fire_revolver(self, player_id: int):
        revolver = self.player_revolvers[player_id]
        
        if revolver['current_position'] == revolver['chamber']:
            self.players.remove(player_id)
            return False
        else:
            revolver['current_position'] = (revolver['current_position'] + 1) % 6
            return True
    
    def get_current_player(self):
        return self.players[self.current_player_index]
    
    def to_dict(self):
        return {
            'game_id': self.game_id,
            'chat_id': self.chat_id,
            'players': self.players,
            'game_state': self.game_state,
            'theme': self.theme,
            'table_cards': self.table_cards,
            'current_player_index': self.current_player_index,
            'player_hands': self.player_hands,
            'player_revolvers': self.player_revolvers,
            'deck': self.deck
        }
    
    @classmethod
    def from_dict(cls, data):
        game = cls(data['game_id'], data['chat_id'])
        game.players = data['players']
        game.game_state = data['game_state']
        game.theme = data['theme']
        game.table_cards = data['table_cards']
        game.current_player_index = data['current_player_index']
        game.player_hands = data['player_hands']
        game.player_revolvers = data['player_revolvers']
        game.deck = data['deck']
        return game
