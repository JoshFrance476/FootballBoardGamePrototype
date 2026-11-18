import pygame

import config
from actions import validate_config
from menu import MainMenu
from render import Renderer


class App:
    """Runs the window and a stack of screens (menu.py): the top screen gets
    the input and is drawn; Esc or Back pops it."""

    def __init__(self, screen):
        self.screen = screen
        self.renderer = Renderer(screen,
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

    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif self.stack:
                    self.stack[-1].handle(event)
            if not self.running:
                break
            top = self.stack[-1]
            top.update()
            if self.running and self.stack:
                self.stack[-1].draw(self.screen)
                pygame.display.flip()
            clock.tick(60)


def main():
    validate_config()   # fail early with a clear message if config.py is wrong

    pygame.init()
    screen = pygame.display.set_mode(config.SCREEN_SIZE)
    pygame.display.set_caption("Football Board Game Prototype")
    app = App(screen)
    app.push(MainMenu(app))
    app.run()
    pygame.quit()


if __name__ == "__main__":
    main()
