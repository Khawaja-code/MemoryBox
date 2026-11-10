"""Turn pynput key/button objects into plain strings for the database, and back."""


def key_to_str(key):
    """'a' for printable keys, 'Key.enter' for special keys, 'vk:NN' for the rest."""
    name = getattr(key, "name", None)          # pynput.keyboard.Key members
    if name:
        return f"Key.{name}"
    char = getattr(key, "char", None)          # pynput.keyboard.KeyCode with a character
    if char is not None:
        return char
    vk = getattr(key, "vk", None)
    return f"vk:{vk}" if vk is not None else str(key)


def str_to_key(text):
    from pynput.keyboard import Key, KeyCode
    if text.startswith("Key."):
        return getattr(Key, text[4:])
    if text.startswith("vk:"):
        return KeyCode.from_vk(int(text[3:]))
    return text                                 # Controller.press accepts a 1-char string


def button_to_str(button):
    return str(button)                          # 'Button.left'


def str_to_button(text):
    from pynput.mouse import Button
    return getattr(Button, text.split(".", 1)[-1])
