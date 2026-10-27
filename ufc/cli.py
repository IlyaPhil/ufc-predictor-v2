"""
Точка входа пайплайна сбора данных: python -m ufc.cli <команда>

Сбор снимков (collect) и загрузка в БД (load-snapshots) разделены:
collect не трогает БД и может работать в облаке, загрузка выполняется локально
"""
import subprocess

import typer

from ufc import checks, snapshots
from ufc.config import SNAPSHOT_DIR
from ufc.db import connect, init_db, sync_run
from ufc.http import PoliteSession
from ufc.sources import bestfightodds, espn, ufcstats_mirror

app = typer.Typer(help="Сбор данных о боях UFC", no_args_is_help=True)

COLLECTORS = {"espn": espn.collect, "bestfightodds": bestfightodds.collect}


def _db():
    conn = connect()
    init_db(conn)
    return conn


def _sync_history() -> None:
    conn = _db()
    with sync_run(conn, "sync-history") as run:
        details = ufcstats_mirror.sync(conn, PoliteSession())
        run["details"] = f"{details} | checks: {checks.summary(checks.run_checks(conn))}"
    typer.echo(f"[sync-history] {run['details']}")


def _collect() -> list[str]:
    """
    Снимает все источники; ошибка одного не мешает остальным. Возвращает список упавших
    """
    failed = []
    session = PoliteSession()
    for source, fn in COLLECTORS.items():
        try:
            captured_at, rows = fn(session)
            path = snapshots.write_snapshot(source, captured_at, rows)
            typer.echo(f"[collect {source}] rows={len(rows)} -> {path}")
        except Exception as exc:
            failed.append(source)
            typer.echo(f"[collect {source}] ERROR: {exc!r}", err=True)
    return failed


def _pull_snapshots() -> None:
    """
    Подтягивает свежие снимки из GitHub, если data/snapshots — рабочая папка ветки snapshots
    """
    if not (SNAPSHOT_DIR / ".git").exists():
        typer.echo(f"[pull-snapshots] {SNAPSHOT_DIR} не подключена к ветке snapshots, пропускаю")
        return
    subprocess.run(["git", "-C", str(SNAPSHOT_DIR), "pull", "--ff-only", "--quiet"], check=True)
    typer.echo("[pull-snapshots] ok")


def _load_snapshots() -> None:
    conn = _db()
    with sync_run(conn, "load-snapshots") as run:
        run["details"] = snapshots.load(conn)
    typer.echo(f"[load-snapshots] {run['details']}")


@app.command("sync-history")
def sync_history():
    """Перезалить историю боев из зеркала ufcstats и прогнать проверки качества."""
    _sync_history()


@app.command()
def collect():
    """Снять кард (ESPN) и коэффициенты (bestfightodds) в файлы снимков. БД не нужна."""
    if _collect():
        raise typer.Exit(1)


@app.command("load-snapshots")
def load_snapshots():
    """Загрузить все файлы снимков в БД (повторная загрузка не создает дублей)."""
    _load_snapshots()


@app.command()
def daily():
    """Локальное обновление по расписанию: история + свежие снимки из GitHub + загрузка в БД.
    Сами снимки собирает GitHub Actions (collect), поэтому здесь collect не вызывается."""
    ok = True
    for step in (_sync_history, _pull_snapshots, _load_snapshots):
        try:
            step()
        except Exception as exc:
            ok = False
            typer.echo(f"[{step.__name__}] ERROR: {exc!r}", err=True)
    if not ok:
        raise typer.Exit(1)


@app.command()
def check():
    """Подробный отчет проверок качества с примерами id боев."""
    for name, n, sample in checks.run_checks(_db()):
        typer.echo(f"{n:>6}  {name}" + (f"   e.g. {', '.join(sample)}" if n else ""))


@app.command()
def status():
    """Сводка по содержимому БД и последним запускам."""
    conn = _db()
    for table in ("events", "bouts", "bout_results", "bout_round_stats", "fighters",
                  "card_snapshots", "odds_snapshots"):
        typer.echo(f"{table:18} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>8}")
    typer.echo("\nПоследние запуски:")
    for row in conn.execute("SELECT task, started_at, status, substr(details, 1, 120) FROM sync_runs "
                            "ORDER BY id DESC LIMIT 6"):
        typer.echo("  " + " | ".join(str(x) for x in row))
    typer.echo("\nНеразрешенные имена (по источникам):")
    for row in conn.execute("SELECT source, method, COUNT(*) FROM fighter_aliases WHERE fighter_id IS NULL "
                            "GROUP BY source, method"):
        typer.echo(f"  {row[0]:15} {row[1]:12} {row[2]}")


@app.command()
def unresolved(source: str = typer.Option(None, help="ufcstats_bout / espn / bestfightodds")):
    """Список неразрешенных имен с подсказками — для заполнения data/manual/fighter_aliases.csv."""
    conn = _db()
    query = "SELECT source, name, method, note FROM fighter_aliases WHERE fighter_id IS NULL"
    params = ()
    if source:
        query += " AND source = ?"
        params = (source,)
    for row in conn.execute(query + " ORDER BY source, name", params):
        typer.echo(" | ".join(str(x) for x in row))


if __name__ == "__main__":
    app()
