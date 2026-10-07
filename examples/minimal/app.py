from fluxyr import Fluxyr
from sqlalchemy import text

app = Fluxyr(__name__)


@app.tool(parallel_safe=True, side_effecting=False)
def server_time() -> dict:
    """Read the database server's current timestamp."""
    with app.db.transaction() as db:
        value = db.scalar(text('SELECT CURRENT_TIMESTAMP'))
    return {'timestamp': str(value)}


@app.get('/api/example/time')
def time_endpoint():
    return server_time()


if __name__ == '__main__':
    app.run()
