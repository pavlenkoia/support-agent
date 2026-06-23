from pathlib import Path


def test_dockerfiles_copy_app_and_migrations_before_uv_sync() -> None:
    for path in [
        Path('deploy/compose/app.Dockerfile'),
        Path('deploy/compose/worker.Dockerfile'),
    ]:
        text = path.read_text()
        assert text.index('COPY app ./app') < text.index('RUN uv sync --no-dev')
        assert text.index('COPY migrations ./migrations') < text.index('RUN uv sync --no-dev')
        assert 'COPY pyproject.toml README.md alembic.ini ./' in text
