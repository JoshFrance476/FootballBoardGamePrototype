"""Turns, action points, score, selection, and how the game responds to input.

Rules state (whose turn, action points, score, the board) is separate from
the human player's selection state (selected tile, chosen action), so the
AI can copy a game and play moves on it without any clicking.

A turn goes: click one of your players, click one of their action buttons,
then click a highlighted tile to carry it out. Repeat with any of your
players until you run out of action points or press End turn.
After a goal, everyone goes back to kick-off positions and the team that
conceded takes the next turn.
"""
import random
from typing import NamedTuple, Optional

import config
from actions import apply_outcome, outcomes, percent


def other_team(team):
    return config.AWAY if team == config.HOME else config.HOME


class Move(NamedTuple):
    """One complete move: a player, one of their actions, and the clicked target."""
    coord: Optional[tuple]    # the acting player's tile, None for End turn
    action: str               # action name, or "end turn"
    target: Optional[tuple]   # the tile clicked to carry it out, None for End turn


END_TURN = Move(None, "end turn", None)


class Game:
    def __init__(self, board, roll=random.random):
        self.board = board
        self.roll = roll        # random number source for tackles and shots
        self.score = {config.HOME: 0, config.AWAY: 0}
        self.message = ""       # what happened last, shown in the status line
        self.turn_number = 0    # goes up by one every time the turn changes hands
        self.start_turn(config.HOME)

    def start_turn(self, team):
        self.active_team = team
        self.action_points = config.ACTION_POINTS_PER_TURN
        self.turn_number += 1
        self.last_actions = {}   # tile coord -> name of the action its player last did this turn
        self.selected = None   # the Tile whose player is selected, or None
        self.action = None     # name of the action awaiting a target click, or None

    def clone(self, roll=None):
        """An independent copy for the AI to experiment on.

        Selection state and the message aren't copied. roll replaces the
        random number source if given.
        """
        copy = Game.__new__(Game)
        copy.board = self.board.clone()
        copy.roll = roll if roll is not None else self.roll
        copy.score = dict(self.score)
        copy.message = ""
        copy.turn_number = self.turn_number
        copy.active_team = self.active_team
        copy.action_points = self.action_points
        copy.last_actions = dict(self.last_actions)
        copy.selected = None
        copy.action = None
        return copy

    def snapshot(self):
        """Everything perform() can change, saved cheaply so a trial move can
        be undone with restore(). Much faster than clone() for "try a move,
        look at the result, take it back"."""
        players = [(tile, tile.player, tile.player.facing)
                   for tile in self.board.tiles.values() if tile.player is not None]
        return (self.board.ball, self.action_points, self.active_team, self.turn_number,
                dict(self.score), dict(self.last_actions), self.message, players)

    def restore(self, snapshot):
        """Put the game back exactly as it was when snapshot() was taken."""
        ball, ap, team, turn, score, last_actions, message, players = snapshot
        for tile in self.board.tiles.values():
            tile.player = None
        for tile, player, facing in players:
            tile.player = player
            player.facing = facing
        self.board.ball = ball
        self.action_points = ap
        self.active_team = team
        self.turn_number = turn
        self.score = dict(score)                # copies: the snapshot may be restored again
        self.last_actions = dict(last_actions)
        self.message = message
        self.selected = None
        self.action = None

    # --- Rules: moves without the mouse ---

    def affordable(self, action_name):
        return config.ACTIONS[action_name]["cost"] <= self.action_points

    def own_tiles(self):
        """Tiles holding a player from the team whose turn it is."""
        return [tile for tile in self.board
                if tile.player is not None and tile.player.team == self.active_team]

    def legal_moves(self):
        """Every move the team whose turn it is could make right now. End turn is always one."""
        moves = [END_TURN]
        for tile in self.own_tiles():
            for name in tile.player.actions:
                if self.affordable(name):
                    for target in outcomes(self.board, tile, name):
                        moves.append(Move(tile.coord, name, target))
        return moves

    def perform(self, move):
        """Carry out a legal move. Returns the actions.Result, or None for End turn."""
        if move == END_TURN:
            self.end_turn()
            return None
        tile = self.board.get(move.coord)
        outcome = outcomes(self.board, tile, move.action)[move.target]
        self.action_points -= config.ACTIONS[move.action]["cost"]
        result = apply_outcome(self.board, tile, outcome, self.roll)
        self.last_actions.pop(move.coord, None)
        self.last_actions[result.tile.coord] = move.action
        self.message = result.message
        if result.goal_for is not None:
            self.goal_scored(result.goal_for, outcome.shot_chance)
        return result

    def goal_scored(self, team, chance):
        """Count the goal, reset to kick-off, and give the next turn to the team that conceded."""
        self.score[team] += 1
        self.board.reset()
        self.start_turn(other_team(team))
        self.message = f"Goal! {config.TEAM_NAMES[team]} scored ({percent(chance)} chance)"

    def end_turn(self):
        """Hand over to the other team with a fresh set of action points."""
        self.start_turn(other_team(self.active_team))
        self.message = ""

    # --- State for drawing ---

    @property
    def available_actions(self):
        """Names of the selected player's actions, in button order."""
        if self.selected is None:
            return []
        return self.selected.player.actions

    def options_for(self, action_name):
        """{clickable coord: Outcome} for the selected player, or {} if they
        can't use the action right now (including not enough action points)."""
        if self.selected is None or not self.affordable(action_name):
            return {}
        return outcomes(self.board, self.selected, action_name)

    def can_use(self, action_name):
        return bool(self.options_for(action_name))

    @property
    def options(self):
        """{clickable coord: Outcome} for the chosen action."""
        if self.action is None:
            return {}
        return self.options_for(self.action)

    @property
    def targets(self):
        """Coordinates to highlight for the chosen action."""
        return list(self.options)

    @property
    def status(self):
        """Text for the status line: the chosen action's chances, or the last result."""
        if self.action is not None:
            options = self.options.values()
            parts = []
            tackles = sorted({o.tackle_chance for o in options if o.tackle_from is not None})
            shots = sorted({o.shot_chance for o in options if o.shot_at is not None})
            if tackles:
                parts.append("Tackle " + " / ".join(map(percent, tackles)))
            if shots:
                parts.append("Score " + " / ".join(map(percent, shots)))
            if parts:
                return "Chance: " + ", ".join(parts)
        return self.message

    # --- Human input ---

    def choose_action(self, action_name):
        """Handle a click on an action button."""
        if action_name == self.action:
            self.action = None          # clicking the chosen action again cancels it
        elif self.can_use(action_name):
            self.action = action_name

    def click(self, tile):
        """Handle a click on a tile. tile is None for clicks off the board."""
        if tile is None:
            return

        # Clicked a highlighted tile: carry out the action.
        # Checked first, because a target can be another player's tile.
        if tile.coord in self.options:
            result = self.perform(Move(self.selected.coord, self.action, tile.coord))
            if result.goal_for is None:     # after a goal, start_turn has cleared the selection
                self.selected = result.tile
                self.action = None

        # Clicked one of your own players: select them
        elif tile.player is not None and tile.player.team == self.active_team:
            self.selected = tile
            self.action = None

        # Clicked an empty tile that isn't a target: deselect
        elif tile.player is None:
            self.selected = None
            self.action = None
