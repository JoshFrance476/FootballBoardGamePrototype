"""The pitch: tiles, the players standing on them, the ball and the goals.

This only holds state. The rules for actions live in actions.py.
"""
from dataclasses import dataclass
from typing import Optional

import config
from hex_math import hex_distance, rectangle_coords
from player import Player


def shift_top():
    """True if config.SHIFTED_ROWS says the top row is one of the shifted
    (half a tile across, one tile shorter) rows."""
    return getattr(config, "SHIFTED_ROWS", "second") == "top"


@dataclass
class Tile:
    q: int
    r: int
    player: Optional[Player] = None

    @property
    def coord(self):
        return (self.q, self.r)


class Board:
    def __init__(self, cols, rows):
        self.tiles = {coord: Tile(*coord) for coord in rectangle_coords(cols, rows, shift_top())}

        # Each goal is the tile in the middle of the left or right edge
        middle_row = sorted(coord for coord in self.tiles if coord[1] == rows // 2)
        self.goals = {"left": middle_row[0], "right": middle_row[-1]}

        self.reset()

    def clone(self):
        """An independent copy: new tiles and players, same layout and ball."""
        copy = Board.__new__(Board)
        copy.tiles = {
            coord: Tile(tile.q, tile.r,
                        None if tile.player is None else
                        Player(tile.player.team, tile.player.actions, tile.player.facing,
                               tile.player.role, tile.player.shield))
            for coord, tile in self.tiles.items()
        }
        copy.goals = self.goals     # never changes, so it can be shared
        copy.ball = self.ball
        return copy

    def reset(self):
        """Put the players and ball back in their kick-off positions."""
        for tile in self.tiles.values():
            tile.player = None
        self.ball = config.INITIAL_BALL
        self.place_players(config.INITIAL_SETUP)

    def place_players(self, setup):
        """Put players on the board from a {team: [{"actions", "position"}, ...]} dict.
        An entry can also give "facing" and "role" ("goalkeeper")."""
        for team, entries in setup.items():
            for entry in entries:
                actions = list(entry["actions"])
                actions += [a for a in config.DEFAULT_ACTIONS if a not in actions]
                facing = entry.get("facing", config.TEAM_FACING[team])
                role = entry.get("role", "outfield")
                self.tiles[entry["position"]].player = Player(team, actions, facing, role)

    # --- Lookup ---

    def __contains__(self, coord):
        return coord in self.tiles

    def __iter__(self):
        return iter(self.tiles.values())

    def get(self, coord):
        """The Tile at coord, or None if it's off the board."""
        return self.tiles.get(coord)

    def has_possession(self, tile):
        """True if the player on this tile has the ball."""
        return tile.player is not None and self.ball == tile.coord

    def goal_attacked_by(self, team):
        """Coordinate of the goal this team shoots at."""
        return self.goals[config.ATTACKING_GOAL[team]]

    def own_goal(self, team):
        """Coordinate of the goal this team defends."""
        other = config.AWAY if team == config.HOME else config.HOME
        return self.goal_attacked_by(other)

    def in_box(self, coord, team):
        """True if coord is in this team's box: within KEEPER_BOX_RADIUS of its goal."""
        return hex_distance(coord, self.own_goal(team)) <= config.KEEPER_BOX_RADIUS

    def keeper_tile(self, team):
        """The tile of this team's goalkeeper, or None if it has none."""
        for tile in self.tiles.values():
            if tile.player is not None and tile.player.team == team and tile.player.is_keeper:
                return tile
        return None

    def goal_side(self, coord):
        """"left" or "right" if coord is a goal tile, otherwise None."""
        for side, goal in self.goals.items():
            if goal == coord:
                return side
        return None

    # --- Changes ---

    def move_player(self, from_tile, to_tile):
        to_tile.player = from_tile.player
        from_tile.player = None
