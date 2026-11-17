"""The pitch: tiles, the players standing on them, the ball and the goals.

This only holds state. The rules for actions live in actions.py.
"""
from dataclasses import dataclass
from typing import Optional

import config
from hex_math import rectangle_coords
from player import Player


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
        self.tiles = {coord: Tile(*coord) for coord in rectangle_coords(cols, rows)}

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
                        Player(tile.player.team, tile.player.actions, tile.player.facing))
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
        """Put players on the board from a {team: [{"actions", "position"}, ...]} dict."""
        for team, entries in setup.items():
            for entry in entries:
                actions = list(entry["actions"])
                actions += [a for a in config.DEFAULT_ACTIONS if a not in actions]
                facing = entry.get("facing", config.TEAM_FACING[team])
                self.tiles[entry["position"]].player = Player(team, actions, facing)

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
