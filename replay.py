"""Match recording and replays.

Every match is played on a RecordingGame, which notes the position after
each move (every step, action and End turn). At full time the match is
saved as one JSON file in config.REPLAY_FOLDER. The file stores positions,
not just moves, so an old replay plays back exactly as it happened even
after the rules or code change. It also stores who was playing, the
settings used and any timestamps (a turn plus a note to come back to).

Rewatching rebuilds a Game for any frame (frame_game) for the renderer to
draw. pitch_settings() temporarily puts the recorded pitch size into config
while drawing, so a replay from a different pitch still draws right.
"""
import contextlib
import datetime
import json
import os
import pprint

import config
from board import Board, Tile
from game import END_TURN, STEP, Game
from player import Player

VERSION = 1


# --- Recording ---

def state_of(game):
    """What a frame stores about the position."""
    players = [[tile.q, tile.r, tile.player.team, tile.player.facing,
                tile.player.role, round(tile.player.shield, 4)]
               for tile in game.board.tiles.values() if tile.player is not None]
    return {"players": players, "ball": list(game.board.ball),
            "score": {str(team): goals for team, goals in game.score.items()},
            "team": game.active_team, "turn": game.turn_number,
            "actions": game.actions_remaining, "over": game.over}


def move_record(move):
    if move == END_TURN:
        return {"action": "end turn"}
    return {"action": move.action, "from": list(move.coord),
            "to": None if move.target is None else list(move.target)}


class RecordingGame(Game):
    """A Game that keeps a frame for every move: self.frames. Trial moves the
    AI plays and takes back (snapshot / restore) leave no frames behind.
    Copies made with clone() are plain Games, so the AI's planning isn't
    recorded."""

    def __init__(self, board, roll=None):
        if roll is None:
            super().__init__(board)
        else:
            super().__init__(board, roll)
        self.frames = [{"move": None, "message": "Kick-off", "state": state_of(self)}]

    def _record(self, move, team):
        self.frames.append({"move": move_record(move), "by": team,
                            "message": self.message, "state": state_of(self)})

    def perform(self, move):
        if move == END_TURN:
            self.end_turn()                     # end_turn records it
            return None
        team = self.active_team
        result = super().perform(move)
        self._record(move, team)
        return result

    def end_turn(self):
        team = self.active_team
        super().end_turn()
        self._record(END_TURN, team)

    def snapshot(self):
        return super().snapshot(), len(self.frames)

    def restore(self, snapshot):
        state, frames = snapshot
        super().restore(state)
        del self.frames[frames:]


def settings_text():
    """{setting name: its value as text} for the settings the menu edits."""
    import settings
    return {name: pprint.pformat(getattr(config, name), width=76, sort_dicts=False)
            for name in settings.EDITABLE if hasattr(config, name)}


def new_record(game, controllers, ranked=None):
    """The replay for a finished RecordingGame. controllers: {team: "human"/"ai"};
    ranked: the ranked opponent's name, if it was a ranked match."""
    tiles = sorted(game.board.tiles)
    now = datetime.datetime.now()
    return {
        "version": VERSION,
        "saved": now.isoformat(timespec="seconds"),
        "title": "",
        "teams": {str(config.HOME): config.TEAM_NAMES[config.HOME],
                  str(config.AWAY): config.TEAM_NAMES[config.AWAY]},
        "home": config.HOME, "away": config.AWAY,
        "controllers": {str(team): kind for team, kind in controllers.items()},
        "ranked": ranked,
        "pitch": {"cols": config.BOARD_COLS, "rows": config.BOARD_ROWS,
                  "shifted_rows": config.SHIFTED_ROWS, "box_radius": config.KEEPER_BOX_RADIUS,
                  "tiles": [list(c) for c in tiles],
                  "goals": {side: list(c) for side, c in game.board.goals.items()},
                  "turn_limit": game.turn_limit},
        "settings": settings_text(),
        "frames": game.frames,
        "timestamps": [],
    }


# --- Files ---

def folder():
    path = config.REPLAY_FOLDER
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    return path


def save(record):
    """Write a replay to a new file in the replay folder; returns its path."""
    os.makedirs(folder(), exist_ok=True)
    stamp = record["saved"].replace(":", "").replace("-", "")
    path = os.path.join(folder(), f"match_{stamp}.json")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder(), f"match_{stamp}_{n}.json")
        n += 1
    write(path, record)
    return path


def write(path, record):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(record, f, separators=(",", ":"))
    os.replace(temp, path)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def delete(path):
    os.remove(path)


def list_replays():
    """[(path, record)] for every readable replay, newest first."""
    if not os.path.isdir(folder()):
        return []
    found = []
    for name in os.listdir(folder()):
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder(), name)
        try:
            record = load(path)
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and "frames" in record:
            found.append((path, record))
    found.sort(key=lambda item: item[1].get("saved", ""), reverse=True)
    return found


# --- Describing a replay ---

def team_name(record, team):
    return record["teams"].get(str(team), str(team))


def final_score(record):
    state = record["frames"][-1]["state"]
    return state["score"][str(record["home"])], state["score"][str(record["away"])]


def who_played(record):
    """e.g. "Home: human, Away: AI (ranked vs Team 37)"."""
    parts = []
    for team in (record["home"], record["away"]):
        kind = record["controllers"].get(str(team), "?")
        parts.append(f"{team_name(record, team)}: {'AI' if kind == 'ai' else kind}")
    text = ", ".join(parts)
    if record.get("ranked"):
        text += f" (ranked vs {record['ranked']})"
    return text


def headline(record):
    """Title (or date) and the result."""
    home, away = final_score(record)
    when = record.get("saved", "").replace("T", " ")[:16]
    title = record.get("title") or when
    return (f"{title}   {team_name(record, record['home'])} {home} - {away} "
            f"{team_name(record, record['away'])}")


def frame_turn(record, index):
    """The turn shown at a frame (1 is the first turn)."""
    return record["frames"][index]["state"]["turn"]


def turn_starts(record):
    """Frame indexes where each turn starts (after the previous End turn)."""
    starts = [0]
    for i, frame in enumerate(record["frames"]):
        if i and frame["move"] and frame["move"]["action"] == "end turn":
            starts.append(i)
    return starts


def describe_move(record, index):
    """What the move into this frame was, for the status line."""
    frame = record["frames"][index]
    move = frame["move"]
    if move is None:
        return frame.get("message") or "Kick-off"
    who = team_name(record, frame.get("by"))
    action = move["action"]
    text = f"{who}: end turn" if action == "end turn" else f"{who}: {action}"
    if frame.get("message"):
        text += f" - {frame['message']}"
    return text


def add_timestamp(record, index, note):
    record["timestamps"].append({"frame": index, "turn": frame_turn(record, index), "note": note})
    record["timestamps"].sort(key=lambda t: t["frame"])


# --- Rebuilding positions ---

def frame_game(record, index):
    """A Game showing the position at frame `index`, for drawing."""
    pitch = record["pitch"]
    board = Board.__new__(Board)
    board.tiles = {tuple(c): Tile(*c) for c in pitch["tiles"]}
    board.goals = {side: tuple(c) for side, c in pitch["goals"].items()}
    state = record["frames"][index]["state"]
    for q, r, team, facing, role, shield in state["players"]:
        board.tiles[(q, r)].player = Player(team, [], facing, role, shield)
    board.ball = tuple(state["ball"])
    game = Game.__new__(Game)
    game.board = board
    game.roll = None
    game.score = {int(team): goals for team, goals in state["score"].items()}
    game.message = record["frames"][index].get("message", "")
    game.turn_number = state["turn"]
    game.turn_limit = pitch.get("turn_limit")
    game.over = state["over"]
    game.active_team = state["team"]
    game.actions_remaining = state["actions"]
    game.had_ball, game.stepped, game.last_actions = set(), set(), {}
    game.selected = game.action = game.hover_action = None
    return game


@contextlib.contextmanager
def pitch_settings(record):
    """Put the replay's pitch size, row shift, box size and team names into
    config while drawing it, and back afterwards."""
    pitch = record["pitch"]
    names = {int(team): name for team, name in record["teams"].items()}
    changes = {"BOARD_COLS": pitch["cols"], "BOARD_ROWS": pitch["rows"],
               "SHIFTED_ROWS": pitch["shifted_rows"], "KEEPER_BOX_RADIUS": pitch["box_radius"],
               "TEAM_NAMES": {**config.TEAM_NAMES, **names}}
    saved = {name: getattr(config, name) for name in changes}
    try:
        for name, value in changes.items():
            setattr(config, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(config, name, value)
