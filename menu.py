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
import ranking as ranking_module
import settings
import ui
from ai import AIController
from board import Board
from game import Game
from hex_math import RIGHT, LEFT, SQRT3, hex_corners, hex_to_pixel, pixel_to_hex, point_in_hex
from render import (FULL_TIME_CHOICES, action_at_pixel, end_turn_button, full_time_button_at_pixel,
                    tile_at_pixel, tile_centre, hex_size)

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
        w, h, gap = 280, 36, 6
        x, y = (WIDTH - w) // 2, 84

        def row(i):
            return (x, y + i * (h + gap), w, h)

        self.widgets = [
            ui.Button(row(0), "Play", self.play, enabled=lambda: self.error is None),
            ui.Button(row(1), "Rankings", lambda: app.push(RankingsScreen(app))),
            ui.Button(row(2), "Match settings", lambda: app.push(MatchScreen(app))),
            ui.Button(row(3), "Pitch and rules", lambda: app.push(RulesScreen(app))),
            ui.Button(row(4), "Formation", lambda: app.push(FormationScreen(app))),
            ui.Button(row(5), "Actions", lambda: app.push(ActionsScreen(app))),
            ui.Button(row(6), "Save to config.py", self.save,
                      enabled=lambda: self.error is None and settings.has_unsaved_changes()),
            Confirm(row(7), "Revert to config.py", "Click again to revert", self.revert,
                    enabled=settings.has_unsaved_changes),
            ui.Button(row(8), "Quit", app.quit),
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
        rows = iter(range(90, 600, 48))

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

        def actions_per_turn(team):
            def set_value(v):
                config.ACTIONS_PER_TURN[team] = v
                settings.changed()
            return ui.Stepper((x, next(rows), w, h), f"{config.TEAM_NAMES[team]} actions per turn",
                              lambda: config.ACTIONS_PER_TURN[team], set_value, 1, 0, 9)

        self.widgets = [
            controller(config.HOME),
            controller(config.AWAY),
            actions_per_turn(config.HOME),
            actions_per_turn(config.AWAY),
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
    MAX_SHOT_DISTANCE = 5

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

        def intercept_setter(v):
            config.INTERCEPT_CHANCE = v
            settings.changed()

        widgets = [
            ui.Stepper((left_x, 92, left_w, h), "Pitch width (tiles)", lambda: config.BOARD_COLS,
                       lambda v: settings.resize_pitch(v, config.BOARD_ROWS), 2, 7, 25),
            ui.Stepper((left_x, 126, left_w, h), "Pitch height (tiles)", lambda: config.BOARD_ROWS,
                       lambda v: settings.resize_pitch(config.BOARD_COLS, v), 2, 5, 17),
            ui.Stepper((left_x, 160, left_w, h), "Keeper's box radius", lambda: config.KEEPER_BOX_RADIUS,
                       self.set_box_radius, 1, 0, 4),
            chance_stepper((left_x, 200, left_w, h), "Tackle from the front", config.TACKLE_CHANCE, "front"),
            chance_stepper((left_x, 234, left_w, h), "Tackle from behind", config.TACKLE_CHANCE, "behind"),
            chance_stepper((left_x, 268, left_w, h), "Moving away: tackle from front",
                           config.ZOC_TACKLE_CHANCE, "front"),
            chance_stepper((left_x, 302, left_w, h), "Moving away: tackle from behind",
                           config.ZOC_TACKLE_CHANCE, "behind"),
            ui.Stepper((left_x, 336, left_w, h), "Interception, per tile passed",
                       lambda: config.INTERCEPT_CHANCE, intercept_setter, 0.05, 0.0, 1.0, percent),
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
        y = 400
        for distance in sorted(config.KEEPER_SAVE_CHANCE):
            label = "On the goal tile" if distance == 0 else f"{distance} tile{'s' if distance > 1 else ''} out"
            widgets.append(chance_stepper((left_x, y, left_w, h), label, config.KEEPER_SAVE_CHANCE, distance))
            y += 34

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
            ui.Choice((right_x, 508, right_w, h), "Shifted rows",
                      [("top", "Top row"), ("second", "Second row")],
                      lambda: config.SHIFTED_ROWS, settings.set_shifted_rows, option_width=100),
            ui.Button((WIDTH - 140, HEIGHT - 50, 120, 40), "Back", self.back),
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
        ui.text(screen, "Keeper save chance (shots on target)", (30, 376), bold=True)
        ui.text(screen, "Extra actions for a first possession", (430, 100), bold=True)
        ui.text(screen, "Shot on target, by distance from goal", (430, 250), bold=True)
        ui.text(screen, "Changing the pitch size stretches the starting positions to fit: "
                        "check them on the Formation page.", (WIDTH // 2, 62), size=15,
                colour=ui.FAINT_TEXT_COLOUR, anchor="midtop")


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
            ui.Button((col(4), y2, w - 20, h), "Actions...", self.edit_actions, enabled=has_selection),
        ]

    def edit_actions(self):
        self.app.push(PlayerActionsScreen(self.app, *self.selected))

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
        if self.placing_ball:
            hint = "Click a tile to put the ball there"
        elif self.selected is not None:
            entry = self.selected[1]
            count = len(actions_module.player_actions(entry["actions"]))
            hint = f"Selected: {settings.action_set_name(entry)}, {count} actions"
        else:
            hint = "Drag players or the ball; click a player to edit"
        ui.text(screen, hint, (WIDTH // 2 - 40, 5), size=15, colour=ui.FAINT_TEXT_COLOUR,
                anchor="midtop")
        for widget in self.widgets:
            widget.draw(screen)


# --- One player's actions ---

class PlayerActionsScreen(Screen):
    """Tick the actions one player has, up to config.MAX_PLAYER_ACTIONS."""

    COLUMNS, CELL_W, CELL_H, GAP = 3, 240, 34, 10

    def __init__(self, app, team, entry):
        super().__init__(app)
        self.team, self.entry = team, entry
        self.title = f"{config.TEAM_NAMES[team]} player's actions"
        self.widgets = []
        for i, name in enumerate(config.ACTIONS):
            col, row = i % self.COLUMNS, i // self.COLUMNS
            rect = (30 + col * (self.CELL_W + self.GAP), 96 + row * (self.CELL_H + 6),
                    self.CELL_W, self.CELL_H)
            self.widgets.append(ui.Toggle(rect, self.label_for(name), self.has_getter(name),
                                          lambda on, name=name: self.toggle(name),
                                          enabled=self.can_toggle(name)))

        def set_limit(v):
            config.MAX_PLAYER_ACTIONS = v
            settings.changed()

        y1, y2 = HEIGHT - 100, HEIGHT - 52
        self.widgets += [
            ui.Stepper((30, y1, 400, 36), "Most actions a player can have",
                       lambda: config.MAX_PLAYER_ACTIONS, set_limit, 1, 0, 30,
                       lambda v: "No limit" if v == 0 else str(v)),
            ui.Button((30, y2, 150, 40), "Outfield set",
                      lambda: settings.use_action_set(self.entry, "OUTFIELD_ACTIONS")),
            ui.Button((190, y2, 150, 40), "Keeper set",
                      lambda: settings.use_action_set(self.entry, "KEEPER_ACTIONS")),
            ui.Button((350, y2, 230, 40), "Copy to outfield team-mates",
                      lambda: settings.copy_actions_to_teammates(self.team, self.entry)),
            ui.Button((WIDTH - 140, y2, 120, 40), "Back", self.back),
        ]

    def own(self):
        return self.entry["actions"]

    def toggle(self, name):
        if name in config.DEFAULT_ACTIONS and name not in self.own():
            return                             # everyone has it: nothing to change
        settings.toggle_player_action(self.entry, name)

    def count(self):
        return len(actions_module.player_actions(self.own()))

    def has_getter(self, name):
        return lambda: name in self.own() or name in config.DEFAULT_ACTIONS

    def label_for(self, name):
        def label():
            if name in self.own():
                return f"{self.own().index(name) + 1}. {name}"
            if name in config.DEFAULT_ACTIONS:
                return f"{name} (everyone)"
            return name
        return label

    def can_toggle(self, name):
        def enabled():
            if name in self.own():
                return True                    # can always take one away
            if name in config.DEFAULT_ACTIONS:
                return True                    # shown as on; clicking does nothing
            limit = config.MAX_PLAYER_ACTIONS
            return limit == 0 or self.count() < limit
        return enabled

    def draw(self, screen):
        super().draw(screen)
        limit = config.MAX_PLAYER_ACTIONS
        of = f" of {limit}" if limit else ""
        ui.text(screen, f"Starting at {tuple(self.entry['position'])} - {settings.action_set_name(self.entry)}"
                        f" - {self.count()}{of} actions", (WIDTH // 2, 62), size=15,
                colour=ui.FAINT_TEXT_COLOUR, anchor="midtop")
        rows = (len(config.ACTIONS) + self.COLUMNS - 1) // self.COLUMNS
        top = 96 + rows * (self.CELL_H + 6) + 8
        lines = ["Click an action to give it to this player or take it away. Their buttons appear "
                 "in the numbered order. Actions given to everyone (DEFAULT_ACTIONS) count towards "
                 "the limit. Changing this player doesn't change anyone else."]
        if limit and self.count() > limit:
            lines.append(f"This player has more than {limit} actions: take some away to play.")
        ui.wrapped(screen, "\n".join(lines), pygame.Rect(30, top, WIDTH - 60, HEIGHT - 110 - top),
                   size=15, colour=ui.ERROR_COLOUR if limit and self.count() > limit else ui.FAINT_TEXT_COLOUR)


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
        widgets.append(ui.Stepper((x, 412, w, 30), "Shield", self.get_shield, self.set_shield,
                                  0.05, 0.0, 1.0,
                                  lambda v: "Off" if v == 0 else f"-{percent(v)} tackles",
                                  enabled=has))
        widgets.append(Confirm((x, 452, w, 30), "Delete action", "Click again to delete",
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

    def get_shield(self):
        return self.action.get("shield", 0.0) if self.has_action() else 0.0

    def set_shield(self, amount):
        if amount > 0:
            self.action["shield"] = amount
        else:
            self.action.pop("shield", None)
        settings.changed()

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
        self.draw_messages(screen, pygame.Rect(x, 490, 270, HEIGHT - 496))

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
    leave_label = "Main menu"       # the pause menu's button for leaving the match

    def __init__(self, app):
        super().__init__(app)
        self.restart()

    def restart(self):
        self.game = Game(Board(config.BOARD_COLS, config.BOARD_ROWS))
        self.ai = AIController()

    def full_time(self):
        """(choices, extra lines of text) for the full-time box."""
        return FULL_TIME_CHOICES, []

    def full_time_choice(self, choice):
        if choice == "play again":
            self.restart()
        elif choice == "main menu":
            self.app.pop()
        elif choice == "quit":
            self.app.quit()

    def ai_turn(self):
        return self.ai.controls(self.game.active_team) and not self.game.over

    def handle(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.push(PauseScreen(self.app, self))
            return True
        if event.type == pygame.MOUSEMOTION:
            # Hovering over an action button previews where it goes
            self.game.hover_action = None if self.ai_turn() else action_at_pixel(event.pos, self.game)
            return False
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        game = self.game
        if game.over:
            choices, lines = self.full_time()
            self.full_time_choice(full_time_button_at_pixel(event.pos, choices, len(lines)))
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
        if self.ai_turn():
            self.game.hover_action = None   # the computer's choice is on show
        self.ai.update(self.game)

    def draw(self, screen):
        self.app.renderer.draw(self.game, self.ai_turn(), self.ai.highlight,
                               self.full_time() if self.game.over else None)
        if not self.game.over:
            ui.text(screen, "Esc: menu", (10, 6), size=15, colour=ui.FAINT_TEXT_COLOUR)


class RankedGameScreen(GameScreen):
    """A ranked match: you (Home) against computer team `number` (Away), with
    the current match settings. At full time the result goes into
    app.ranking. Leaving or restarting before then changes nothing."""

    leave_label = "Rankings"

    def __init__(self, app, number):
        self.number = number
        self.outcome = None             # lines for the full-time box, once recorded
        super().__init__(app)

    def restart(self):
        self.game = Game(Board(config.BOARD_COLS, config.BOARD_ROWS))
        self.ai = AIController(controllers={config.HOME: "human", config.AWAY: "ai"})
        self.outcome = None

    def update(self):
        super().update()
        if self.game.over and self.outcome is None:
            ladder = self.app.ranking
            result = ranking_module.result_for(self.game.score, config.HOME)
            old_rating = ladder.rating
            points, before, after = ladder.record(self.number, result)
            verb = {"win": "beat", "draw": "drew with", "loss": "lost to"}[result]
            self.outcome = [f"You {verb} {ladder.team_name(self.number)}: {points:+} points",
                            f"Rating {old_rating} -> {ladder.rating}, #{before} -> #{after}"]
            if after == 1:
                self.outcome.append("You're #1!")

    def full_time(self):
        return ["rankings", "quit"], self.outcome or []

    def full_time_choice(self, choice):
        if choice == "rankings":
            self.app.pop()
        elif choice == "quit":
            self.app.quit()


# --- Rankings ---

def ladder(app):
    """app.ranking, made on first use and started again if its settings changed."""
    if getattr(app, "ranking", None) is None or app.ranking.out_of_date():
        app.ranking = ranking_module.Ranking()
    return app.ranking


def can_play_ranked():
    """(True, "") or (False, why not) for starting a ranked match."""
    error, _ = check_config()
    if error:
        return False, "Can't play yet: " + error
    if config.TURNS_PER_TEAM == 0:
        return False, ("Ranked matches need a full time: set Turns per team in "
                       "Match settings (it's No limit).")
    return True, ""


class RankingsScreen(Screen):
    """The ladder, #1 at the top. Scroll with the mouse wheel, the arrow and
    page keys, or the bar on the right."""

    title = "Rankings"
    TOP, ROW_H = 92, 34
    COLUMNS = {"place": 40, "name": 110, "rating": 330}
    VIEW_RECT = pygame.Rect(470, 0, 110, 28)       # x and size; y set per row
    CHALLENGE_RECT = pygame.Rect(592, 0, 140, 28)
    YOU_COLOUR = (255, 236, 160)
    STRIPE_COLOUR = (228, 228, 228)

    def __init__(self, app):
        super().__init__(app)
        ladder(app)
        self.table = pygame.Rect(30, self.TOP, WIDTH - 60, HEIGHT - 110 - self.TOP)
        self.bar = pygame.Rect(self.table.right - 12, self.table.top, 12, self.table.height)
        self.dragging_bar = False
        self.playable, self.why_not = True, ""
        self.widgets = [
            ui.Button(BACK_RECT, "Back", self.back),
            ui.Button((160, HEIGHT - 56, 200, 40), "Ranking settings",
                      lambda: app.push(RankingSettingsScreen(app))),
            Confirm((380, HEIGHT - 56, 200, 40), "Start again", "Click again to start again",
                    lambda: app.ranking.reset()),
        ]
        self.show_you()

    # Scrolling

    def rows(self):
        return ladder(self.app).rows()

    def max_scroll(self):
        return max(0, len(self.rows()) * self.ROW_H - self.table.height)

    def scroll_to(self, pixels):
        self.scroll = max(0, min(self.max_scroll(), int(pixels)))

    def show_you(self):
        """Scroll so your row is in the middle of the table."""
        you = ladder(self.app).place() - 1
        self.scroll = 0
        self.scroll_to(you * self.ROW_H - self.table.height // 2 + self.ROW_H // 2)

    def thumb(self):
        total = len(self.rows()) * self.ROW_H
        if total <= self.table.height:
            return None
        height = max(30, self.bar.height * self.table.height // total)
        top = self.bar.top + (self.bar.height - height) * self.scroll // self.max_scroll()
        return pygame.Rect(self.bar.left, top, self.bar.width, height)

    def drag_bar(self, y):
        thumb = self.thumb()
        if thumb is not None:
            share = (y - self.bar.top - thumb.height / 2) / max(1, self.bar.height - thumb.height)
            self.scroll_to(share * self.max_scroll())

    # Input

    def row_at(self, pos):
        """(row, which button: "view", "challenge" or None) under pos, or (None, None)."""
        if not self.table.collidepoint(pos) or pos[0] >= self.bar.left:
            return None, None
        index = (pos[1] - self.table.top + self.scroll) // self.ROW_H
        rows = self.rows()
        if not 0 <= index < len(rows):
            return None, None
        row = rows[index]
        y = self.table.top + index * self.ROW_H - self.scroll + (self.ROW_H - self.VIEW_RECT.height) // 2
        for name, rect in (("view", self.VIEW_RECT), ("challenge", self.CHALLENGE_RECT)):
            if rect.move(0, y).collidepoint(pos):
                return row, name
        return row, None

    def can_challenge(self, number):
        return number is not None and self.playable and ladder(self.app).can_challenge(number)

    def handle(self, event):
        if super().handle(event):
            return True
        if event.type == pygame.MOUSEWHEEL:
            self.scroll_to(self.scroll - event.y * self.ROW_H * 2)
            return True
        if event.type == pygame.KEYDOWN:
            page = self.table.height - self.ROW_H
            moves = {pygame.K_UP: -self.ROW_H, pygame.K_DOWN: self.ROW_H,
                     pygame.K_PAGEUP: -page, pygame.K_PAGEDOWN: page}
            if event.key in moves:
                self.scroll_to(self.scroll + moves[event.key])
            elif event.key == pygame.K_HOME:
                self.scroll_to(0)
            elif event.key == pygame.K_END:
                self.scroll_to(self.max_scroll())
            else:
                return False
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.bar.collidepoint(event.pos):
                self.dragging_bar = True
                self.drag_bar(event.pos[1])
                return True
            row, button = self.row_at(event.pos)
            if row is None:
                return False
            number = row[1]
            if button == "view":
                self.app.push(TeamViewScreen(self.app, number))
            elif button == "challenge" and self.can_challenge(number):
                self.app.push(RankedGameScreen(self.app, number))
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging_bar:
            self.dragging_bar = False
            return True
        if event.type == pygame.MOUSEMOTION and self.dragging_bar:
            self.drag_bar(event.pos[1])
            return True
        return False

    def update(self):
        self.playable, self.why_not = can_play_ranked()
        self.scroll_to(self.scroll)          # the table may have changed size

    # Drawing

    def draw(self, screen):
        super().draw(screen)
        board = ladder(self.app)
        header_y = self.TOP - 22
        for key, label in (("place", "#"), ("name", "Team"), ("rating", "Rating")):
            ui.text(screen, label, (self.COLUMNS[key], header_y), size=15, bold=True)
        screen.set_clip(self.table)
        for index, (place, number, name, rating) in enumerate(self.rows()):
            top = self.table.top + index * self.ROW_H - self.scroll
            if top + self.ROW_H < self.table.top or top > self.table.bottom:
                continue
            band = pygame.Rect(self.table.left, top, self.bar.left - self.table.left - 4, self.ROW_H)
            if number is None:
                pygame.draw.rect(screen, self.YOU_COLOUR, band)
            elif index % 2:
                pygame.draw.rect(screen, self.STRIPE_COLOUR, band)
            middle = top + self.ROW_H // 2
            you = number is None
            ui.text(screen, f"#{place}", (self.COLUMNS["place"], middle), anchor="midleft", bold=you)
            ui.text(screen, name, (self.COLUMNS["name"], middle), anchor="midleft", bold=you)
            ui.text(screen, str(rating), (self.COLUMNS["rating"], middle), anchor="midleft", bold=you)
            y = top + (self.ROW_H - self.VIEW_RECT.height) // 2
            ui.draw_box(screen, self.VIEW_RECT.move(0, y), "View team", *ui.colours(), size=15)
            if not you:
                ui.draw_box(screen, self.CHALLENGE_RECT.move(0, y), "Challenge",
                            *ui.colours(self.can_challenge(number)), size=15)
        screen.set_clip(None)
        pygame.draw.rect(screen, self.STRIPE_COLOUR, self.bar, border_radius=6)
        thumb = self.thumb()
        if thumb is not None:
            pygame.draw.rect(screen, config.MID_GREY, thumb, border_radius=6)

        if not self.playable:
            line, colour = self.why_not, ui.ERROR_COLOUR
        elif board.at_top():
            line, colour = (f"You're #1 with {board.rating} points! There's nobody left to "
                            f"challenge.", ui.TEXT_COLOUR)
        else:
            line, colour = (f"You're #{board.place()} of {board.size + 1} with {board.rating} "
                            f"points. Challenge any team above you.", ui.FAINT_TEXT_COLOUR)
        ui.wrapped(screen, line, pygame.Rect(30, self.table.bottom + 6, WIDTH - 60, 40),
                   size=15, colour=colour)


class TeamViewScreen(Screen):
    """A team's players and their actions, before you challenge them.
    number: the computer team's number, or None for your own team. Every
    computer team plays as the Away team in config; you're the Home team."""

    def __init__(self, app, number):
        super().__init__(app)
        self.number = number
        self.team = config.HOME if number is None else config.AWAY
        self.board = Board(config.BOARD_COLS, config.BOARD_ROWS)
        for tile in self.board:
            if tile.player is not None and tile.player.team != self.team:
                tile.player = None
        own = [tile for tile in self.board if tile.player is not None]
        self.selected = own[0] if own else None
        self.hovered = None
        self.playable, self.why_not = True, ""
        w, h = 150, 38
        y = HEIGHT - h - 8
        self.widgets = [ui.Button((20, y, 120, h), "Back", self.back)]
        if number is not None:
            self.widgets.append(ui.Button((WIDTH - w - 20, y, w, h), "Challenge", self.challenge,
                                          enabled=self.can_challenge))

    def can_challenge(self):
        return self.playable and ladder(self.app).can_challenge(self.number)

    def challenge(self):
        self.app.pop()                                  # back to the table after the match
        self.app.push(RankedGameScreen(self.app, self.number))

    def update(self):
        self.playable, self.why_not = can_play_ranked()

    def handle(self, event):
        if super().handle(event):
            return True
        if event.type == pygame.MOUSEMOTION:
            tile = tile_at_pixel(event.pos, self.board)
            self.hovered = tile if tile is not None and tile.player is not None else None
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            tile = tile_at_pixel(event.pos, self.board)
            if tile is not None and tile.player is not None:
                self.selected = tile
                return True
        return False

    def heading(self):
        board = ladder(self.app)
        if self.number is None:
            return f"Your team ({config.TEAM_NAMES[self.team]}) - #{board.place()}, {board.rating} points"
        place = next(row[0] for row in board.rows() if row[1] == self.number)
        return (f"#{place} {board.team_name(self.number)} - {board.team_rating(self.number)} points"
                f" (plays as {config.TEAM_NAMES[self.team]})")

    def player_text(self, tile):
        player = tile.player
        role = "Goalkeeper" if getattr(player, "role", None) == "goalkeeper" else "Outfield player"
        facing = "right" if player.facing == RIGHT else "left"
        names = ", ".join(f"{i}. {name}" for i, name in enumerate(player.actions, 1))
        return f"{role}, facing {facing}: {names}"

    def preview(self):
        """What each result would do to you, or why you can't challenge."""
        board = ladder(self.app)
        if self.number is None:
            return "Click a player to see their actions."
        if not self.playable:
            return self.why_not
        if not board.can_challenge(self.number):
            return ("You're #1: there's nobody left to challenge." if board.at_top()
                    else "This team is below you: you can only challenge teams above you.")
        parts = []
        for result, label in (("win", "Win"), ("draw", "Draw"), ("loss", "Lose")):
            points = board.change(self.number, result)
            place = board.place_after(self.number, result)
            where = f"stay #{place}" if place == board.place() else f"to #{place}"
            parts.append(f"{label} {points:+} points ({where})")
        return "  |  ".join(parts)

    def draw(self, screen):
        screen.fill(config.BACKGROUND_COLOUR)
        renderer = self.app.renderer
        for tile in self.board:
            border = None
            if tile is self.selected:
                border = (config.SELECTED_COLOUR, config.SELECTED_BORDER_WIDTH)
            elif tile is self.hovered:
                border = (config.MOVE_TARGET_COLOUR, config.BORDER_WIDTH)
            renderer.draw_tile(tile, border, self.board)
        ui.text(screen, self.heading(), (10, 4), bold=True)
        shown = self.hovered or self.selected
        if shown is not None:
            ui.wrapped(screen, self.player_text(shown),
                       pygame.Rect(20, config.UI_POSITION[1] + 4, WIDTH - 40, 48), size=15)
        ui.wrapped(screen, self.preview(), pygame.Rect(160, HEIGHT - 44, WIDTH - 360, 40), size=15,
                   colour=ui.ERROR_COLOUR if not self.playable and self.number is not None
                   else ui.TEXT_COLOUR)
        for widget in self.widgets:
            widget.draw(screen)


class FastStepper(ui.Stepper):
    """A stepper with extra big-step buttons:  label ...  [--] [-] value [+] [++]"""

    def __init__(self, rect, label, get, set, step, big_step, low=None, high=None, show=str):
        super().__init__(rect, label, get, set, step, low, high, show)
        self.big_step = big_step
        h, gap = self.rect.height, 4
        self.big_plus = pygame.Rect(self.rect.right - h, self.rect.top, h, h)
        self.plus = self.big_plus.move(-h - gap, 0)
        self.value_rect = pygame.Rect(self.plus.left - 90, self.rect.top, 90, h)
        self.minus = pygame.Rect(self.value_rect.left - h, self.rect.top, h, h)
        self.big_minus = self.minus.move(-h - gap, 0)

    def handle(self, event):
        for rect, direction in ((self.big_minus, -1), (self.big_plus, 1)):
            if ui.clicked(event, rect):
                step, self.step = self.step, self.big_step
                self._change(direction)
                self.step = step
                return True
        return super().handle(event)

    def draw(self, screen):
        super().draw(screen)
        value = self.get()
        big = self.show(self.big_step)
        ui.draw_box(screen, self.big_minus, "-" + big,
                    *ui.colours(self.low is None or value > self.low), size=14)
        ui.draw_box(screen, self.big_plus, "+" + big,
                    *ui.colours(self.high is None or value < self.high), size=14)


class RankingSettingsScreen(Screen):
    """How big the ladder is and how far results move you. Changing the
    number of teams, the top rating or the points per place starts the
    ladder again."""

    title = "Ranking settings"

    def __init__(self, app):
        super().__init__(app)
        x, w, h = 60, 680, 34
        rows = iter(range(80, 600, 44))

        def setter(name):
            def set_value(value):
                setattr(config, name, value)
                settings.changed()
            return set_value

        def points(v):
            return f"{v:g}"

        def stepper(label, name, step, big, low, high, show=points):
            return FastStepper((x, next(rows), w, h), label, lambda: getattr(config, name),
                               setter(name), step, big, low, high, show)

        self.widgets = [
            stepper("Computer teams (starts the ladder again)", "RANKING_TEAMS", 1, 10, 1, 100),
            stepper("Top team's rating (starts again)", "RANKING_TOP_RATING", 50, 500, 0, 10000),
            stepper("Points between places (starts again)", "RANKING_POINTS_PER_PLACE", 1, 10, 1, 500),
            stepper("Win: share of the rating gap gained", "RANKING_WIN_SHARE", 0.05, 0.25, 0.0, 2.0,
                    percent),
            stepper("Win: fewest points gained", "RANKING_MIN_WIN", 1, 10, 0, 1000),
            stepper("Draw: share of the rating gap gained", "RANKING_DRAW_SHARE", 0.05, 0.25, 0.0, 1.0,
                    percent),
            stepper("Loss: most points lost (to a team just above)", "RANKING_LOSS_MAX", 1, 10, 0, 1000),
            stepper("Loss: rating gap that halves the loss", "RANKING_LOSS_HALF_GAP", 10, 100, 0, 10000),
            ui.Button(BACK_RECT, "Back", self.back),
        ]

    def examples(self):
        """What results would do against teams some places above you, as text.
        Places are points / points between places (from the bottom you can't
        actually drop any lower)."""
        sample = ranking_module.Ranking()
        size, per_place = sample.size, sample.per_place
        lines = ["Against a team this far above you (starting from the bottom):"]
        for n in dict.fromkeys((size, max(1, size - 19), 1)):
            above = size + 1 - n
            parts = []
            for result, label in (("win", "win"), ("draw", "draw"), ("loss", "lose")):
                points = sample.change(n, result)
                places = round(points / per_place)
                parts.append(f"{label} {points:+} pts ({places:+} place{'' if abs(places) == 1 else 's'})")
            lines.append(f"{above} place{'' if above == 1 else 's'} above: " + ",  ".join(parts))
        return "\n".join(lines)

    def draw(self, screen):
        super().draw(screen)
        ui.wrapped(screen, self.examples(), pygame.Rect(60, 438, WIDTH - 120, 100), size=15,
                   colour=ui.FAINT_TEXT_COLOUR)


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
            ui.Button(row(2), game_screen.leave_label, self.main_menu),
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
