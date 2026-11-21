"""Changing config while the program runs, for the menu's editors.

The editors change the values in the config module directly, so the rest of
the game sees them straight away. Nothing is written to disk until
save_to_config() is called; revert() reloads config.py and drops every edit.
"""
import ast
import copy
import importlib
import json
import math
import os
import re
from collections import deque

import actions as actions_module
import ai
import config
import theme
from board import shift_top
from hex_math import hex_distance, neighbours, rectangle_coords, row_is_short, row_start

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")

# Every setting the menu can change, in the order they're written back.
EDITABLE = [
    "TURNS_PER_TEAM", "CONTROLLERS", "ACTIONS_PER_TURN", "AI_ENGINE", "AI_PLAN_SECONDS", "AI_THINK_SECONDS",
    "AI_MOVE_DELAY", "BOARD_COLS", "BOARD_ROWS", "KEEPER_BOX_RADIUS", "TACKLE_CHANCE",
    "START_POSSESSION_ACTIONS", "TACKLE_POSSESSION_ACTIONS", "RECEIVE_POSSESSION_ACTIONS",
    "INTERCEPT_CHANCE", "MAX_PLAYER_ACTIONS",
    "SHIFTED_ROWS",
    "KEEPER_SAVE_CHANCE", "SHOT_CHANCE", "ACTIONS", "DEFAULT_ACTIONS", "OUTFIELD_ACTIONS",
    "KEEPER_ACTIONS", "INITIAL_SETUP", "INITIAL_BALL",
    "DARK_MODE", "FULLSCREEN",
    "RANKING_TEAMS", "RANKING_TOP_RATING", "RANKING_POINTS_PER_PLACE", "RANKING_WIN_SHARE",
    "RANKING_MIN_WIN", "RANKING_DRAW_SHARE", "RANKING_LOSS_MAX", "RANKING_LOSS_HALF_GAP",
]

# Keys of an action, in the order they're written out
ACTION_KEY_ORDER = ["requires", "self", "ball", "lofted", "turn", "face_movement",
                    "shoot", "shield"]

_saved = {}   # EDITABLE values as they are in config.py


def _remember_saved():
    _saved.clear()
    _saved.update({name: copy.deepcopy(getattr(config, name)) for name in EDITABLE})


_remember_saved()


def has_unsaved_changes():
    return any(getattr(config, name) != _saved[name] for name in EDITABLE)


def changed():
    """Call after any edit, so caches built from the old values are dropped."""
    ai.clear_caches()


def revert():
    """Throw away every edit: reload config.py."""
    importlib.reload(config)
    actions_module.apply_config_defaults()
    theme.apply(fresh=True)
    _remember_saved()
    changed()


# --- Pitch geometry ---

def to_offset(coord):
    """Axial (q, r) -> (column, row), column counted from each row's left end."""
    q, r = coord
    return q - row_start(r, shift_top()), r


def from_offset(col, row):
    return col + row_start(row, shift_top()), row


def row_length(row, cols=None):
    """Tiles in a row: the shifted rows have one fewer, so the pitch is symmetric."""
    cols = config.BOARD_COLS if cols is None else cols
    return cols - row_is_short(row, shift_top())


def pitch_coords(cols=None, rows=None):
    cols = config.BOARD_COLS if cols is None else cols
    rows = config.BOARD_ROWS if rows is None else rows
    return rectangle_coords(cols, rows, shift_top())


def goals(cols=None, rows=None):
    """{"left": coord, "right": coord}, as board.Board works them out."""
    rows = config.BOARD_ROWS if rows is None else rows
    middle_row = sorted(c for c in pitch_coords(cols, rows) if c[1] == rows // 2)
    return {"left": middle_row[0], "right": middle_row[-1]}


def own_goal(team, cols=None, rows=None):
    attacked = config.ATTACKING_GOAL[team]
    return goals(cols, rows)["left" if attacked == "right" else "right"]


def centre_spot(cols=None, rows=None):
    """The middle tile of the middle row (the left of the two middle tiles
    when that row has an even number)."""
    cols = config.BOARD_COLS if cols is None else cols
    rows = config.BOARD_ROWS if rows is None else rows
    row = rows // 2
    return from_offset((row_length(row, cols) - 1) // 2, row)


def mirror(coord, cols=None):
    """The tile in the same place on the other half (left <-> right)."""
    col, row = to_offset(coord)
    return from_offset(row_length(row, cols) - 1 - col, row)


def nearest_free(coord, taken, cols=None, rows=None):
    """coord if it's on the board and free, else the closest tile that is."""
    board = set(pitch_coords(cols, rows))
    if coord not in board:
        coord = min(board, key=lambda c: (hex_distance(c, coord), c))
    seen, queue = {coord}, deque([coord])
    while queue:
        current = queue.popleft()
        if current not in taken:
            return current
        for n in sorted(neighbours(current)):
            if n in board and n not in seen:
                seen.add(n)
                queue.append(n)
    return None   # the board is full


def all_entries():
    """[(team, entry)] for every player in config.INITIAL_SETUP."""
    return [(team, entry) for team, entries in config.INITIAL_SETUP.items() for entry in entries]


# --- Pitch and formation edits ---

def stretch(index, old_count, new_count):
    """Scale an index about the middle, rounding halves towards the middle
    so mirror-image positions stay mirror images."""
    old_mid, new_mid = (old_count - 1) / 2, (new_count - 1) / 2
    x = new_mid + (index - old_mid) * (new_count - 1) / max(old_count - 1, 1)
    if x - math.floor(x) == 0.5:
        x = math.floor(x) if x > new_mid else math.ceil(x)
    return min(max(round(x), 0), new_count - 1)


def set_shifted_rows(value):
    """Switch which rows are shifted ("top" or "second"), moving each player to
    the matching place in their row (keepers to their goal, the ball to the
    centre spot), so the formation keeps its shape and stays symmetric."""
    if value == config.SHIFTED_ROWS:
        return
    old = [(team, entry, to_offset(entry["position"]), row_length(entry["position"][1]))
           for team, entry in all_entries()]
    config.SHIFTED_ROWS = value
    taken = set()
    for team, entry, _, _ in old:
        if entry.get("role") == "goalkeeper":
            entry["position"] = nearest_free(own_goal(team), taken)
            taken.add(entry["position"])
    for team, entry, (col, row), old_length in old:
        if entry.get("role") != "goalkeeper":
            new_col = stretch(col, old_length, row_length(row))
            entry["position"] = nearest_free(from_offset(new_col, row), taken)
            taken.add(entry["position"])
    config.INITIAL_BALL = centre_spot()
    changed()


def resize_pitch(cols, rows):
    """Change the pitch size, stretching the starting positions to fit.
    Keepers go to their goal tile and the ball to the centre spot."""
    old_cols, old_rows = config.BOARD_COLS, config.BOARD_ROWS

    def scale(coord):
        col, row = to_offset(coord)
        new_row = stretch(row, old_rows, rows)
        new_col = stretch(col, row_length(row, old_cols), row_length(new_row, cols))
        return from_offset(new_col, new_row)

    taken = set()
    keepers = [(t, e) for t, e in all_entries() if e.get("role") == "goalkeeper"]
    for team, entry in keepers:
        entry["position"] = nearest_free(own_goal(team, cols, rows), taken, cols, rows)
        taken.add(entry["position"])
    for team, entry in all_entries():
        if entry.get("role") != "goalkeeper":
            entry["position"] = nearest_free(scale(entry["position"]), taken, cols, rows)
            taken.add(entry["position"])
    config.BOARD_COLS, config.BOARD_ROWS = cols, rows
    config.INITIAL_BALL = centre_spot(cols, rows)
    changed()


def mirror_home_to_away():
    """Make Away's formation a mirror image of Home's (same actions and roles)."""
    away = []
    for entry in config.INITIAL_SETUP[config.HOME]:
        new = dict(entry)
        new["position"] = mirror(entry["position"])
        if "facing" in entry:
            new["facing"] = config.LEFT if entry["facing"] == config.RIGHT else config.RIGHT
        away.append(new)
    config.INITIAL_SETUP[config.AWAY] = away
    changed()


def player_at(coord):
    """(team, entry) of the player starting on coord, or None."""
    for team, entry in all_entries():
        if entry["position"] == coord:
            return team, entry
    return None


def add_player(team):
    """Add an outfield player on the free tile nearest the middle of team's half."""
    goal = own_goal(team)
    spot = centre_spot()
    middle = ((goal[0] + spot[0]) // 2, spot[1])
    taken = {entry["position"] for _, entry in all_entries()}
    coord = nearest_free(middle, taken)
    if coord is None:
        return None
    entry = {"actions": config.OUTFIELD_ACTIONS, "position": coord}
    config.INITIAL_SETUP[team].append(entry)
    changed()
    return entry


def remove_player(entry):
    for entries in config.INITIAL_SETUP.values():
        if any(e is entry for e in entries):
            entries[:] = [e for e in entries if e is not entry]
    changed()


def set_keeper(team, entry, keeper):
    """Make entry team's goalkeeper (any other keeper on the team becomes
    outfield) or an outfield player. Keepers get KEEPER_ACTIONS, outfield
    players OUTFIELD_ACTIONS."""
    if keeper:
        for other in config.INITIAL_SETUP[team]:
            if other.get("role") == "goalkeeper":
                other.pop("role")
                other["actions"] = config.OUTFIELD_ACTIONS
        entry["role"] = "goalkeeper"
        entry["actions"] = config.KEEPER_ACTIONS
    else:
        entry.pop("role", None)
        entry["actions"] = config.OUTFIELD_ACTIONS
    changed()


# --- One player's actions ---

def toggle_player_action(entry, name):
    """Give a player an action (at the end of their buttons) or take it away.
    The player gets their own list, so a shared set like OUTFIELD_ACTIONS and
    the other players using it don't change."""
    names = list(entry["actions"])
    if name in names:
        names.remove(name)
    else:
        names.append(name)
    entry["actions"] = names
    changed()


def use_action_set(entry, list_name):
    """Put a player back on a shared set: "OUTFIELD_ACTIONS" or "KEEPER_ACTIONS"."""
    entry["actions"] = getattr(config, list_name)
    changed()


def copy_actions_to_teammates(team, entry):
    """Give every other outfield player on the team the same actions as entry
    (keepers keep theirs). Each gets their own copy."""
    for other in config.INITIAL_SETUP[team]:
        if other is not entry and other.get("role") != "goalkeeper":
            other["actions"] = list(entry["actions"])
    changed()


def action_set_name(entry):
    """"Outfield set", "Keeper set" or "Own set" for a player's actions."""
    for list_name, label in (("OUTFIELD_ACTIONS", "Outfield set"), ("KEEPER_ACTIONS", "Keeper set")):
        if entry["actions"] is getattr(config, list_name) or entry["actions"] == getattr(config, list_name):
            return label
    return "Own set"


# --- Action edits ---

ACTION_LISTS = ["OUTFIELD_ACTIONS", "KEEPER_ACTIONS", "DEFAULT_ACTIONS"]


def _name_lists():
    """Every list of action names: the three in config plus any player's own list."""
    lists = [getattr(config, name) for name in ACTION_LISTS]
    for _, entry in all_entries():
        if not any(entry["actions"] is existing for existing in lists):
            lists.append(entry["actions"])
    return lists


def new_action():
    """Add an empty action with an unused name and return the name."""
    name, n = "new action", 1
    while name in config.ACTIONS:
        n += 1
        name = f"new action {n}"
    config.ACTIONS[name] = {}
    changed()
    return name


def rename_action(old, new):
    """Rename an action everywhere it's used. Returns an error message or None."""
    new = new.strip()
    if not new:
        return "A name can't be empty"
    if new != old and new in config.ACTIONS:
        return f"There's already an action called {new!r}"
    config.ACTIONS = {(new if name == old else name): action
                      for name, action in config.ACTIONS.items()}
    for names in _name_lists():
        names[:] = [new if name == old else name for name in names]
    changed()
    return None


def delete_action(name):
    config.ACTIONS.pop(name, None)
    for names in _name_lists():
        names[:] = [n for n in names if n != name]
    changed()


def offsets_of(action, key):
    """An action's "self" or "ball" offsets as a list ([] for none or "with_player")."""
    value = action.get(key)
    if value is None or value == "with_player":
        return []
    return list(value) if isinstance(value, list) else [value]


def set_offsets(action, key, offsets):
    """Store offsets the way config.py writes them: a single offset on its
    own, several as a list. No "self" offsets removes the key; no "ball"
    offsets leaves an empty list (the action can't be used until it has some)."""
    if len(offsets) == 1:
        action[key] = offsets[0]
    elif offsets or key == "ball":
        action[key] = list(offsets)
    else:
        action.pop(key, None)
    changed()


# --- Writing config.py ---

TEAM_NAMES_IN_SOURCE = {config.HOME: "HOME", config.AWAY: "AWAY"}


def lit(value):
    """Python source for a plain value, in config.py's style."""
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, float):
        return repr(round(value, 4))
    if isinstance(value, int):
        return repr(value)
    if isinstance(value, tuple):
        return "(" + ", ".join(lit(v) for v in value) + ("," if len(value) == 1 else "") + ")"
    if isinstance(value, list):
        return "[" + ", ".join(lit(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{lit(k)}: {lit(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"Can't write {value!r} to config.py")


def team_key(team):
    return TEAM_NAMES_IN_SOURCE.get(team, lit(team))


def facing_name(facing):
    return "RIGHT" if facing == config.RIGHT else "LEFT"


def wrap_list(prefix, items, width=95):
    """`prefix[...]` with the items wrapped onto continuation lines."""
    lines, line = [], prefix + "["
    indent = " " * len(line)
    for i, item in enumerate(items):
        text = lit(item) + (", " if i < len(items) - 1 else "")
        if len(line + text.rstrip()) > width and line.strip() not in ("", prefix.strip() + "["):
            lines.append(line.rstrip())
            line = indent
        line += text
    lines.append(line + "]")
    return "\n".join(lines)


def comments_by_key(old_lines):
    """({key source text: trailing comment}, {key source text: [comment lines
    above it]}) for the top-level entries (4-space indent) of a multi-line
    dict, so rewriting it keeps their comments."""
    trailing, above, pending = {}, {}, []
    for line in old_lines:
        if line.startswith("    #"):
            pending.append(line)
            continue
        match = re.match(r'^    (\S[^:#]*?)\s*:', line)
        if match and not line.startswith("     "):
            key = match.group(1)
            comment = re.search(r'(#[^"]*)$', line)
            if comment:
                trailing[key] = comment.group(1)
            if pending:
                above[key] = pending
        if not line.startswith("     "):
            pending = []
    return trailing, above


def with_comment(line, comment, column=32):
    if not comment:
        return line
    return line + " " * max(2, column - len(line)) + comment


def format_chance_table(name, table, old_lines):
    comments, _ = comments_by_key(old_lines)
    lines = [f"{name} = {{"]
    for key, value in table.items():
        lines.append(with_comment(f"    {lit(key)}: {lit(value)},", comments.get(lit(key)), 14))
    return "\n".join(lines + ["}"])


def format_actions(old_lines):
    comments, above = comments_by_key(old_lines)
    lines = ["ACTIONS = {"]
    for name, action in config.ACTIONS.items():
        lines += above.get(lit(name), [])
        lines.append(with_comment(f"    {lit(name)}: {{", comments.get(lit(name))))
        keys = [k for k in ACTION_KEY_ORDER if k in action] + \
               [k for k in action if k not in ACTION_KEY_ORDER]
        for key in keys:
            lines.append(f"        {lit(key)}: {lit(action[key])},")
        lines.append("    },")
    return "\n".join(lines + ["}"])


def actions_source(names):
    for list_name in ("OUTFIELD_ACTIONS", "KEEPER_ACTIONS"):
        if names is getattr(config, list_name) or names == getattr(config, list_name):
            return list_name
    return lit(list(names))


def format_setup(old_lines):
    # Keep each player's comment if they still start where they did
    comments = {}
    for line in old_lines:
        match = re.search(r'"position":\s*(\(\s*-?\d+\s*,\s*-?\d+\s*\)).*?(#.*)$', line)
        if match:
            comments[ast.literal_eval(match.group(1))] = match.group(2)
    team_comments, _ = comments_by_key(old_lines)
    lines = ["INITIAL_SETUP = {"]
    for team, entries in config.INITIAL_SETUP.items():
        lines.append(with_comment(f"    {team_key(team)}: [", team_comments.get(team_key(team)), 62))
        for entry in entries:
            parts = [f'"actions": {actions_source(entry["actions"])}',
                     f'"position": {lit(tuple(entry["position"]))}']
            if "role" in entry:
                parts.append(f'"role": {lit(entry["role"])}')
            if "facing" in entry:
                parts.append(f'"facing": {facing_name(entry["facing"])}')
            extra = [f"{lit(k)}: {lit(v)}" for k, v in entry.items()
                     if k not in ("actions", "position", "role", "facing")]
            line = "        {" + ", ".join(parts + extra) + "},"
            lines.append(with_comment(line, comments.get(tuple(entry["position"])), 62))
        lines.append("    ],")
    return "\n".join(lines + ["}"])


def format_setting(name, old_lines):
    value = getattr(config, name)
    if name == "ACTIONS":
        return format_actions(old_lines)
    if name == "INITIAL_SETUP":
        return format_setup(old_lines)
    if name in ("TACKLE_CHANCE", "SHOT_CHANCE", "KEEPER_SAVE_CHANCE"):
        return format_chance_table(name, value, old_lines)
    if name in ("CONTROLLERS", "ACTIONS_PER_TURN"):
        return f"{name} = {{" + ", ".join(f"{team_key(t)}: {lit(c)}"
                                          for t, c in value.items()) + "}"
    if name in ACTION_LISTS:
        return wrap_list(f"{name} = ", list(value))
    return f"{name} = {lit(value)}"


def save_to_config(path=None):
    """Write every edited setting back into config.py, replacing just those
    assignments so the rest of the file (comments included) stays as it is.
    The previous file is kept as config.py.bak. Returns the names written."""
    path = path or CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        source = f.read()
    on_disk = {}
    exec(compile(source, path, "exec"), on_disk)
    names = [n for n in EDITABLE if on_disk.get(n) != getattr(config, n)]
    if not names:
        _remember_saved()
        return []

    lines = source.split("\n")
    spans = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
            spans[node.targets[0].id] = (node.lineno - 1, node.end_lineno)
    # Replace from the bottom up so earlier line numbers stay right
    for name in sorted(spans, key=lambda n: spans[n][0], reverse=True):
        start, end = spans[name]
        old = lines[start:end]
        # Keep a trailing comment on a one-line assignment (e.g. BOARD_COLS = 13  # ...)
        comment = ""
        if end - start == 1 and "#" in old[0]:
            comment = old[0][old[0].index("#"):]
        new = format_setting(name, old)
        if comment and "\n" not in new:
            new = with_comment(new, comment, 22)
        lines[start:end] = new.split("\n")
    missing = [n for n in names if n not in spans]
    if missing:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "# Added by the in-game menu"] + [format_setting(n, []) for n in missing] + [""]

    new_source = "\n".join(lines)
    check = {}
    exec(compile(new_source, path, "exec"), check)   # never write a file that doesn't load
    for name in names:
        if check[name] != getattr(config, name):
            raise RuntimeError(f"Writing {name} to config.py didn't round-trip")
    with open(path + ".bak", "w", encoding="utf-8") as f:
        f.write(source)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_source)
    _remember_saved()
    return names
