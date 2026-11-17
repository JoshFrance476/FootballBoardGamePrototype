"""Everything to do with the screen: layout in pixels and drawing."""
import math

import pygame

import config
from hex_math import (DIRECTIONS, SQRT3, hex_corners, hex_to_pixel, pixel_to_hex,
                      point_in_hex)


# --- Layout ---

def tile_centre(q, r):
    """Pixel centre of a board tile, using this game's layout settings."""
    return hex_to_pixel(q, r, config.HEX_SIZE, config.HEX_GAP, config.ORIGIN)


def tile_at_pixel(pos, board):
    """The Tile under a screen position, or None if off the board or in a gap."""
    coord = pixel_to_hex(*pos, config.HEX_SIZE, config.HEX_GAP, config.ORIGIN)
    tile = board.get(coord)
    if tile is None or not point_in_hex(pos, tile_centre(*coord), config.HEX_SIZE):
        return None
    return tile


def button_row_y():
    return config.SCREEN_SIZE[1] - config.BUTTON_MARGIN - config.BUTTON_SIZE[1]


def action_buttons(game):
    """[(action name, pygame.Rect)] for the selected player's action buttons.

    Both drawing and click handling use this, so they always agree on
    where the buttons are.
    """
    names = game.available_actions
    if not names:
        return []
    # Full size if they fit, otherwise shrink to share the screen width
    max_width, height = config.BUTTON_SIZE
    available = config.SCREEN_SIZE[0] - 2 * config.BUTTON_MARGIN
    width = min(max_width, (available - config.BUTTON_GAP * (len(names) - 1)) // len(names))
    buttons = []
    for i, name in enumerate(names):
        x = config.BUTTON_MARGIN + i * (width + config.BUTTON_GAP)
        buttons.append((name, pygame.Rect(x, button_row_y(), width, height)))
    return buttons


def end_turn_button():
    """pygame.Rect of the End turn button, at the right end of the status line,
    leaving the whole bottom row for action buttons."""
    width, height = config.BUTTON_SIZE
    x = config.SCREEN_SIZE[0] - config.BUTTON_MARGIN - width
    return pygame.Rect(x, config.UI_POSITION[1], width, height)


def action_at_pixel(pos, game):
    """Name of the action button under a screen position, or None."""
    for name, rect in action_buttons(game):
        if rect.collidepoint(pos):
            return name
    return None


# --- Shapes ---

def draw_hexagon(screen, colour, centre, size):
    pygame.draw.polygon(screen, colour, hex_corners(centre, size))


def draw_bordered_hexagon(screen, colour, border_colour, centre, size, border_width):
    """Draw a hex with a border that sits inside its outline.

    The whole hex is drawn in the border colour, then a smaller hex in the
    fill colour goes on top. Shrinking the corner radius by
    border_width * 2 / sqrt(3) pulls every edge in by exactly border_width,
    so the border is even all the way round and the corners stay sharp.
    """
    draw_hexagon(screen, border_colour, centre, size)
    inner_size = size - border_width * 2 / SQRT3
    if inner_size > 0:
        draw_hexagon(screen, colour, centre, inner_size)


# --- Game drawing ---

class Renderer:
    def __init__(self, screen, font, button_font):
        self.screen = screen
        self.font = font
        self.button_font = button_font

    def draw(self, game, ai_turn=False, highlight=None):
        """Draw everything. ai_turn: a computer team is playing, so the End turn
        button shows that instead. highlight: a tile to mark as the AI's choice."""
        self.screen.fill(config.BACKGROUND_COLOUR)
        targets = set(game.targets)
        for tile in game.board:
            border = self.tile_border(tile, game.selected, targets, highlight)
            self.draw_tile(tile, border, game.board)
        self.draw_ball(game.board.ball)
        self.draw_player_previews(game)
        self.draw_ball_previews(game)
        self.draw_score(game)
        self.draw_ui(game)
        self.draw_action_buttons(game)
        self.draw_end_turn_button(game, ai_turn)

    # --- Board ---

    def tile_border(self, tile, selected, targets, highlight=None):
        """(colour, width) of the border this tile should have, or None."""
        if tile is selected or tile.coord == highlight:
            return config.SELECTED_COLOUR, config.SELECTED_BORDER_WIDTH
        if tile.coord in targets:
            return config.MOVE_TARGET_COLOUR, config.BORDER_WIDTH
        return None

    def tile_fill_colour(self, tile, board):
        if tile.player is not None:
            return config.TEAM_COLOURS[tile.player.team]
        side = board.goal_side(tile.coord)
        if side is not None:
            return config.GOAL_COLOURS[side]
        return config.PITCH_GREEN

    def draw_tile(self, tile, border, board):
        centre = tile_centre(tile.q, tile.r)
        colour = self.tile_fill_colour(tile, board)
        if border is None:
            draw_hexagon(self.screen, colour, centre, config.HEX_SIZE)
        else:
            border_colour, border_width = border
            draw_bordered_hexagon(self.screen, colour, border_colour,
                                  centre, config.HEX_SIZE, border_width)
        if tile.player is not None:
            self.draw_facing(centre, tile.player.facing)

    def draw_facing(self, centre, facing, colour=config.FACING_COLOUR):
        """Small triangle pointing from the tile centre the way the player faces."""
        # Unit vector on screen for this direction, from the hex layout itself
        dx, dy = hex_to_pixel(*DIRECTIONS[facing], size=1)
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        px, py = -uy, ux                       # perpendicular, for the back edge
        cx, cy = centre
        tip = (cx + ux * config.FACING_TIP, cy + uy * config.FACING_TIP)
        bx, by = cx + ux * config.FACING_BASE, cy + uy * config.FACING_BASE
        w = config.FACING_HALF_WIDTH
        pygame.draw.polygon(self.screen, colour,
                            [tip, (bx + px * w, by + py * w), (bx - px * w, by - py * w)])

    def draw_ball(self, coord):
        pygame.draw.circle(self.screen, config.BALL_COLOUR, tile_centre(*coord),
                           config.BALL_RADIUS)

    def draw_player_previews(self, game):
        """Outline, in the team's colour, where the chosen action would move the
        player, with their facing marker if every option ends facing the same way."""
        if game.selected is None:
            return
        colour = config.TEAM_COLOURS[game.selected.player.team]
        facings = {}    # destination coord -> set of directions they could end up facing
        for outcome in game.options.values():
            if outcome.player_to != game.selected.coord:
                facings.setdefault(outcome.player_to, set()).add(outcome.facing)
        for coord, directions in facings.items():
            centre = tile_centre(*coord)
            inner = config.HEX_SIZE - config.PREVIEW_PLAYER_INSET
            pygame.draw.polygon(self.screen, colour, hex_corners(centre, inner),
                                config.PREVIEW_PLAYER_WIDTH)
            if len(directions) == 1:
                self.draw_facing(centre, next(iter(directions)), colour)

    def draw_ball_previews(self, game):
        """Outline where the chosen action would send the ball."""
        for coord in {o.ball_to for o in game.options.values() if o.ball_to is not None}:
            pygame.draw.circle(self.screen, config.BALL_COLOUR, tile_centre(*coord),
                               config.BALL_RADIUS, config.PREVIEW_BALL_WIDTH)

    # --- UI ---

    def draw_score(self, game):
        label = (f"{config.TEAM_NAMES[config.HOME]}  {game.score[config.HOME]}"
                 f"  -  {game.score[config.AWAY]}  {config.TEAM_NAMES[config.AWAY]}")
        text = self.button_font.render(label, True, config.BLACK)
        self.screen.blit(text, text.get_rect(midtop=(config.SCREEN_SIZE[0] // 2, config.SCORE_Y)))

    def draw_ui(self, game):
        """Whose turn it is and their action points, then the status message."""
        label = (f"{config.TEAM_NAMES[game.active_team]}  -  "
                 f"{game.action_points}/{config.ACTION_POINTS_PER_TURN} AP")
        text = self.font.render(label, True, config.BLACK)
        rect = self.screen.blit(text, config.UI_POSITION)
        if game.status:
            status = self.button_font.render(game.status, True, config.BLACK)
            self.screen.blit(status, status.get_rect(midleft=(rect.right + 20, rect.centery)))

    def draw_button(self, rect, label, fill, text_colour):
        pygame.draw.rect(self.screen, fill, rect, border_radius=config.BUTTON_RADIUS)
        text = self.fit_label(label, text_colour, rect.inflate(-2 * config.BUTTON_PADDING,
                                                               -2 * config.BUTTON_PADDING))
        self.screen.blit(text, text.get_rect(center=rect.center))

    def fit_label(self, label, colour, area):
        """Render a label to fit inside area: on one line if it fits, otherwise
        split over two lines, and scaled down if it's still too big."""
        font = self.button_font
        lines = [label]
        words = label.split()
        if font.size(label)[0] > area.width and len(words) > 1:
            # Break at the space that makes the two lines most even
            splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
            lines = list(min(splits, key=lambda pair: max(font.size(line)[0] for line in pair)))

        rendered = [font.render(line, True, colour) for line in lines]
        width = max(surface.get_width() for surface in rendered)
        height = sum(surface.get_height() for surface in rendered)
        text = pygame.Surface((width, height), pygame.SRCALPHA)
        y = 0
        for surface in rendered:
            text.blit(surface, ((width - surface.get_width()) // 2, y))
            y += surface.get_height()

        scale = min(1, area.width / width, area.height / height)
        if scale < 1:
            text = pygame.transform.smoothscale(text, (int(width * scale), int(height * scale)))
        return text

    def button_colours(self, game, action_name):
        """(fill, text) colours for an action button in its current state."""
        if action_name == game.action:
            return config.BUTTON_ACTIVE_COLOUR, config.BUTTON_ACTIVE_TEXT_COLOUR
        if game.can_use(action_name):
            return config.BUTTON_COLOUR, config.BUTTON_TEXT_COLOUR
        return config.BUTTON_DISABLED_COLOUR, config.BUTTON_DISABLED_TEXT_COLOUR

    def draw_action_buttons(self, game):
        for name, rect in action_buttons(game):
            label = f"{name.capitalize()} ({config.ACTIONS[name]['cost']})"
            self.draw_button(rect, label, *self.button_colours(game, name))

    def draw_end_turn_button(self, game, ai_turn=False):
        if ai_turn:
            colours = config.BUTTON_DISABLED_COLOUR, config.BUTTON_DISABLED_TEXT_COLOUR
            self.draw_button(end_turn_button(), "AI's turn", *colours)
            return
        # Highlighted once the team has nothing left to spend
        if game.action_points == 0:
            colours = config.BUTTON_ACTIVE_COLOUR, config.BUTTON_ACTIVE_TEXT_COLOUR
        else:
            colours = config.BUTTON_COLOUR, config.BUTTON_TEXT_COLOUR
        self.draw_button(end_turn_button(), "End turn", *colours)
