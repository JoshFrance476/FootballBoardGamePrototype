"""Everything to do with the screen: layout in pixels and drawing."""
import math

import pygame

import config
from board import shift_top
from hex_math import (DIRECTIONS, SQRT3, hex_corners, hex_to_pixel, pixel_to_hex,
                      point_in_hex, rectangle_coords)


# --- Layout ---

_layout_cache = {}


def layout():
    """(hex size, gap, origin) that fit the pitch between the score and the UI.

    Hexes are config.HEX_SIZE when the pitch fits at that size, and shrink
    (gap and markers too) for bigger pitches. The pitch is centred.
    """
    key = (config.BOARD_COLS, config.BOARD_ROWS, shift_top(), config.HEX_SIZE, config.HEX_GAP,
           config.SCREEN_SIZE, config.UI_POSITION, config.SCORE_Y)
    if key not in _layout_cache:
        coords = rectangle_coords(config.BOARD_COLS, config.BOARD_ROWS, shift_top())
        size, gap = config.HEX_SIZE, config.HEX_GAP
        points = [hex_to_pixel(q, r, size, gap) for q, r in coords]
        half_width, half_height = size * SQRT3 / 2, size
        left = min(x for x, _ in points) - half_width
        right = max(x for x, _ in points) + half_width
        top = min(y for _, y in points) - half_height
        bottom = max(y for _, y in points) + half_height
        area_top = config.SCORE_Y + 22
        area_bottom = config.UI_POSITION[1] - 2
        area_width = config.SCREEN_SIZE[0] - 20
        scale = min(1.0, area_width / (right - left), (area_bottom - area_top) / (bottom - top))
        size, gap = size * scale, gap * scale
        origin = (config.SCREEN_SIZE[0] / 2 - (left + right) / 2 * scale,
                  (area_top + area_bottom) / 2 - (top + bottom) / 2 * scale)
        _layout_cache[key] = (size, gap, origin)
    return _layout_cache[key]


def hex_size():
    return layout()[0]


def scaled(pixels):
    """A marker size from config, shrunk along with the hexes."""
    return max(1, round(pixels * hex_size() / config.HEX_SIZE))


def tile_centre(q, r):
    """Pixel centre of a board tile."""
    size, gap, origin = layout()
    return hex_to_pixel(q, r, size, gap, origin)


def coord_at_pixel(pos):
    """The axial coordinate under a screen position (may be off the board)."""
    size, gap, origin = layout()
    return pixel_to_hex(*pos, size, gap, origin)


def tile_at_pixel(pos, board):
    """The Tile under a screen position, or None if off the board or in a gap."""
    coord = coord_at_pixel(pos)
    tile = board.get(coord)
    if tile is None or not point_in_hex(pos, tile_centre(*coord), hex_size()):
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


FULL_TIME_CHOICES = ["play again", "main menu", "quit"]
FULL_TIME_LINE_HEIGHT = 24      # each extra line of text (e.g. the ranking change) adds this


def full_time_panel(extra_lines=0):
    """pygame.Rect of the full-time box, centred on the screen, taller for
    extra lines of text under the result."""
    width, height = config.FULL_TIME_PANEL_SIZE
    rect = pygame.Rect(0, 0, width, height + extra_lines * FULL_TIME_LINE_HEIGHT)
    rect.center = (config.SCREEN_SIZE[0] // 2, config.SCREEN_SIZE[1] // 2)
    return rect


def full_time_buttons(choices=FULL_TIME_CHOICES, extra_lines=0):
    """{choice: Rect} side by side along the bottom of the full-time box."""
    panel = full_time_panel(extra_lines)
    width, height = config.BUTTON_SIZE
    gap = config.BUTTON_GAP * 2
    total = len(choices) * width + (len(choices) - 1) * gap
    y = panel.bottom - height - 20
    return {name: pygame.Rect(panel.centerx - total // 2 + i * (width + gap), y, width, height)
            for i, name in enumerate(choices)}


def full_time_button_at_pixel(pos, choices=FULL_TIME_CHOICES, extra_lines=0):
    """The choice under pos ("play again", "main menu", "quit"...) or None."""
    for name, rect in full_time_buttons(choices, extra_lines).items():
        if rect.collidepoint(pos):
            return name
    return None


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

    def draw(self, game, ai_turn=False, highlight=None, full_time=None):
        """Draw everything. ai_turn: a computer team is playing, so the End turn
        button shows that instead. highlight: a tile to mark as the AI's choice.
        full_time: (choices, extra lines of text) for the full-time box, if not
        the usual Play again / Main menu / Quit."""
        self.screen.fill(config.BACKGROUND_COLOUR)
        targets = set(game.targets)
        previews = set(game.preview_options) - targets     # destinations shown but not clickable
        steps = set(game.selected_step_targets)
        for tile in game.board:
            border = self.tile_border(tile, game.selected, targets, highlight, steps, previews)
            self.draw_tile(tile, border, game.board)
        self.draw_ball(game.board.ball)
        self.draw_player_previews(game)
        self.draw_ball_previews(game)
        self.draw_score(game)
        self.draw_ui(game)
        self.draw_action_buttons(game)
        self.draw_end_turn_button(game, ai_turn)
        if game.over:
            self.draw_full_time(game, *(full_time or (FULL_TIME_CHOICES, [])))

    # --- Board ---

    def tile_border(self, tile, selected, targets, highlight=None, steps=(), previews=()):
        """(colour, width) of the border this tile should have, or None."""
        if tile is selected or tile.coord == highlight:
            return config.SELECTED_COLOUR, config.SELECTED_BORDER_WIDTH
        if tile.coord in targets:
            return config.MOVE_TARGET_COLOUR, config.BORDER_WIDTH
        if tile.coord in previews:
            return config.PREVIEW_TARGET_COLOUR, config.BORDER_WIDTH
        if tile.coord in steps:
            return config.STEP_TARGET_COLOUR, config.STEP_TARGET_WIDTH
        return None

    def tile_fill_colour(self, tile, board):
        if tile.player is not None:
            colours = config.KEEPER_COLOURS if tile.player.is_keeper else config.TEAM_COLOURS
            return colours[tile.player.team]
        side = board.goal_side(tile.coord)
        if side is not None:
            return config.GOAL_COLOURS[side]
        if board.in_box(tile.coord, config.HOME) or board.in_box(tile.coord, config.AWAY):
            return config.BOX_COLOUR
        return config.PITCH_GREEN

    def draw_tile(self, tile, border, board):
        centre = tile_centre(tile.q, tile.r)
        colour = self.tile_fill_colour(tile, board)
        if border is None:
            draw_hexagon(self.screen, colour, centre, hex_size())
        else:
            border_colour, border_width = border
            draw_bordered_hexagon(self.screen, colour, border_colour,
                                  centre, hex_size(), scaled(border_width))
        if tile.player is not None:
            if tile.player.shield > 0:
                inner = hex_size() - scaled(config.SHIELD_INSET)
                pygame.draw.polygon(self.screen, config.SHIELD_COLOUR, hex_corners(centre, inner),
                                    max(1, scaled(config.SHIELD_WIDTH)))
            self.draw_facing(centre, tile.player.facing)

    def draw_facing(self, centre, facing, colour=config.FACING_COLOUR):
        """Small triangle pointing from the tile centre the way the player faces."""
        # Unit vector on screen for this direction, from the hex layout itself
        dx, dy = hex_to_pixel(*DIRECTIONS[facing], size=1)
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        px, py = -uy, ux                       # perpendicular, for the back edge
        cx, cy = centre
        tip = (cx + ux * scaled(config.FACING_TIP), cy + uy * scaled(config.FACING_TIP))
        bx, by = cx + ux * scaled(config.FACING_BASE), cy + uy * scaled(config.FACING_BASE)
        w = scaled(config.FACING_HALF_WIDTH)
        pygame.draw.polygon(self.screen, colour,
                            [tip, (bx + px * w, by + py * w), (bx - px * w, by - py * w)])

    def draw_ball(self, coord):
        pygame.draw.circle(self.screen, config.BALL_COLOUR, tile_centre(*coord),
                           scaled(config.BALL_RADIUS))

    def draw_player_previews(self, game):
        """Outline, in the team's colour, where the chosen action would move the
        player, with their facing marker if every option ends facing the same way."""
        if game.selected is None:
            return
        colour = config.TEAM_COLOURS[game.selected.player.team]
        facings = {}    # destination coord -> set of directions they could end up facing
        for outcome in game.preview_options.values():
            if outcome.player_to != game.selected.coord:
                facings.setdefault(outcome.player_to, set()).add(outcome.facing)
        for coord, directions in facings.items():
            centre = tile_centre(*coord)
            inner = hex_size() - scaled(config.PREVIEW_PLAYER_INSET)
            pygame.draw.polygon(self.screen, colour, hex_corners(centre, inner),
                                config.PREVIEW_PLAYER_WIDTH)
            if len(directions) == 1:
                self.draw_facing(centre, next(iter(directions)), colour)

    def draw_ball_previews(self, game):
        """Outline where the chosen (or hovered) action would send the ball."""
        for coord in {o.ball_to for o in game.preview_options.values() if o.ball_to is not None}:
            pygame.draw.circle(self.screen, config.BALL_COLOUR, tile_centre(*coord),
                               scaled(config.BALL_RADIUS), config.PREVIEW_BALL_WIDTH)

    # --- UI ---

    def draw_score(self, game):
        label = (f"{config.TEAM_NAMES[config.HOME]}  {game.score[config.HOME]}"
                 f"  -  {game.score[config.AWAY]}  {config.TEAM_NAMES[config.AWAY]}")
        text = self.button_font.render(label, True, config.BLACK)
        rect = self.screen.blit(text, text.get_rect(midtop=(config.SCREEN_SIZE[0] // 2, config.SCORE_Y)))
        if game.turn_limit is not None:
            clock = "Full time" if game.over else f"Turn {game.turn_number} of {game.turn_limit}"
            text = self.button_font.render(clock, True, config.BLACK)
            self.screen.blit(text, text.get_rect(topright=(config.SCREEN_SIZE[0] - 10, config.SCORE_Y)))

    def draw_full_time(self, game, choices=FULL_TIME_CHOICES, lines=()):
        """Dim the screen and show the result, any extra lines, and the choice buttons."""
        shade = pygame.Surface(config.SCREEN_SIZE, pygame.SRCALPHA)
        shade.fill(config.FULL_TIME_SHADE)
        self.screen.blit(shade, (0, 0))
        panel = full_time_panel(len(lines))
        pygame.draw.rect(self.screen, config.FULL_TIME_PANEL_COLOUR, panel,
                         border_radius=config.BUTTON_RADIUS * 2)
        title = self.font.render("Full time", True, config.BLACK)
        self.screen.blit(title, title.get_rect(midtop=(panel.centerx, panel.top + 20)))
        result = self.button_font.render(game.result_text(), True, config.BLACK)
        self.screen.blit(result, result.get_rect(midtop=(panel.centerx, panel.top + 60)))
        for i, line in enumerate(lines):
            text = self.button_font.render(line, True, config.BLACK)
            y = panel.top + 60 + (i + 1) * FULL_TIME_LINE_HEIGHT
            self.screen.blit(text, text.get_rect(midtop=(panel.centerx, y)))
        for name, rect in full_time_buttons(choices, len(lines)).items():
            self.draw_button(rect, name.capitalize(), config.BUTTON_COLOUR, config.BUTTON_TEXT_COLOUR)

    def draw_ui(self, game):
        """Whose turn it is, what they have left underneath, then the status message."""
        if game.over:
            return
        text = self.font.render(config.TEAM_NAMES[game.active_team], True, config.BLACK)
        rect = self.screen.blit(text, config.UI_POSITION)
        budget = f"Actions left: {game.actions_left()}    Steps left: {game.steps_left()}"
        self.screen.blit(self.button_font.render(budget, True, config.BLACK),
                         (config.UI_POSITION[0], rect.bottom))
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
            label = name.capitalize()
            self.draw_button(rect, label, *self.button_colours(game, name))

    def draw_end_turn_button(self, game, ai_turn=False):
        if game.over:
            colours = config.BUTTON_DISABLED_COLOUR, config.BUTTON_DISABLED_TEXT_COLOUR
            self.draw_button(end_turn_button(), "Full time", *colours)
            return
        if ai_turn:
            colours = config.BUTTON_DISABLED_COLOUR, config.BUTTON_DISABLED_TEXT_COLOUR
            self.draw_button(end_turn_button(), "AI's turn", *colours)
            return
        # Highlighted once the team has nothing left to do
        if game.actions_left() == 0 and game.steps_left() == 0:
            colours = config.BUTTON_ACTIVE_COLOUR, config.BUTTON_ACTIVE_TEXT_COLOUR
        else:
            colours = config.BUTTON_COLOUR, config.BUTTON_TEXT_COLOUR
        self.draw_button(end_turn_button(), "End turn", *colours)
