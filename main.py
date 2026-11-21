import pygame

import config
import theme
import ui
from actions import validate_config
from menu import MainMenu
from render import Renderer


MOUSE_EVENTS = (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION)


class App:
    """Runs the window and a stack of screens (menu.py): the top screen gets
    the input and is drawn; Esc or Back pops it.

    Everything is drawn on self.screen, a config.SCREEN_SIZE surface, which
    is then copied to the middle of the window. Windowed, that's the whole
    window; fullscreen (F11, or the menu buttons) it stays the same size with
    black all round, and mouse positions are moved to match."""

    def __init__(self, window):
        self.window = window
        self.screen = pygame.Surface(config.SCREEN_SIZE)
        self.offset = (0, 0)
        self.renderer = Renderer(self.screen,
                                 pygame.font.SysFont(config.FONT_NAME, config.FONT_SIZE),
                                 pygame.font.SysFont(config.FONT_NAME, config.BUTTON_FONT_SIZE))
        self.stack = []
        self.running = True

    def push(self, screen):
        self.stack.append(screen)

    def pop(self):
        self.stack.pop()
        if not self.stack:
            self.running = False

    def quit(self):
        self.running = False

    def set_fullscreen(self, on):
        """Fullscreen with the game at its normal size in the middle, black
        around it; or back to a normal window."""
        config.FULLSCREEN = bool(on)
        if on:
            self.window = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.window = pygame.display.set_mode(config.SCREEN_SIZE)
        self.place()

    def toggle_fullscreen(self):
        self.set_fullscreen(not config.FULLSCREEN)

    def place(self):
        """Centre the game in the window."""
        width, height = self.window.get_size()
        self.offset = ((width - config.SCREEN_SIZE[0]) // 2, (height - config.SCREEN_SIZE[1]) // 2)
        ui.MOUSE_OFFSET = self.offset

    def translate(self, event):
        """The event with its mouse position moved from the window to the game."""
        if event.type in MOUSE_EVENTS and self.offset != (0, 0):
            x, y = event.pos
            return pygame.event.Event(event.type, {**event.dict,
                                                   "pos": (x - self.offset[0], y - self.offset[1])})
        return event

    def show(self):
        """Copy the game onto the window, black all round it."""
        if self.window is not self.screen:
            self.window.fill((0, 0, 0))
            self.window.blit(self.screen, self.offset)
        pygame.display.flip()

    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                    self.toggle_fullscreen()
                elif self.stack:
                    self.stack[-1].handle(self.translate(event))
            if not self.running:
                break
            top = self.stack[-1]
            top.update()
            if self.running and self.stack:
                self.stack[-1].draw(self.screen)
                self.show()
            clock.tick(60)


def main():
    validate_config()   # fail early with a clear message if config.py is wrong
    theme.apply(fresh=True)   # dark mode, if config.DARK_MODE is on

    pygame.init()
    screen = pygame.display.set_mode(config.SCREEN_SIZE)
    pygame.display.set_caption("Football Board Game Prototype")
    app = App(screen)
    if config.FULLSCREEN:
        app.set_fullscreen(True)
    app.push(MainMenu(app))
    app.run()
    pygame.quit()


if __name__ == "__main__":
    main()
