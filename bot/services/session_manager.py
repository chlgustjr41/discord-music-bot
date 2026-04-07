import random
import string
from config import SESSION_CODE_LENGTH


def generate_session_code(length: int = SESSION_CODE_LENGTH) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))
