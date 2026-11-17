import pygame

import config
from actions import validate_config
from ai import AIController
from board import Board
from game import Game
from render import Renderer, action_at_pixel, end_turn_button, tile_at_pixel


def handle_click(pos, game):
    """Send a click to whichever button or tile it landed on."""
    if end_turn_button().collidepoint(pos):
        game.end_turn()
        return
    action = action_at_pixel(pos, game)
    if action is not None:
        game.choose_action(action)
    else:
        game.click(tile_at_pixel(pos, game.board))


def main():
    validate_config()   # fail early with a clear message if the config is wrong

    pygame.init()
    screen = pygame.display.set_mode(config.SCREEN_SIZE)
    pygame.display.set_caption("Football Board Game Prototype")
    clock = pygame.time.Clock()

    renderer = Renderer(screen,
                        pygame.font.SysFont(config.FONT_NAME, config.FONT_SIZE),
                        pygame.font.SysFont(config.FONT_NAME, config.BUTTON_FONT_SIZE))
    game = Game(Board(config.BOARD_COLS, config.BOARD_ROWS))
    ai = AIController()

    running = True
    while running:
        ai_turn = ai.controls(game.active_team)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                    and not ai_turn):          # clicks are ignored while the AI plays
                handle_click(event.pos, game)

        ai.update(game)

        renderer.draw(game, ai_turn, ai.highlight)
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
