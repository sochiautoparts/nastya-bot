"""FSM States для многошаговых диалогов Nastya Bot."""
from aiogram.fsm.state import State, StatesGroup


class DialogStates(StatesGroup):
    """Состояния диалога для разных сценариев."""
    idle = State()                    # Ожидание команды
    waiting_for_image = State()       # Ожидание изображения для анализа
    waiting_for_video = State()       # Ожидание видео для анализа
    waiting_for_link = State()        # Ожидание ссылки
    chatting = State()                # Режим свободного чата
