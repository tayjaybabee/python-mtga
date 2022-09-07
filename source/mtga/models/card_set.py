import logging
import re
from .card import GameCard, Card


class Set(object):

    def __init__(self, set_name, cards=None):
        self.set_name = set_name
        self.mtga_ids = set()
        self.cards_in_set = []

        if cards:
            for card in cards:
                self.add_card(card)

    def add_card(self, card):
        if card.mtga_id in self.mtga_ids:
            raise ValueError(f"This set already has MTGA ID {card.mtga_id}")
        self.cards_in_set.append(card)
        self.mtga_ids.add(card.mtga_id)


class Pool(object):

    def __init__(self, pool_name, cards=None, abilities=None):
        self.pool_name = pool_name
        if cards is None:
            cards = []
        if abilities is None:
            abilities = {}
        self.abilities = abilities
        self.cards = cards
        self.lookup = {**abilities}
        for card in cards:
            self.lookup[card.mtga_id] = card

    def __repr__(self):
        return f"<Pool {self.pool_name}: {len(self.cards)} cards>"

    def __hash__(self):
        return sum(
            hash(element.name + str(idx)) for idx, element in enumerate(self.cards)
        )

    @property
    def total_count(self):
        return len(self.cards)

    def count_cards_owned_by(self, seat):
        return sum(card.owner_seat_id == seat for card in self.cards)

    def count(self, mtga_id):
        return len([card for card in self.cards if card.mtga_id == mtga_id])

    def group_cards(self):
        grouped = {}
        for card in self.cards:
            if card not in grouped:
                grouped[card] = 0
            grouped[card] += 1
        return grouped

    def transfer_all_to(self, other_pool):
        for card in self.cards:
            other_pool.cards.append(card)
        while self.cards:
            self.cards.pop()

    def transfer_cards_to(self, cards, other_pool):
        # TODO: make this "session safe" (ie if we error on the third card, we should not have transferred the first 2)
        for card in cards:
            self.transfer_card_to(card, other_pool)

    def transfer_card_to(self, card, other_pool):
        # TODO: make this atomic, somehow?
        res = card
        if not isinstance(card, Card):  # allow you to pass in cards or ids or searches
            res = self.find_one(card)
        self.cards.remove(res)
        other_pool.cards.append(res)

    @classmethod
    def from_sets(cls, pool_name, sets, abilities=None):
        if abilities is None:
            abilities = {}
        cards = []
        for set in sets:
            cards.extend(iter(set.cards_in_set))
        return Pool(pool_name, cards, abilities)

    def find_one(self, id_or_keyword):
        result = set(self.search(id_or_keyword))
        if not result:
            raise ValueError(f"Pool does not contain {id_or_keyword}")
        elif len(result) > 1:
            raise ValueError(
                f"Pool search '{id_or_keyword}' not narrow enough, got: {result}"
            )

        return result.pop()

    def search(self, id_or_keyword, direct_match_returns_single=False):
        keyword_as_int = None
        keyword_as_str = str(id_or_keyword)
        try:
            keyword_as_int = int(id_or_keyword)
            # why is this check here??
            if keyword_as_int < 10000 and keyword_as_int not in self.lookup.keys():
                keyword_as_int = None
        except (ValueError, TypeError):
            pass
        if keyword_as_int and keyword_as_int in self.lookup.keys():
            # we can skip the loop and return in O(1)
            return [self.lookup[keyword_as_int]]
        results = []
        for card in self.cards:
            if keyword_as_int in [card.mtga_id, card.set_number]:
                return [card]

            keyword_clean = re.sub('[^0-9a-zA-Z_]', '', keyword_as_str.lower())
            if keyword_clean == card.name and direct_match_returns_single:
                return [card]
            if keyword_clean in card.name:
                results.append(card)
        return results


class Zone(Pool):
    def __init__(self, pool_name, zone_id=-1):
        super().__init__(pool_name)
        self.zone_id = zone_id

    def match_game_id_to_card(self, instance_id, card_id):
        for card in self.cards:
            assert isinstance(card, GameCard)
            if card.game_id == instance_id or instance_id in card.previous_iids:
                if card.mtga_id not in [-1, card_id]:
                    raise Exception(
                        f"WHOA. tried to match iid {str(instance_id)} to {str(card_id)}, but already has card {str(card.mtga_id)}"
                    )

                card.transform_to(card_id)
            elif card.mtga_id == card_id:
                # only allowed to set it if it's still -1 (should probably never hit this!)
                if card.game_id == -1:
                    logging.getLogger("mtga").error("What the hell?! How'd we get a card ID without an instance ID?")
                    card.game_id = instance_id


class Deck(Pool):

    def __init__(self, pool_name, deck_id):
        super().__init__(pool_name)
        self.deck_id = deck_id

    def generate_library(self, owner_id=-1):
        library = Library(self.pool_name, self.deck_id, owner_id, -1)
        for card in self.cards:
            game_card = GameCard(card.name, card.pretty_name, card.cost, card.color_identity, card.card_type,
                                       card.sub_types, card.set, card.set_number, card.mtga_id, owner_id, -1)
            library.cards.append(game_card)
        return library

    def to_serializable(self, transform_to_counted=False):
        obj = {
            "deck_id": self.deck_id,
            "pool_name": self.pool_name,
        }
        if transform_to_counted:
            card_dict = {}
            for card in self.cards:
                card_dict[card.mtga_id] = card_dict.get(card.mtga_id, card.to_serializable())
                card_dict[card.mtga_id]["count_in_deck"] = card_dict[card.mtga_id].get("count_in_deck", 0) + 1
            obj["cards"] = list(card_dict.values())
            obj["cards"].sort(key=lambda x: x["count_in_deck"])
            obj["cards"].reverse()
            obj["cards"] = obj["cards"]
        else:
            obj["cards"] = [c.to_serializable() for c in self.cards]
        return obj

    def to_min_json(self):
        min_deck = {}
        for card in self.cards:
            if card.mtga_id not in min_deck:
                min_deck[card.mtga_id] = 0
            min_deck[card.mtga_id] += 1
        return {"deckID": self.deck_id, "poolName": self.pool_name, "cards": min_deck}

    @classmethod
    def from_dict(cls, obj):
        deck = Deck(obj["pool_name"], obj["deck_id"])
        for card in obj["cards"]:
            deck.cards.append(Card.from_dict(card))
        return deck


class Library(Deck, Zone):
    def __init__(self, pool_name, deck_id, owner_seat_id, zone_id=-1):
        super().__init__(pool_name, deck_id)
        self.owner_seat_id = owner_seat_id
        self.zone_id = zone_id

    def set_seat_id(self, seat_id):
        self.owner_seat_id = seat_id
        for card in self.cards:
            assert isinstance(card, GameCard)
            card.owner_seat_id = seat_id
