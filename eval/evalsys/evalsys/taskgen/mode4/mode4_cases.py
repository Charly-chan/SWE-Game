
from ...frozen_data import load_manifest


def active_mode4_cases(game_id=None):
    games = load_manifest()['games']
    return [dict(game_id=name, case_id=case)
            for name, game in games.items() if game_id is None or name == game_id
            for case in game['cases']]
