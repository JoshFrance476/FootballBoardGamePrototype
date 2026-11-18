"""The screens: main menu, the settings editors, the match itself and the
pause menu. main.App keeps a stack of them; the top one gets the input.

The editors change the config module directly (see settings.py), so a match
started afterwards uses the new values. Save to config.py makes them
permanent; Revert to config.py throws them away.
"""
import contextlib
import io
import time

import pygame

import actions as actions_module
import config
import settings
import ui
from ai import AIController
from board import Board
from game import Game
from hex_math import RIGHT, LEFT, SQRT3, hex_corners, hex_to_pixel, pixel_to_hex, point_in_hex
from render import (action_at_pixel, end_turn_button, full_time_button_at_pixel, tile_at_pixel,
                    tile_centre, hex_size)

WIDTH, HEIGHT = config.SCREEN_SIZE
BACK_RECT = (20, HEIGHT - 56, 120, 40)


def check_config():
    """(error message or None, [notes]) from actions.validate_config."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            actions_module.validate_config()
        error = None
    except ValueError as exc:
        error = str(exc)
    notes = [line[len("Note: "):] for line in out.getvalue().splitlines() if line.startswith("Note: ")]
    return error, notes


def percent(value):
    return f"{round(value * 100)}%"


class Confirm:
    """A button that needs a second click within a few seconds."""

    def __init__(self, rect, label, confirm_label, on_confirm, enabled=None):
        self.armed_until = 0.0
        self.button = ui.Button(rect, lambda: confirm_label if self.armed() else label,
                                self.click, enabled, self.armed)
        self.on_confirm = on_confirm

    def armed(self):
        return time.perf_counter() < self.armed_until

    def click(self):
        if self.armed():
            self.armed_until = 0.0
            self.on_confirm()
        else:
            self.armed_until = time.perf_counter() + 3.0

    def handle(self, event):
        return self.button.handle(event)

    def draw(self, screen):
        self.button.draw(screen)


class Screen:
    title = ""

    def __init__(self, app):
        self.app = app
        self.widgets = []

    def handle(self, event):
        for widget in self.widgets:
            if widget.handle(event):
                return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.back()
            return True
        return False

    def back(self):
        self.app.pop()

    def update(self):
        pass

    def draw(self, screen):
        screen.fill(config.BACKGROUND_COLOUR)
        if self.title:
            ui.text(screen, self.title, (WIDTH // 2, 16), ui.TITLE_SIZE, anchor="midtop")
        for widget in self.widgets:
            widget.draw(screen)


# --- Main menu ---

class MainMenu(Screen):
    title = "Football Board Game"

    def __init__(self, app):
        super().__init__(app)
        self.message = ""
        self.error, self.notes = None, []
        w, h, gap = 280, 40, 8
        x, y = (WIDTH - w) // 2, 90

        def row(i):
            return (x, y + i * (h + gap), w, h)

        self.widgets = [
            ui.Button(row(0), "Play", self.play, enabled=lambda: self.error is None),
            ui.Button(row(1), "Match settings", lambda: app.push(MatchScreen(app))),
            ui.Button(row(2), "Pitch and rules", lambda: app.push(RulesScreen(app))),
            ui.Button(row(3), "Formation", lambda: app.push(FormationScreen(app))),
            ui.Button(row(4), "Actions", lambda: app.push(ActionsScreen(app))),
            ui.Button(row(5), "Save to config.py", self.save,
                      enabled=lambda: self.error is None and settings.has_unsaved_changes()),
            Confirm(row(6), "Revert to config.py", "Click again to revert", self.revert,
                    enabled=settings.has_unsaved_changes),
            ui.Button(row(7), "Quit", app.quit),
        ]

    def back(self):
        pass   # Esc on the main menu does nothing

    def play(self):
        self.app.push(GameScreen(self.app))

    def save(self):
        try:
            names = settings.save_to_config()
        except Exception as exc:   # never lose the edits because a save went wrong
            self.message = f"Couldn't save: {exc}"
            return
        self.message = (f"Saved {len(names)} setting{'s' if len(names) != 1 else ''} to config.py "
                        f"(the old file is config.py.bak)")

    def revert(self):
        settings.revert()
        self.message = "Reverted to config.py"

    def update(self):
        self.error, self.notes = check_config()

    def draw(self, screen):
        super().draw(screen)
        area = pygame.Rect(40, 486, WIDTH - 80, HEIGHT - 496)
        if self.error:
            ui.wrapped(screen, "Can't play yet: " + self.error, area, colour=ui.ERROR_COLOUR)
            return
        lines = []
        if settings.has_unsaved_changes():
            lines.append("You have unsaved changes: they apply to matches until you quit.")
        if self.message:
            lines.append(self.message)
        lines += self.notes
        ui.wrapped(screen, "\n".join(lines), area, colour=ui.FAINT_TEXT_COLOUR)


# --- Match settings ---

class MatchScreen(Screen):
    title = "Match settings"

    def __init__(self, app):
        super().__init__(app)
        x, w, h = 140, 520, 36
        rows = iter(range(100, 600, 50))

        def controller(team):
            return ui.Choice((x, next(rows), w, h), f"{config.TEAM_NAMES[team]} played by",
                             [("human", "Human"), ("ai", "Computer")],
                             lambda: config.CONTROLLERS[team],
                             lambda v: config.CONTROLLERS.__setitem__(team, v))

        def setter(name):
            def set_value(value):
                setattr(config, name, value)
                settings.changed()
            return set_value

        def seconds(v):
            return f"{v:g} s"

        self.widgets = [
            controller(config.HOME),
            controller(config.AWAY),
            ui.Stepper((x, next(rows), w, h), "Turns per team", lambda: config.TURNS_PER_TEAM,
                       setter("TURNS_PER_TEAM"), 1, 0, 99,
                       lambda v: "No limit" if v == 0 else str(v)),
            ui.Choice((x, next(rows), w, h), "Computer player",
                      [("planner", "Planner"), ("mcts", "MCTS")],
                      lambda: config.AI_ENGINE, setter("AI_ENGINE")),
            ui.Stepper((x, next(rows), w, h), "Planner thinking time per turn",
                       lambda: config.AI_PLAN_SECONDS, setter("AI_PLAN_SECONDS"), 0.5, 0.5, 30.0,
                       seconds, enabled=lambda: config.AI_ENGINE == "planner"),
            ui.Stepper((x, next(rows), w, h), "MCTS thinking time per move",
                       lambda: config.AI_THINK_SECONDS, setter("AI_THINK_SECONDS"), 0.2, 0.2, 10.0,
                       seconds, enabled=lambda: config.AI_ENGINE == "mcts"),
            ui.Stepper((x, next(rows), w, h), "Pause to show each computer move",
                       lambda: config.AI_MOVE_DELAY, setter("AI_MOVE_DELAY"), 0.1, 0.0, 3.0, seconds),
            ui.Button(BACK_RECT, "Back", self.back),
        ]


# --- Pitch and rules ---

class RulesScreen(Screen):
    title = "Pitch and rules"
    MAX_SHOT_DISTANCE = 6

    def __init__(self, app):
        super().__init__(app)
        self.build()

    def build(self):
        h = 32
        left_x, left_w = 30, 350
        right_x, right_w = 430, 340

        def chance_stepper(rect, label, table, key):
            def set_value(v):
                table[key] = v
                settings.changed()
            return ui.Stepper(rect, label, lambda: table[key], set_value, 0.05, 0.0, 1.0, percent)

        widgets = [
            ui.Stepper((left_x, 96, left_w, h), "Pitch width (tiles)", lambda: config.BOARD_COLS,
                       lambda v: settings.resize_pitch(v, config.BOARD_ROWS), 2, 7, 25),
            ui.Stepper((left_x, 132, left_w, h), "Pitch height (tiles)", lambda: config.BOARD_ROWS,
                       lambda v: settings.resize_pitch(config.BOARD_COLS, v), 2, 5, 17),
            ui.Stepper((left_x, 168, left_w, h), "Keeper's box radius", lambda: config.KEEPER_BOX_RADIUS,
                       self.set_box_radius, 1, 0, 4),
            chance_stepper((left_x, 214, left_w, h), "Tackle from the front", config.TACKLE_CHANCE, "front"),
            chance_stepper((left_x, 250, left_w, h), "Tackle from behind", config.TACKLE_CHANCE, "behind"),
            ui.Stepper((right_x, 128, right_w, h), "Starting a turn with the ball",
                       lambda: config.START_POSSESSION_ACTIONS,
                       self.count_setter("START_POSSESSION_ACTIONS"), 1, 0, 5),
            ui.Stepper((right_x, 164, right_w, h), "Winning the ball with a tackle",
                       lambda: config.TACKLE_POSSESSION_ACTIONS,
                       self.count_setter("TACKLE_POSSESSION_ACTIONS"), 1, 0, 5),
            ui.Stepper((right_x, 200, right_w, h), "Receiving a pass or loose ball",
                       lambda: config.RECEIVE_POSSESSION_ACTIONS,
                       self.count_setter("RECEIVE_POSSESSION_ACTIONS"), 1, 0, 5),
        ]
        y = 320
        for distance in sorted(config.KEEPER_SAVE_CHANCE):
            label = "On the goal tile" if distance == 0 else f"{distance} tile{'s' if distance > 1 else ''} out"
            widgets.append(chance_stepper((left_x, y, left_w, h), label, config.KEEPER_SAVE_CHANCE, distance))
            y += 36

        y = 278
        for distance in sorted(config.SHOT_CHANCE):
            label = f"From {distance} tile{'s' if distance > 1 else ''}"
            widgets.append(chance_stepper((right_x, y, right_w, h), label, config.SHOT_CHANCE, distance))
            y += 36
        widgets += [
            ui.Button((right_x, y + 6, 165, h), "Add distance", self.add_distance,
                      enabled=lambda: len(config.SHOT_CHANCE) < self.MAX_SHOT_DISTANCE),
            ui.Button((right_x + 175, y + 6, 165, h), "Remove furthest", self.remove_distance,
                      enabled=lambda: len(config.SHOT_CHANCE) > 1),
            ui.Button(BACK_RECT, "Back", self.back),
        ]
        self.widgets = widgets

    @staticmethod
    def count_setter(name):
        def set_value(count):
            setattr(config, name, count)
            settings.changed()
        return set_value

    def set_box_radius(self, radius):
        """Change the box, keeping one keeper-save chance per distance inside it."""
        table = config.KEEPER_SAVE_CHANCE
        for distance in list(table):
            if distance > radius:
                del table[distance]
        for distance in range(radius + 1):
            if distance not in table:
                previous = table.get(distance - 1, 0.2)
                table[distance] = round(round(previous / 2 / 0.05) * 0.05, 2)
        config.KEEPER_SAVE_CHANCE = dict(sorted(table.items()))
        config.KEEPER_BOX_RADIUS = radius
        settings.changed()
        self.build()

    def add_distance(self):
        table = config.SHOT_CHANCE
        furthest = max(table)
        table[furthest + 1] = round(round(table[furthest] / 2 / 0.05) * 0.05, 2)
        settings.changed()
        self.build()

    def remove_distance(self):
        del config.SHOT_CHANCE[max(config.SHOT_CHANCE)]
        settings.changed()
        self.build()

    def draw(self, screen):
        super().draw(screen)
        ui.text(screen, "Keeper save chance (shots on target)", (30, 294), bold=True)
        ui.text(screen, "Extra actions for a first possession", (430, 100), bold=True)
        ui.text(screen, "Shot on target, by distance from goal", (430, 250), bold=True)
        ui.wrapped(screen, "Changing the pitch size stretches the starting positions to fit; "
                           "check them on the Formation page.",
                   pygame.Rect(160, HEIGHT - 56, 250, 50), size=15, colour=ui.FAINT_TEXT_COLOUR)


# --- Formation ---

class FormationScreen(Screen):
    """Drag players to set where they start. Click one to edit them."""

    def __init__(self, app):
        super().__init__(app)
        self.selected = None        # (team, entry)
        self.dragging = None        # "player" or "ball"
        self.drag_from = None
        self.mouse = (0, 0)
        self.placing_ball = False
        w, h, gap = 150, 40, 8
        y1, y2 = HEIGHT - 2 * h - 22, HEIGHT - h - 12

        def col(i):
            return 12 + i * (w + gap)

        has_selection = lambda: self.selected is not None
        self.widgets = [
            ui.Button((col(0), y1, w, h), "Add Home player", lambda: self.add(config.HOME)),
            ui.Button((col(1), y1, w, h), "Add Away player", lambda: self.add(config.AWAY)),
            ui.Button((col(2), y1, w, h), "Mirror Home to Away", settings.mirror_home_to_away),
            ui.Toggle((col(3), y1, w, h), "Place ball", lambda: self.placing_ball,
                      lambda v: setattr(self, "placing_ball", v)),
            ui.Button((col(4), y1, w - 20, h), "Back", self.back),
            ui.Toggle((col(0), y2, w, h), "Goalkeeper",
                      lambda: has_selection() and self.selected[1].get("role") == "goalkeeper",
                      self.set_keeper, enabled=has_selection),
            ui.Choice((col(1), y2, 3 * 96 + 8, h), "",
                      [(None, "Team way"), (RIGHT, "Face right"), (LEFT, "Face left")],
                      lambda: self.selected[1].get("facing") if has_selection() else "none",
                      self.set_facing, option_width=96, enabled=has_selection),
            ui.Button((col(3), y2, w, h), "Remove player", self.remove, enabled=has_selection),
        ]

    # Edits

    def add(self, team):
        entry = settings.add_player(team)
        if entry is not None:
            self.selected = (team, entry)

    def set_keeper(self, keeper):
        settings.set_keeper(*self.selected, keeper)

    def set_facing(self, facing):
        entry = self.selected[1]
        if facing is None:
            entry.pop("facing", None)
        else:
            entry["facing"] = facing
        settings.changed()

    def remove(self):
        settings.remove_player(self.selected[1])
        self.selected = None

    # Input

    def board(self):
        return Board(config.BOARD_COLS, config.BOARD_ROWS)

    def handle(self, event):
        if super().handle(event):
            return True
        board = self.board()
        if event.type == pygame.MOUSEMOTION:
            self.mouse = event.pos
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.mouse = event.pos
            tile = tile_at_pixel(event.pos, board)
            if tile is None:
                self.selected = None
                return False
            if self.placing_ball:
                config.INITIAL_BALL = tile.coord
                self.placing_ball = False
                settings.changed()
                return True
            found = settings.player_at(tile.coord)
            if found is not None:
                self.selected = found
                self.dragging, self.drag_from = "player", tile.coord
            elif tile.coord == config.INITIAL_BALL:
                self.dragging, self.drag_from = "ball", tile.coord
            else:
                self.selected = None
            return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging:
            tile = tile_at_pixel(event.pos, board)
            if tile is not None and tile.coord != self.drag_from:
                if self.dragging == "ball":
                    config.INITIAL_BALL = tile.coord
                else:
                    other = settings.player_at(tile.coord)
                    if other is not None:   # swap places
                        other[1]["position"] = self.drag_from
                    self.selected[1]["position"] = tile.coord
                settings.changed()
            self.dragging = None
            return True
        return False

    # Drawing

    def draw(self, screen):
        screen.fill(config.BACKGROUND_COLOUR)
        renderer = self.app.renderer
        board = self.board()
        hidden = self.drag_from if self.dragging == "player" else None
        for tile in board:
            border = None
            if self.selected is not None and tile.coord == self.selected[1]["position"]:
                border = (config.SELECTED_COLOUR, config.SELECTED_BORDER_WIDTH)
            if tile.coord == hidden:
                player, tile.player = tile.player, None
                renderer.draw_tile(tile, border, board)
                tile.player = player
            else:
                renderer.draw_tile(tile, border, board)
        if self.dragging != "ball":
            renderer.draw_ball(config.INITIAL_BALL)
        # What's being dragged follows the mouse
        if self.dragging == "player":
            team, entry = self.selected
            colour = config.KEEPER_COLOURS[team] if entry.get("role") == "goalkeeper" \
                else config.TEAM_COLOURS[team]
            pygame.draw.polygon(screen, colour, hex_corners(self.mouse, hex_size()))
            renderer.draw_facing(self.mouse, entry.get("facing", config.TEAM_FACING[team]))
        elif self.dragging == "ball":
            pygame.draw.circle(screen, config.BALL_COLOUR, self.mouse, 8)
            pygame.draw.circle(screen, config.BLACK, self.mouse, 8, 1)

        counts = "    ".join(f"{config.TEAM_NAMES[t]}: {len(e)} players"
                             for t, e in config.INITIAL_SETUP.items())
        ui.text(screen, "Formation", (10, 4), bold=True)
        ui.text(screen, counts, (WIDTH - 10, 4), anchor="topright")
        hint = ("Click a tile to put the ball there" if self.placing_ball else
                "Drag players or the ball; click a player to edit")
        ui.text(screen, hint, (WIDTH // 2 - 40, 5), size=15, colour=ui.FAINT_TEXT_COLOUR,
                anchor="midtop")
        for widget in self.widgets:
            widget.draw(screen)


# --- Actions ---

class ActionsScreen(Screen):
    """List of actions on the left, the selected one's settings in the middle,
    and a grid on the right to click where the player and ball go."""

    LIST_RECT = pygame.Rect(10, 92, 170, HEIGHT - 102)
    ITEM_HEIGHT, ITEM_GAP = 28, 4
    GRID_CENTRE = (636, 300)
    GRID_RADIUS = 6
    GRID_SIZE = 12
    GRID_GAP = 2

    REQUIRE_LABELS = {"possession": "Has the ball", "no_possession": "Without ball",
                      "goalkeeper": "Goalkeeper", "in_own_box": "In own box"}
    EFFECT_LABELS = [("tackle", "Tackle"), ("shoot", "Shoot"), ("lofted", "Lofted"),
                     ("turn", "Turn"), ("face_movement", "Face move")]
    GIVEN_TO = [("OUTFIELD_ACTIONS", "Outfield"), ("KEEPER_ACTIONS", "Keepers"),
                ("DEFAULT_ACTIONS", "Everyone")]

    def __init__(self, app):
        super().__init__(app)
        self.selected = next(iter(config.ACTIONS), None)
        self.layer = "self"     # which offsets a grid click edits: "self" or "ball"
        self.scroll = 0
        self.message = ""
        self.build()

    # Helpers

    @property
    def action(self):
        return config.ACTIONS.get(self.selected)

    def has_action(self):
        return self.action is not None

    def ball_mode(self):
        if not self.has_action() or "ball" not in self.action:
            return "none"
        return "with_player" if self.action["ball"] == "with_player" else "tile"

    def build(self):
        x, w = 195, 270
        has = self.has_action
        widgets = [
            ui.Button((10, 50, 170, 34), "+ New action", self.new_action),
            ui.TextBox((x, 50, w, 32), lambda: self.selected or "", self.rename),
        ]
        for i, (key, label) in enumerate(self.REQUIRE_LABELS.items()):
            rect = (x + (i % 2) * 137, 116 + (i // 2) * 34, 133, 30)
            widgets.append(ui.Toggle(rect, label, self.requirement_getter(key),
                                     self.requirement_setter(key), enabled=has))
        for i, (key, label) in enumerate(self.EFFECT_LABELS):
            rect = (x + (i % 3) * 91, 210 + (i // 3) * 34, 87, 30)
            widgets.append(ui.Toggle(rect, label, self.flag_getter(key), self.flag_setter(key),
                                     enabled=has))
        widgets.append(ui.Choice((x, 304, w, 30), "",
                                 [("none", "None"), ("tile", "To a tile"), ("with_player", "With player")],
                                 self.ball_mode, self.set_ball_mode, option_width=87, enabled=has))
        for i, (list_name, label) in enumerate(self.GIVEN_TO):
            rect = (x + i * 91, 368, 87, 30)
            widgets.append(ui.Toggle(rect, label, self.given_getter(list_name),
                                     self.given_setter(list_name), enabled=has))
        widgets.append(Confirm((x, 416, w, 32), "Delete action", "Click again to delete",
                               self.delete, enabled=has))
        widgets.append(ui.Choice((480, 50, 310, 30), "Click sets",
                                 [("self", "Player"), ("ball", "Ball")],
                                 lambda: self.layer, lambda v: setattr(self, "layer", v),
                                 option_width=100,
                                 enabled=lambda: has() and self.ball_mode() == "tile"))
        self.widgets = widgets

    def requirement_getter(self, key):
        return lambda: self.has_action() and key in self.action.get("requires", [])

    def requirement_setter(self, key):
        opposite = {"possession": "no_possession", "no_possession": "possession"}.get(key)

        def set_value(on):
            requires = [r for r in self.action.get("requires", []) if r not in (key, opposite)]
            if on:
                requires.append(key)
                requires.sort(key=list(self.REQUIRE_LABELS).index)
            if requires:
                self.action["requires"] = requires
            else:
                self.action.pop("requires", None)
            settings.changed()
        return set_value

    def flag_getter(self, key):
        return lambda: self.has_action() and bool(self.action.get(key))

    def flag_setter(self, key):
        exclusive = {"turn": "face_movement", "face_movement": "turn"}.get(key)

        def set_value(on):
            if on:
                self.action[key] = True
                if exclusive:
                    self.action.pop(exclusive, None)
            else:
                self.action.pop(key, None)
            settings.changed()
        return set_value

    def given_getter(self, list_name):
        return lambda: self.has_action() and self.selected in getattr(config, list_name)

    def given_setter(self, list_name):
        def set_value(on):
            names = getattr(config, list_name)
            if on and self.selected not in names:
                names.append(self.selected)
            elif not on:
                names[:] = [n for n in names if n != self.selected]
            settings.changed()
        return set_value

    def set_ball_mode(self, mode):
        if mode == "none":
            self.action.pop("ball", None)
        elif mode == "with_player":
            self.action["ball"] = "with_player"
        elif self.ball_mode() != "tile":
            self.action["ball"] = []
        if mode != "tile":
            self.layer = "self"
        settings.changed()

    # Edits

    def new_action(self):
        self.selected = settings.new_action()
        self.scroll = max(0, len(config.ACTIONS) * (self.ITEM_HEIGHT + self.ITEM_GAP)
                          - self.LIST_RECT.height)
        self.message = "New action: set where it moves the player or ball, or what else it does."

    def rename(self, new_name):
        error = settings.rename_action(self.selected, new_name)
        if error:
            self.message = error
        else:
            self.selected = new_name.strip()
            self.message = ""

    def delete(self):
        names = list(config.ACTIONS)
        index = names.index(self.selected)
        settings.delete_action(self.selected)
        names = list(config.ACTIONS)
        self.selected = names[min(index, len(names) - 1)] if names else None
        self.message = ""

    # Grid geometry

    def grid_tiles(self):
        n = self.GRID_RADIUS
        return [(q, r) for q in range(-n, n + 1) for r in range(-n, n + 1) if abs(q + r) <= n]

    def grid_centre(self, offset):
        return hex_to_pixel(*offset, self.GRID_SIZE, self.GRID_GAP, self.GRID_CENTRE)

    def grid_offset_at(self, pos):
        offset = pixel_to_hex(*pos, self.GRID_SIZE, self.GRID_GAP, self.GRID_CENTRE)
        q, r = offset
        if max(abs(q), abs(r), abs(q + r)) > self.GRID_RADIUS:
            return None
        if not point_in_hex(pos, self.grid_centre(offset), self.GRID_SIZE):
            return None
        return offset

    # Input

    def item_rects(self):
        """[(name, rect)] for the visible action list entries."""
        rects = []
        y = self.LIST_RECT.top - self.scroll
        for name in config.ACTIONS:
            rect = pygame.Rect(self.LIST_RECT.left, y, self.LIST_RECT.width, self.ITEM_HEIGHT)
            if rect.bottom > self.LIST_RECT.top and rect.top < self.LIST_RECT.bottom:
                rects.append((name, rect))
            y += self.ITEM_HEIGHT + self.ITEM_GAP
        return rects

    def handle(self, event):
        editing = any(isinstance(w, ui.TextBox) and w.editing for w in self.widgets)
        if editing and event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return any(w.handle(event) for w in self.widgets if isinstance(w, ui.TextBox))
        if super().handle(event):
            return True
        if event.type == pygame.MOUSEWHEEL and self.LIST_RECT.collidepoint(pygame.mouse.get_pos()):
            total = len(config.ACTIONS) * (self.ITEM_HEIGHT + self.ITEM_GAP)
            self.scroll = max(0, min(self.scroll - event.y * 30, total - self.LIST_RECT.height))
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for name, rect in self.item_rects():
                if rect.collidepoint(event.pos) and self.LIST_RECT.collidepoint(event.pos):
                    self.selected = name
                    self.message = ""
                    if self.ball_mode() != "tile":
                        self.layer = "self"
                    return True
            offset = self.grid_offset_at(event.pos)
            if offset is not None and offset != (0, 0) and self.has_action():
                key = self.layer
                offsets = settings.offsets_of(self.action, key)
                if offset in offsets:
                    offsets.remove(offset)
                else:
                    offsets.append(offset)
                settings.set_offsets(self.action, key, offsets)
                return True
        return False

    # Drawing

    def draw(self, screen):
        screen.fill(config.BACKGROUND_COLOUR)
        ui.text(screen, "Actions", (10, 12), 30, bold=True)

        # The list
        clip = screen.get_clip()
        screen.set_clip(self.LIST_RECT)
        for name, rect in self.item_rects():
            ui.draw_box(screen, rect, name, *ui.colours(True, name == self.selected))
        screen.set_clip(clip)

        x = 195
        ui.text(screen, "Needs", (x, 96), bold=True)
        ui.text(screen, "Also does", (x, 190), bold=True)
        ui.text(screen, "Ball goes", (x, 284), bold=True)
        ui.text(screen, "Given to", (x, 348), bold=True)
        for widget in self.widgets:
            widget.draw(screen)
        self.draw_grid(screen)
        self.draw_messages(screen, pygame.Rect(x, 458, 270, HEIGHT - 466))

    def draw_grid(self, screen):
        action = self.action or {}
        selves = settings.offsets_of(action, "self")
        balls = settings.offsets_of(action, "ball")
        with_player = action.get("ball") == "with_player"
        team_colour = config.TEAM_COLOURS[config.HOME]
        for offset in self.grid_tiles():
            centre = self.grid_centre(offset)
            fill = team_colour if offset == (0, 0) else config.PITCH_GREEN
            pygame.draw.polygon(screen, fill, hex_corners(centre, self.GRID_SIZE))
            if offset in selves:
                pygame.draw.polygon(screen, config.YELLOW, hex_corners(centre, self.GRID_SIZE - 2), 3)
            if offset in balls or (with_player and offset in selves):
                pygame.draw.circle(screen, config.BALL_COLOUR, centre, 4)
        # The player facing right, in the middle
        cx, cy = self.grid_centre((0, 0))
        pygame.draw.polygon(screen, config.WHITE, [(cx + 8, cy), (cx + 3, cy - 4), (cx + 3, cy + 4)])

        y = self.GRID_CENTRE[1] + int((self.GRID_RADIUS + 1) * 1.5 * (self.GRID_SIZE + self.GRID_GAP / SQRT3)) + 4
        legend = ("Yellow outline: where the player can end up. White dot: where the ball "
                  "can go. Offsets are for a player facing right; they're mirrored for "
                  "players facing left. Click a tile to add or remove it.")
        ui.wrapped(screen, legend, pygame.Rect(480, y, 310, HEIGHT - y - 6), size=15,
                   colour=ui.FAINT_TEXT_COLOUR)

    def draw_messages(self, screen, area):
        error, notes = check_config()
        lines, colour = [], ui.FAINT_TEXT_COLOUR
        if error:
            lines.append(error)
            colour = ui.ERROR_COLOUR
        elif self.message:
            lines.append(self.message)
        lines += [n for n in notes if self.selected and repr(self.selected) in n]
        ui.wrapped(screen, "\n".join(lines), area, size=15, colour=colour)


# --- The match ---

class GameScreen(Screen):
    def __init__(self, app):
        super().__init__(app)
        self.restart()

    def restart(self):
        self.game = Game(Board(config.BOARD_COLS, config.BOARD_ROWS))
        self.ai = AIController()

    def ai_turn(self):
        return self.ai.controls(self.game.active_team) and not self.game.over

    def handle(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.push(PauseScreen(self.app, self))
            return True
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        game = self.game
        if game.over:
            choice = full_time_button_at_pixel(event.pos)
            if choice == "play again":
                self.restart()
            elif choice == "main menu":
                self.app.pop()
            elif choice == "quit":
                self.app.quit()
            return True
        if self.ai_turn():
            return False                 # clicks are ignored while the AI plays
        if end_turn_button().collidepoint(event.pos):
            game.end_turn()
            return True
        action = action_at_pixel(event.pos, game)
        if action is not None:
            game.choose_action(action)
        else:
            game.click(tile_at_pixel(event.pos, game.board))
        return True

    def update(self):
        self.ai.update(self.game)

    def draw(self, screen):
        self.app.renderer.draw(self.game, self.ai_turn(), self.ai.highlight)
        if not self.game.over:
            ui.text(screen, "Esc: menu", (10, 6), size=15, colour=ui.FAINT_TEXT_COLOUR)


class PauseScreen(Screen):
    """Over the match: Resume / Restart match / Main menu / Quit. The match
    (and the computer players) stop while it's open."""

    def __init__(self, app, game_screen):
        super().__init__(app)
        self.game_screen = game_screen
        w, h, gap = 240, 42, 10
        x = (WIDTH - w) // 2
        top = HEIGHT // 2 - 2 * (h + gap) + 20

        def row(i):
            return (x, top + i * (h + gap), w, h)

        self.widgets = [
            ui.Button(row(0), "Resume", self.back),
            ui.Button(row(1), "Restart match", self.restart),
            ui.Button(row(2), "Main menu", self.main_menu),
            ui.Button(row(3), "Quit", app.quit),
        ]
        self.panel = pygame.Rect(0, 0, w + 60, 4 * (h + gap) + 80)
        self.panel.midtop = (WIDTH // 2, top - 60)

    def restart(self):
        self.game_screen.restart()
        self.app.pop()

    def main_menu(self):
        self.app.pop()   # this
        self.app.pop()   # the match

    def draw(self, screen):
        self.game_screen.draw(screen)
        shade = pygame.Surface(config.SCREEN_SIZE, pygame.SRCALPHA)
        shade.fill(config.FULL_TIME_SHADE)
        screen.blit(shade, (0, 0))
        pygame.draw.rect(screen, config.FULL_TIME_PANEL_COLOUR, self.panel,
                         border_radius=config.BUTTON_RADIUS * 2)
        ui.text(screen, "Paused", (WIDTH // 2, self.panel.top + 16), 32, anchor="midtop")
        for widget in self.widgets:
            widget.draw(screen)
