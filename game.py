"""Turns, the action budget, score, selection, and how the game responds to input.

Rules state (whose turn, what's left this turn, score, the board) is
separate from the human player's selection state (selected tile, chosen
action), so the AI can copy a game and play moves on it without any clicking.

A turn:
  - Every player on the team may take one free step to an empty
    neighbouring tile, ending up facing the side (left or right) they stepped
    towards. A player with the ball takes it with them; stepping onto a loose
    ball picks it up.
  - The team has one action, which any of its players can use.
  - The first time each player gets the ball in a turn, the team earns an
    extra action, which any of its players can use. Holding it at the start
    of the turn counts, and so do receiving a pass, a tackle, or picking up a
    loose ball. Getting it back later in the same turn earns nothing more.
  - Steps and actions can happen in any order. Press End turn when done.
After a goal, everyone goes back to kick-off positions and the team that
conceded takes the next turn.
"""
import random
from typing import NamedTuple, Optional

import config
from actions import apply_outcome, outcomes, percent
from hex_math import facing_after_move, neighbours


def other_team(team):
    return config.AWAY if team == config.HOME else config.HOME


class Move(NamedTuple):
    """One complete move: a player, what they do, and the clicked target."""
    coord: Optional[tuple]    # the acting player's tile, None for End turn
    action: str               # an action name, "step", or "end turn"
    target: Optional[tuple]   # the tile clicked to carry it out, None for End turn


STEP = "step"
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
        self.turn_number += 1
        self.actions_remaining = 1  # actions any player on the team can use this turn
        self.had_ball = set()       # coords of players who've had the ball this turn
        self.stepped = set()      # coords of players who've taken their free step this turn
        self.last_actions = {}    # tile coord -> name of the action its player last did this turn
        self.selected = None      # the Tile whose player is selected, or None
        self.action = None        # name of the action awaiting a target click, or None
        self._check_new_carrier()  # holding the ball at the start of the turn counts

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
        copy.actions_remaining = self.actions_remaining
        copy.had_ball = set(self.had_ball)
        copy.stepped = set(self.stepped)
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
        return (self.board.ball, self.actions_remaining, frozenset(self.had_ball),
                self.active_team, self.turn_number, dict(self.score), frozenset(self.stepped),
                dict(self.last_actions), self.message, players)

    def restore(self, snapshot):
        """Put the game back exactly as it was when snapshot() was taken."""
        (ball, actions_remaining, had_ball, team, turn, score, stepped, last_actions,
         message, players) = snapshot
        for tile in self.board.tiles.values():
            tile.player = None
        for tile, player, facing in players:
            tile.player = player
            player.facing = facing
        self.board.ball = ball
        self.actions_remaining = actions_remaining
        self.had_ball = set(had_ball)
        self.active_team = team
        self.turn_number = turn
        self.score = dict(score)                # copies: the snapshot may be restored again
        self.stepped = set(stepped)
        self.last_actions = dict(last_actions)
        self.message = message
        self.selected = None
        self.action = None

    # --- Rules: what's allowed ---

    def has_ball(self, team):
        carrier = self.board.get(self.board.ball).player
        return carrier is not None and carrier.team == team

    def can_act(self, tile):
        """True if the team has an action left (any player can use it)."""
        return self.actions_remaining > 0

    def can_step(self, tile):
        return tile.coord not in self.stepped

    def step_targets(self, tile):
        """Empty neighbouring tiles this player could step to (none if they've stepped)."""
        if not self.can_step(tile):
            return []
        return [coord for coord in neighbours(tile.coord)
                if coord in self.board and self.board.get(coord).player is None]

    def own_tiles(self):
        """Tiles holding a player from the team whose turn it is."""
        return [tile for tile in self.board.tiles.values()
                if tile.player is not None and tile.player.team == self.active_team]

    def actions_left(self):
        """How many actions the team can still take this turn."""
        return self.actions_remaining

    def steps_left(self):
        return sum(1 for tile in self.own_tiles() if self.can_step(tile))

    def legal_moves(self):
        """Every move the team whose turn it is could make right now. End turn is always one."""
        moves = [END_TURN]
        for tile in self.own_tiles():
            for coord in self.step_targets(tile):
                moves.append(Move(tile.coord, STEP, coord))
            if self.can_act(tile):
                for name in tile.player.actions:
                    for target in outcomes(self.board, tile, name):
                        moves.append(Move(tile.coord, name, target))
        return moves

    # --- Rules: making moves ---

    def perform(self, move):
        """Carry out a legal move. Returns the actions.Result for an action,
        the Tile the player ends on for a step, or None for End turn."""
        if move == END_TURN:
            self.end_turn()
            return None
        if move.action == STEP:
            result = self._step(move)
        else:
            result = self._act(move)
            if result.goal_for is not None:
                return result
        self._check_new_carrier()
        return result

    def _check_new_carrier(self):
        """If the ball is now with a player of the team to move who hasn't had
        it yet this turn, earn the team an extra action."""
        carrier = self.board.get(self.board.ball)
        if (carrier.player is not None and carrier.player.team == self.active_team
                and carrier.coord not in self.had_ball):
            self.had_ball.add(carrier.coord)
            self.actions_remaining += 1

    def _step(self, move):
        tile, destination = self.board.get(move.coord), self.board.get(move.target)
        carrying = self.board.ball == tile.coord
        facing = facing_after_move(tile.coord, destination.coord, tile.player.facing)
        self.board.move_player(tile, destination)
        if carrying:
            self.board.ball = destination.coord
        destination.player.facing = facing
        self._player_moved(tile.coord, destination.coord)
        self.stepped.add(destination.coord)
        self.message = ""
        return destination

    def _act(self, move):
        tile = self.board.get(move.coord)
        outcome = outcomes(self.board, tile, move.action)[move.target]
        self.actions_remaining -= 1
        result = apply_outcome(self.board, tile, outcome, self.roll)
        self._player_moved(move.coord, result.tile.coord)
        self.last_actions[result.tile.coord] = move.action
        self.message = result.message
        if result.goal_for is not None:
            self.goal_scored(result.goal_for, outcome.shot_chance)
        return result

    def _player_moved(self, old, new):
        """Keep this turn's per-player records with a player who moved."""
        if old == new:
            return
        for record in (self.stepped, self.had_ball):
            if old in record:
                record.discard(old)
                record.add(new)
        if old in self.last_actions:
            self.last_actions[new] = self.last_actions.pop(old)

    def goal_scored(self, team, chance):
        """Count the goal, reset to kick-off, and give the next turn to the team that conceded."""
        self.score[team] += 1
        self.board.reset()
        self.start_turn(other_team(team))
        self.message = f"Goal! {config.TEAM_NAMES[team]} scored ({percent(chance)} chance)"

    def end_turn(self):
        """Hand over to the other team."""
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
        can't use the action right now (including having no action left)."""
        if self.selected is None or not self.can_act(self.selected):
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
    def selected_step_targets(self):
        """Tiles the selected player could step to right now (only when no
        action is chosen)."""
        if self.selected is None or self.action is not None:
            return []
        return self.step_targets(self.selected)

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

        # Clicked a tile the selected player can step to: take the free step
        elif tile.coord in self.selected_step_targets:
            self.selected = self.perform(Move(self.selected.coord, STEP, tile.coord))

        # Clicked one of your own players: select them
        elif tile.player is not None and tile.player.team == self.active_team:
            self.selected = tile
            self.action = None

        # Clicked an empty tile that isn't a target: deselect
        elif tile.player is None:
            self.selected = None
            self.action = None
