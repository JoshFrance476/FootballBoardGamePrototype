"""Turns, the action budget, score, selection, and how the game responds to input.

Rules state (whose turn, what's left this turn, score, the board) is
separate from the human player's selection state (selected tile, chosen
action), so the AI can copy a game and play moves on it without any clicking.

A turn:
  - Every player on the team may take one free step to an empty
    neighbouring tile, ending up facing the side (left or right) they stepped
    towards. A player with the ball takes it with them; stepping onto a loose
    ball picks it up.
  - The team has config.ACTIONS_PER_TURN[team] actions (1 by default),
    which any of its players can use.
  - The first time each player gets the ball in a turn, the team earns an
    extra actions, which any of its players can use: holding it at the start
    of the turn earns config.START_POSSESSION_ACTIONS, winning it by moving
    across the carrier config.TACKLE_POSSESSION_ACTIONS, and any other way (receiving a
    pass, picking up a loose ball) config.RECEIVE_POSSESSION_ACTIONS. Getting it back later in the same turn earns nothing more.
  - Steps and actions can happen in any order. Press End turn when done.
  - Tackling: a player who moves (free step or action) from a tile next to
    the opponent with the ball to another tile next to them tackles, with
    config.TACKLE_CHANCE for the side they start on. Won or not, they end up
    where they were going; with the ball if it won.
  - Going past: a player with the ball who moves from a tile next to an
    opponent to another tile next to that opponent gives them a tackle. If it
    wins the ball goes to that opponent and the carrier still makes the move.
    Moving straight away from an opponent is safe.
  - Shielding: an action with "shield" takes that much off the chance of
    being tackled by an opponent moving across the player, until their
    team's next turn starts, or until they move.
  - Passes can be intercepted by opponents next to their path
    (config.INTERCEPT_CHANCE). See actions.py for the details.
After a goal, everyone goes back to kick-off positions and the team that
conceded takes the next turn. Turns always alternate, so a match of
config.TURNS_PER_TEAM turns each is over after twice that many turns.
"""
import random
from typing import NamedTuple, Optional

import config
from actions import (apply_outcome, carry_tackles, moving_tackle, outcomes, percent,
                     preview_outcomes)
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
        self.turn_limit = 2 * config.TURNS_PER_TEAM if config.TURNS_PER_TEAM else None
        self.over = False       # True once the last turn has been played
        self.start_turn(config.HOME)

    def start_turn(self, team):
        if self.turn_limit is not None and self.turn_number >= self.turn_limit:
            self.over = True    # full time: no more turns
            self.selected = None
            self.action = None
            return
        self.active_team = team
        self.turn_number += 1
        for tile in self.board.tiles.values():   # shields last until the team's next turn
            if tile.player is not None and tile.player.team == team:
                tile.player.shield = 0.0
        self.actions_remaining = config.ACTIONS_PER_TURN[team]  # for any player on the team
        self.had_ball = set()       # coords of players who've had the ball this turn
        self.stepped = set()      # coords of players who've taken their free step this turn
        self.last_actions = {}    # tile coord -> name of the action its player last did this turn
        self.selected = None      # the Tile whose player is selected (either team), or None
        self.action = None        # name of the action awaiting a target click, or None
        self.hover_action = None  # name of the action button under the mouse, or None
        # Holding the ball at the start of the turn counts as a first possession
        self._check_new_carrier(config.START_POSSESSION_ACTIONS)

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
        copy.turn_limit = self.turn_limit
        copy.over = self.over
        copy.active_team = self.active_team
        copy.actions_remaining = self.actions_remaining
        copy.had_ball = set(self.had_ball)
        copy.stepped = set(self.stepped)
        copy.last_actions = dict(self.last_actions)
        copy.selected = None
        copy.action = None
        copy.hover_action = None
        return copy

    def snapshot(self):
        """Everything perform() can change, saved cheaply so a trial move can
        be undone with restore(). Much faster than clone() for "try a move,
        look at the result, take it back"."""
        players = [(tile, tile.player, tile.player.facing, tile.player.shield)
                   for tile in self.board.tiles.values() if tile.player is not None]
        return (self.board.ball, self.actions_remaining, frozenset(self.had_ball),
                self.active_team, self.turn_number, self.over, dict(self.score),
                frozenset(self.stepped), dict(self.last_actions), self.message, players)

    def restore(self, snapshot):
        """Put the game back exactly as it was when snapshot() was taken."""
        (ball, actions_remaining, had_ball, team, turn, over, score, stepped, last_actions,
         message, players) = snapshot
        for tile in self.board.tiles.values():
            tile.player = None
        for tile, player, facing, shield in players:
            tile.player = player
            player.facing = facing
            player.shield = shield
        self.board.ball = ball
        self.actions_remaining = actions_remaining
        self.had_ball = set(had_ball)
        self.active_team = team
        self.turn_number = turn
        self.over = over
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

    def turns_left(self):
        """Turns still to be played after this one (None with no limit)."""
        if self.turn_limit is None:
            return None
        return max(0, self.turn_limit - self.turn_number)

    def result_text(self):
        """e.g. "Home win 2 - 1" or "Draw 1 - 1"."""
        home, away = self.score[config.HOME], self.score[config.AWAY]
        if home == away:
            return f"Draw {home} - {away}"
        winner = config.HOME if home > away else config.AWAY
        return f"{config.TEAM_NAMES[winner]} win {home} - {away}"

    def legal_moves(self):
        """Every move the team whose turn it is could make right now. End turn
        is always one, until the match is over (then there are none)."""
        if self.over:
            return []
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

    def _check_new_carrier(self, earns=None):
        """If the ball is now with a player of the team to move who hasn't had
        it yet this turn, earn the team `earns` extra actions (by default
        config.RECEIVE_POSSESSION_ACTIONS)."""
        if earns is None:
            earns = config.RECEIVE_POSSESSION_ACTIONS
        carrier = self.board.get(self.board.ball)
        if (carrier.player is not None and carrier.player.team == self.active_team
                and carrier.coord not in self.had_ball):
            self.had_ball.add(carrier.coord)
            self.actions_remaining += earns

    def _step(self, move):
        tile, destination = self.board.get(move.coord), self.board.get(move.target)
        carrying = self.board.ball == tile.coord
        self.message = ""
        if carrying:
            # Going past opponents: each gets a tackle, in order
            risks = carry_tackles(self.board, tile, destination.coord)
            for coord, chance in risks:
                if self.roll() < chance:
                    self.board.ball = coord
                    carrying = False            # the step still happens, without the ball
                    self.message = f"Tackled going past ({percent(chance)})"
                    break
            else:
                if risks:
                    self.message = "Got past the tackle" + ("s" if len(risks) > 1 else "")
            tackle_won = False
        else:
            # Stepping across the opponent with the ball tackles them
            tackle = moving_tackle(self.board, tile, destination.coord)
            tackle_won = tackle is not None and self.roll() < tackle[1]
            if tackle is not None:
                self.message = f"Tackle {'won' if tackle_won else 'failed'} ({percent(tackle[1])})"
        facing = facing_after_move(tile.coord, destination.coord, tile.player.facing)
        self.board.move_player(tile, destination)
        if carrying or tackle_won:
            self.board.ball = destination.coord
        destination.player.facing = facing
        destination.player.shield = 0.0         # moving ends a shield
        self._player_moved(tile.coord, destination.coord)
        self.stepped.add(destination.coord)
        if tackle_won and destination.coord not in self.had_ball:
            self.had_ball.add(destination.coord)
            self.actions_remaining += config.TACKLE_POSSESSION_ACTIONS
        return destination

    def _act(self, move):
        tile = self.board.get(move.coord)
        outcome = outcomes(self.board, tile, move.action)[move.target]
        self.actions_remaining -= 1
        result = apply_outcome(self.board, tile, outcome, self.roll)
        self._player_moved(move.coord, result.tile.coord)
        self.last_actions[result.tile.coord] = move.action
        self.message = result.message
        if result.tackle_won and result.tile.coord not in self.had_ball:
            # The tackler's first possession this turn. If the same action
            # passes the ball on, the receiver still earns one as usual.
            self.had_ball.add(result.tile.coord)
            self.actions_remaining += config.TACKLE_POSSESSION_ACTIONS
        if result.goal_for is not None:
            self.goal_scored(result.goal_for, outcome.score_chance)
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
        if self.selected is None or self.selected.player is None:
            return []
        return self.selected.player.actions

    @property
    def viewing_opponent(self):
        """True if the selected player is on the other team: their actions can
        be looked at (previewed) but not used."""
        return (self.selected is not None and self.selected.player is not None
                and self.selected.player.team != self.active_team)

    def options_for(self, action_name):
        """{coord: Outcome} for the selected player's action, or {} if it can't
        be used now. For your own player that includes having no action left;
        for an opponent it's what the action could do on their turn from here."""
        if self.selected is None:
            return {}
        if not self.viewing_opponent and not self.can_act(self.selected):
            return {}
        return outcomes(self.board, self.selected, action_name)

    def can_use(self, action_name):
        return bool(self.options_for(action_name))

    @property
    def preview_action(self):
        """The action to show destinations for: the chosen one, otherwise the
        one whose button is under the mouse."""
        if self.action is not None:
            return self.action
        if self.hover_action in self.available_actions:
            return self.hover_action
        return None

    @property
    def preview_options(self):
        """{coord: Outcome} to draw for preview_action (may not be clickable).
        For an action the player can't use right now (e.g. a pass without the
        ball), where it would go if they could."""
        name = self.preview_action
        if name is None:
            return {}
        return self.options_for(name) or preview_outcomes(self.board, self.selected, name)

    @property
    def preview_usable(self):
        """True if preview_action can be used right now (not just shown)."""
        return self.preview_action is not None and self.can_use(self.preview_action)

    @property
    def options(self):
        """{clickable coord: Outcome} for the chosen action."""
        if self.action is None or self.viewing_opponent:
            return {}
        return self.options_for(self.action)

    @property
    def selected_step_targets(self):
        """Tiles the selected player could step to right now (only when no
        action is chosen, and only for your own players)."""
        if self.selected is None or self.action is not None or self.viewing_opponent:
            return []
        return self.step_targets(self.selected)

    @property
    def targets(self):
        """Coordinates to highlight for the chosen action."""
        return list(self.options)

    @property
    def status(self):
        """Text for the status line: the chosen (or hovered) action's chances,
        or the last result."""
        if self.preview_action is not None:
            options = self.preview_options.values()
            parts = []
            tackles = sorted({o.tackle_chance for o in options if o.tackle_from is not None})
            shots = sorted({o.score_chance for o in options if o.shot_at is not None})
            saves = sorted({o.save_chance for o in options if o.shot_at is not None and o.save_chance})
            if tackles:
                parts.append("Tackle " + " / ".join(map(percent, tackles)))
            if shots:
                parts.append("Score " + " / ".join(map(percent, shots)))
            if saves:
                parts.append("keeper saves " + " / ".join(map(percent, saves)))
            losses = sorted({o.loss_chance for o in options if o.loss_chance > 0})
            if losses:
                parts.append("lose the ball " + " / ".join(map(percent, losses)))
            if not self.preview_usable:
                if not self.preview_options:
                    return f"Can't {self.preview_action} from here"
                return (f"Can't {self.preview_action} now: showing where it would go"
                        + (f" ({', '.join(parts)})" if parts else ""))
            if parts:
                return "Chance: " + ", ".join(parts)
        elif self.viewing_opponent:
            return (f"Viewing a {config.TEAM_NAMES[self.selected.player.team]} player: "
                    f"hover over an action to see where it goes")
        elif self.selected is not None and self.selected_step_targets:
            risk = self.step_tackle_note()
            if risk:
                return f"{self.message} - {risk}" if self.message else risk
        return self.message

    def step_tackle_note(self):
        """What stepping the selected player could do with tackles: the risk
        of going past opponents with the ball, or the chance of tackling by
        stepping across the carrier. "" if none of their steps involve one."""
        tile = self.selected
        if self.board.ball == tile.coord:
            losses = []
            for target in self.selected_step_targets:
                keep = 1.0
                for _, chance in carry_tackles(self.board, tile, target):
                    keep *= 1 - chance
                if keep < 1:
                    losses.append(1 - keep)
            if losses:
                worst = max(losses)
                return f"Stepping past an opponent: up to {percent(worst)} chance of being tackled"
            return ""
        chances = sorted({t[1] for t in (moving_tackle(self.board, tile, target)
                                         for target in self.selected_step_targets) if t})
        if chances:
            return "Stepping across the ball carrier tackles: " + " / ".join(map(percent, chances))
        return ""

    # --- Human input ---

    def choose_action(self, action_name):
        """Handle a click on an action button."""
        if self.over or self.viewing_opponent:
            return                      # an opponent's actions can only be looked at
        if action_name == self.action:
            self.action = None          # clicking the chosen action again cancels it
        elif self.can_use(action_name):
            self.action = action_name

    def click(self, tile):
        """Handle a click on a tile. tile is None for clicks off the board."""
        if tile is None or self.over:
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

        # Clicked an opponent: select them to look at their actions
        elif tile.player is not None:
            self.selected = tile
            self.action = None

        # Clicked an empty tile that isn't a target: deselect
        elif tile.player is None:
            self.selected = None
            self.action = None
