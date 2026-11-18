"""Small widget kit for the menus: buttons, steppers, option pickers,
toggles and a text box. Each widget has a rect, handle(event) (True if it
used the event) and draw(screen).

Values are read and written through get/set functions, so a widget always
shows the current setting even if something else changed it.
"""
import pygame

import config

TITLE_SIZE = 40
TEXT_COLOUR = config.BLACK
FAINT_TEXT_COLOUR = config.MID_GREY
ERROR_COLOUR = (190, 0, 0)
PANEL_COLOUR = (235, 235, 235)
ROW_HEIGHT = 34

_fonts = {}


def font(size=None, bold=False):
    """Cached font: default size is config.BUTTON_FONT_SIZE."""
    size = size or config.BUTTON_FONT_SIZE
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = pygame.font.SysFont(config.FONT_NAME, size, bold=bold)
    return _fonts[key]


def text(screen, string, pos, size=None, colour=TEXT_COLOUR, anchor="topleft", bold=False):
    """Draw a line of text; returns its rect."""
    surface = font(size, bold).render(string, True, colour)
    rect = surface.get_rect(**{anchor: pos})
    screen.blit(surface, rect)
    return rect


def wrapped(screen, string, rect, size=None, colour=TEXT_COLOUR):
    """Draw text word-wrapped inside rect (lines that don't fit are dropped)."""
    f = font(size)
    y = rect.top
    for paragraph in string.split("\n"):
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if f.size(trial)[0] > rect.width and line:
                if y + f.get_linesize() > rect.bottom:
                    return
                screen.blit(f.render(line, True, colour), (rect.left, y))
                y += f.get_linesize()
                line = word
            else:
                line = trial
        if line:
            if y + f.get_linesize() > rect.bottom:
                return
            screen.blit(f.render(line, True, colour), (rect.left, y))
            y += f.get_linesize()


def draw_box(screen, rect, label, fill, text_colour, size=None):
    pygame.draw.rect(screen, fill, rect, border_radius=config.BUTTON_RADIUS)
    f = font(size)
    # Shrink the label if it's too wide
    while f.size(label)[0] > rect.width - 2 * config.BUTTON_PADDING and f.get_height() > 10:
        size = (size or config.BUTTON_FONT_SIZE) - 1
        f = font(size)
    surface = f.render(label, True, text_colour)
    screen.blit(surface, surface.get_rect(center=rect.center))


def colours(enabled=True, active=False):
    if not enabled:
        return config.BUTTON_DISABLED_COLOUR, config.BUTTON_DISABLED_TEXT_COLOUR
    if active:
        return config.BUTTON_ACTIVE_COLOUR, config.BUTTON_ACTIVE_TEXT_COLOUR
    return config.BUTTON_COLOUR, config.BUTTON_TEXT_COLOUR


def clicked(event, rect):
    return (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
            and rect.collidepoint(event.pos))


class Button:
    def __init__(self, rect, label, on_click, enabled=None, active=None, size=None):
        self.rect = pygame.Rect(rect)
        self.label = label            # a string, or a function returning one
        self.on_click = on_click
        self.enabled = enabled or (lambda: True)
        self.active = active or (lambda: False)
        self.size = size

    def handle(self, event):
        if clicked(event, self.rect) and self.enabled():
            self.on_click()
            return True
        return False

    def draw(self, screen):
        label = self.label() if callable(self.label) else self.label
        draw_box(screen, self.rect, label, *colours(self.enabled(), self.active()), self.size)


class Stepper:
    """label ......  [-]  value  [+]"""

    def __init__(self, rect, label, get, set, step=1, low=None, high=None, show=str,
                 enabled=None):
        self.rect = pygame.Rect(rect)
        self.label, self.get, self.set = label, get, set
        self.step, self.low, self.high, self.show = step, low, high, show
        self.enabled = enabled or (lambda: True)
        h = self.rect.height
        self.plus = pygame.Rect(self.rect.right - h, self.rect.top, h, h)
        self.value_rect = pygame.Rect(self.plus.left - 90, self.rect.top, 90, h)
        self.minus = pygame.Rect(self.value_rect.left - h, self.rect.top, h, h)

    def _change(self, direction):
        value = self.get() + direction * self.step
        if isinstance(self.step, float):
            value = round(value, 4)
        if self.low is not None:
            value = max(self.low, value)
        if self.high is not None:
            value = min(self.high, value)
        if value != self.get():
            self.set(value)

    def handle(self, event):
        if not self.enabled():
            return False
        if clicked(event, self.minus):
            self._change(-1)
            return True
        if clicked(event, self.plus):
            self._change(1)
            return True
        return False

    def draw(self, screen):
        enabled = self.enabled()
        colour = TEXT_COLOUR if enabled else FAINT_TEXT_COLOUR
        text(screen, self.label, (self.rect.left, self.rect.centery), colour=colour,
             anchor="midleft")
        value = self.get()
        draw_box(screen, self.minus, "-", *colours(enabled and (self.low is None or value > self.low)))
        draw_box(screen, self.plus, "+", *colours(enabled and (self.high is None or value < self.high)))
        text(screen, self.show(value), self.value_rect.center, colour=colour, anchor="center")


class Choice:
    """label ......  [option][option][option], the current one highlighted."""

    def __init__(self, rect, label, options, get, set, option_width=90, enabled=None):
        self.rect = pygame.Rect(rect)
        self.label, self.options, self.get, self.set = label, options, get, set
        self.enabled = enabled or (lambda: True)
        gap = 4
        x = self.rect.right - len(options) * option_width - (len(options) - 1) * gap
        self.buttons = []
        for value, name in options:
            self.buttons.append((value, name, pygame.Rect(x, self.rect.top, option_width,
                                                          self.rect.height)))
            x += option_width + gap

    def handle(self, event):
        if not self.enabled():
            return False
        for value, _, rect in self.buttons:
            if clicked(event, rect):
                if value != self.get():
                    self.set(value)
                return True
        return False

    def draw(self, screen):
        enabled = self.enabled()
        if self.label:
            text(screen, self.label, (self.rect.left, self.rect.centery),
                 colour=TEXT_COLOUR if enabled else FAINT_TEXT_COLOUR, anchor="midleft")
        current = self.get()
        for value, name, rect in self.buttons:
            draw_box(screen, rect, name, *colours(enabled, value == current))


class Toggle(Button):
    """A button that's highlighted while its setting is on; click to flip it."""

    def __init__(self, rect, label, get, set, enabled=None, size=None):
        super().__init__(rect, label, lambda: set(not get()), enabled, get, size)


class TextBox:
    """Click to edit; Enter (or clicking elsewhere) commits, Esc cancels."""

    def __init__(self, rect, get, on_submit):
        self.rect = pygame.Rect(rect)
        self.get, self.on_submit = get, on_submit
        self.editing = False
        self.draft = ""

    def handle(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                if not self.editing:
                    self.editing, self.draft = True, self.get()
                return True
            if self.editing:
                self.commit()
            return False
        if not self.editing:
            return False
        if event.type == pygame.TEXTINPUT:
            self.draft += event.text
            return True
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.commit()
            elif event.key == pygame.K_ESCAPE:
                self.editing = False
            elif event.key == pygame.K_BACKSPACE:
                self.draft = self.draft[:-1]
            return True
        return False

    def commit(self):
        self.editing = False
        if self.draft != self.get():
            self.on_submit(self.draft)

    def draw(self, screen):
        pygame.draw.rect(screen, config.WHITE, self.rect, border_radius=4)
        pygame.draw.rect(screen, config.BUTTON_ACTIVE_COLOUR if self.editing else config.MID_GREY,
                         self.rect, 2, border_radius=4)
        shown = self.draft + "|" if self.editing else self.get()
        text(screen, shown, (self.rect.left + 8, self.rect.centery), anchor="midleft")
