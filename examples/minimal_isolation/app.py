"""Single-agent application; configuration comes from the environment."""

from fluxyr import Fluxyr

app = Fluxyr(__name__)


if __name__ == "__main__":
    app.run()
