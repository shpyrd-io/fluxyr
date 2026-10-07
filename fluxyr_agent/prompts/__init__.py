from pathlib import Path


def load_prompt(name):
    return (Path(__file__).parent / f"{name}.md").read_text()
