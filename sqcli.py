#!/usr/bin/env python3
"""
SQLite CLI mejorado con autocompletado, syntax highlighting y output formateado.

Instalación de dependencias:
    pip install prompt_toolkit rich pygments

Uso:
    python sqlite_cli.py [database.db]
    python sqlite_cli.py  # usa :memory:
"""

import shlex
import sqlite3
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import has_completions
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers.sql import SqlLexer


def create_key_bindings():
    """
    Key bindings personalizados:
    - ↑/↓: navegan dentro del texto multiline (o autocompletado si está abierto)
    - Shift+←/→: navegan por el historial
    - Enter: nueva línea si no termina en ; (excepto dot-commands)
    - Ctrl+O o Alt+Enter: forzar envío
    """
    kb = KeyBindings()

    @kb.add(Keys.Up, filter=~has_completions)
    def _(event):
        """Flecha arriba: mover cursor una línea arriba en el buffer."""
        buff = event.app.current_buffer
        buff.cursor_up(count=1)

    @kb.add(Keys.Down, filter=~has_completions)
    def _(event):
        """Flecha abajo: mover cursor una línea abajo en el buffer."""
        buff = event.app.current_buffer
        buff.cursor_down(count=1)

    @kb.add(Keys.ShiftLeft)
    def _(event):
        """Shift+←: ir al comando anterior en el historial."""
        buff = event.app.current_buffer
        buff.history_backward(count=1)

    @kb.add(Keys.ShiftRight)
    def _(event):
        """Shift+→: ir al comando siguiente en el historial."""
        buff = event.app.current_buffer
        buff.history_forward(count=1)

    @kb.add(Keys.Enter, filter=~has_completions)
    def _(event):
        """Enter: enviar si termina en ; o es dot-command, sino nueva línea."""
        buff = event.app.current_buffer
        text = buff.text.strip()

        # Dot-commands se envían directo
        if text.startswith("."):
            buff.validate_and_handle()
        # SQL debe terminar en ;
        elif text.endswith(";"):
            buff.validate_and_handle()
        # Si está vacío, enviar (para permitir líneas vacías)
        elif not text:
            buff.validate_and_handle()
        else:
            buff.insert_text("\n")

    @kb.add(Keys.Escape, Keys.Enter)  # Alt+Enter
    def _(event):
        """Alt+Enter: forzar envío sin importar el contenido."""
        buff = event.app.current_buffer
        buff.validate_and_handle()

    @kb.add("c-o")  # Ctrl+O
    def _(event):
        """Ctrl+O: forzar envío."""
        buff = event.app.current_buffer
        buff.validate_and_handle()

    return kb


from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

console = Console()


class SQLiteCompleter(Completer):
    """Autocompletado inteligente para SQL y dot-commands."""

    def __init__(self, conn):
        self.conn = conn
        self.keywords = [
            "SELECT",
            "FROM",
            "WHERE",
            "INSERT",
            "INTO",
            "VALUES",
            "UPDATE",
            "SET",
            "DELETE",
            "CREATE",
            "TABLE",
            "INDEX",
            "VIEW",
            "TRIGGER",
            "DROP",
            "ALTER",
            "JOIN",
            "LEFT",
            "RIGHT",
            "INNER",
            "OUTER",
            "CROSS",
            "NATURAL",
            "ON",
            "USING",
            "GROUP",
            "BY",
            "ORDER",
            "HAVING",
            "LIMIT",
            "OFFSET",
            "UNION",
            "INTERSECT",
            "EXCEPT",
            "AND",
            "OR",
            "NOT",
            "IN",
            "LIKE",
            "GLOB",
            "BETWEEN",
            "IS",
            "NULL",
            "TRUE",
            "FALSE",
            "EXISTS",
            "CASE",
            "WHEN",
            "THEN",
            "ELSE",
            "END",
            "AS",
            "DISTINCT",
            "ALL",
            "ASC",
            "DESC",
            "PRIMARY",
            "KEY",
            "FOREIGN",
            "REFERENCES",
            "UNIQUE",
            "CHECK",
            "DEFAULT",
            "AUTOINCREMENT",
            "IF",
            "NOT",
            "EXISTS",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "TRANSACTION",
            "SAVEPOINT",
            "PRAGMA",
            "EXPLAIN",
            "ANALYZE",
            "VACUUM",
            "REINDEX",
            "COUNT",
            "SUM",
            "AVG",
            "MIN",
            "MAX",
            "TOTAL",
            "GROUP_CONCAT",
            "ABS",
            "COALESCE",
            "IFNULL",
            "NULLIF",
            "LENGTH",
            "LOWER",
            "UPPER",
            "TRIM",
            "LTRIM",
            "RTRIM",
            "SUBSTR",
            "REPLACE",
            "DATE",
            "TIME",
            "DATETIME",
            "STRFTIME",
            "JULIANDAY",
        ]
        self.dot_commands = [
            ".tables",
            ".schema",
            ".read",
            ".mode",
            ".quit",
            ".exit",
            ".help",
            ".dump",
            ".import",
            ".export",
            ".columns",
            ".indexes",
            ".describe",
            ".count",
            ".sample",
            ".timer",
            ".changes",
            ".parameter",
            ".param",
            ".load",
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        text_upper = text.upper()
        word = document.get_word_before_cursor()

        # Dot commands
        if document.text.startswith("."):
            for cmd in self.dot_commands:
                if cmd.startswith(document.text.lower()):
                    yield Completion(cmd, start_position=-len(document.text))
            return

        # Columnas después de SELECT o WHERE (si hay FROM antes)
        if ("SELECT" in text_upper or "WHERE" in text_upper) and "FROM" in text_upper:
            for col in self._get_columns_from_context(text):
                if col.upper().startswith(word.upper()):
                    yield Completion(col, start_position=-len(word))

        # Tablas después de FROM/JOIN/INTO/UPDATE/TABLE
        trigger_words = ["FROM", "JOIN", "INTO", "UPDATE", "TABLE"]
        if any(tw in text_upper for tw in trigger_words):
            for table in self._get_tables():
                if table.upper().startswith(word.upper()):
                    yield Completion(table, start_position=-len(word))

        # Keywords SQL
        for kw in self.keywords:
            if kw.startswith(word.upper()):
                yield Completion(kw, start_position=-len(word))

    def _get_tables(self):
        try:
            cur = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
            return [row[0] for row in cur]
        except:
            return []

    def _get_columns_from_context(self, text):
        """Extrae columnas de las tablas mencionadas en el query."""
        columns = []
        tables = self._get_tables()
        text_upper = text.upper()

        for table in tables:
            if table.upper() in text_upper:
                try:
                    cur = self.conn.execute(f"PRAGMA table_info({table})")
                    columns.extend([row[1] for row in cur])
                except:
                    pass
        return list(set(columns))


class SQLiteCLI:
    """CLI interactivo mejorado para SQLite."""

    def __init__(self, db_path=":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

        # Historial persistente
        history_file = Path.home() / ".sqlite_cli_history"

        self.session = PromptSession(
            lexer=PygmentsLexer(SqlLexer),
            completer=SQLiteCompleter(self.conn),
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
            key_bindings=create_key_bindings(),
            multiline=True,
        )

        self.running = True
        self.timer_enabled = False
        self.mode = "table"  # table, csv, json, line
        self.parameters = {}  # parámetros para queries

    def run(self):
        """Loop principal del CLI."""
        console.print(f"[bold green]SQLite CLI mejorado[/]")
        console.print(f"Base de datos: [cyan]{self.db_path}[/]")
        console.print("Escribe [bold].help[/] para ver comandos disponibles")
        console.print(
            "[dim]Tip: ↑↓ navegan el texto, Shift+←→ el historial, Ctrl+O fuerza envío[/]\n"
        )

        # Ejecutar default.sql si existe
        default_sql = Path("default.sql")
        if default_sql.exists():
            console.print(f"[dim]Ejecutando {default_sql}...[/]")
            self._cmd_read([str(default_sql)])
            console.print()

        while self.running:
            try:
                text = self.session.prompt("sqlite> ").strip()

                if not text:
                    continue

                if text.startswith("."):
                    self._handle_dot_command(text)
                else:
                    self._execute_sql(text)

            except KeyboardInterrupt:
                console.print("\n[dim]Usa .quit para salir[/]")
                continue
            except EOFError:
                break

        self.conn.close()
        console.print("[dim]Bye![/]")

    def _execute_sql(self, sql):
        """Ejecuta SQL y muestra resultados."""
        import time

        try:
            start = time.perf_counter()

            # Usar parámetros si hay definidos
            if self.parameters:
                cur = self.conn.execute(sql, self.parameters)
            else:
                cur = self.conn.execute(sql)

            rows = cur.fetchall()
            elapsed = time.perf_counter() - start

            if rows:
                headers = [desc[0] for desc in cur.description]
                self._print_results(rows, headers)

                if self.timer_enabled:
                    console.print(f"[dim]{len(rows)} filas en {elapsed:.4f}s[/]")
            else:
                self.conn.commit()
                msg = f"[green]OK[/]"
                if self.conn.total_changes:
                    msg += f" [dim]({cur.rowcount} filas afectadas)[/]"
                if self.timer_enabled:
                    msg += f" [dim]{elapsed:.4f}s[/]"
                console.print(msg)

        except sqlite3.Error as e:
            console.print(f"[red]Error: {e}[/]")

    def _print_results(self, rows, headers):
        """Imprime resultados según el modo configurado."""
        if self.mode == "table":
            self._print_table(rows, headers)
        elif self.mode == "csv":
            self._print_csv(rows, headers)
        elif self.mode == "json":
            self._print_json(rows, headers)
        elif self.mode == "line":
            self._print_line(rows, headers)

    def _print_table(self, rows, headers):
        """Formato tabla con Rich."""
        table = Table(show_header=True, header_style="bold cyan")
        for h in headers:
            table.add_column(h)
        for row in rows:
            table.add_row(*[self._format_value(v) for v in row])
        console.print(table)

    def _print_csv(self, rows, headers):
        """Formato CSV."""
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        console.print(output.getvalue())

    def _print_json(self, rows, headers):
        """Formato JSON."""
        import json

        data = [dict(zip(headers, row)) for row in rows]
        console.print(Syntax(json.dumps(data, indent=2, default=str), "json"))

    def _print_line(self, rows, headers):
        """Formato línea por línea."""
        for i, row in enumerate(rows):
            if i > 0:
                console.print("---")
            for h, v in zip(headers, row):
                console.print(f"[cyan]{h}[/] = {self._format_value(v)}")

    def _format_value(self, v):
        """Formatea un valor para display."""
        if v is None:
            return "[dim]NULL[/]"
        return str(v)

    def _handle_dot_command(self, text):
        """Procesa dot-commands."""
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()

        cmd = parts[0].lower()
        args = parts[1:]

        commands = {
            ".tables": self._cmd_tables,
            ".schema": self._cmd_schema,
            ".read": self._cmd_read,
            ".dump": self._cmd_dump,
            ".mode": self._cmd_mode,
            ".import": self._cmd_import,
            ".export": self._cmd_export,
            ".columns": self._cmd_columns,
            ".indexes": self._cmd_indexes,
            ".describe": self._cmd_describe,
            ".count": self._cmd_count,
            ".sample": self._cmd_sample,
            ".timer": self._cmd_timer,
            ".changes": self._cmd_changes,
            ".parameter": self._cmd_parameter,
            ".param": self._cmd_parameter,
            ".load": self._cmd_load,
            ".quit": self._cmd_quit,
            ".exit": self._cmd_quit,
            ".help": self._cmd_help,
        }

        if cmd in commands:
            commands[cmd](args)
        else:
            console.print(f"[yellow]Comando desconocido: {cmd}[/]")
            console.print("[dim]Usa .help para ver comandos disponibles[/]")

    # --- Comandos ---

    def _cmd_tables(self, args):
        """Lista tablas y vistas."""
        cur = self.conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'view') ORDER BY type, name"
        )
        rows = cur.fetchall()
        if rows:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Tipo")
            table.add_column("Nombre")
            for row in rows:
                table.add_row(row[0], row[1])
            console.print(table)
        else:
            console.print("[dim]No hay tablas[/]")

    def _cmd_schema(self, args):
        """Muestra schema de tabla(s)."""
        if args:
            cur = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = ? AND sql IS NOT NULL",
                (args[0],),
            )
        else:
            cur = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
            )

        schemas = [row[0] for row in cur if row[0]]
        if schemas:
            for sql in schemas:
                console.print(Syntax(sql + ";", "sql"))
                console.print()
        else:
            console.print("[dim]No se encontró schema[/]")

    def _cmd_read(self, args):
        """Ejecuta SQL y dot-commands desde archivo."""
        if not args:
            console.print("[red]Uso: .read ARCHIVO[/]")
            return

        filepath = Path(args[0])
        if not filepath.exists():
            console.print(f"[red]Archivo no encontrado: {filepath}[/]")
            return

        try:
            content = filepath.read_text()
            lines = content.split("\n")

            sql_buffer = []

            for line in lines:
                stripped = line.strip()

                # Ignorar líneas vacías y comentarios
                if not stripped or stripped.startswith("--"):
                    continue

                # Dot-commands se ejecutan directamente
                if stripped.startswith("."):
                    # Primero ejecutar SQL acumulado
                    if sql_buffer:
                        sql = "\n".join(sql_buffer).strip()
                        if sql:
                            self._execute_sql(sql)
                        sql_buffer = []

                    # Ejecutar dot-command
                    self._handle_dot_command(stripped)
                else:
                    # Acumular SQL
                    sql_buffer.append(line)

                    # Si termina en ;, ejecutar
                    if stripped.endswith(";"):
                        sql = "\n".join(sql_buffer).strip()
                        if sql:
                            self._execute_sql(sql)
                        sql_buffer = []

            # Ejecutar SQL restante
            if sql_buffer:
                sql = "\n".join(sql_buffer).strip()
                if sql:
                    self._execute_sql(sql)

            console.print(f"[green]OK[/] - Ejecutado {filepath}")
        except Exception as e:
            console.print(f"[red]Error: {e}[/]")

    def _cmd_dump(self, args):
        """Exporta base de datos como SQL."""
        table_filter = args[0] if args else None

        for line in self.conn.iterdump():
            if table_filter and table_filter not in line:
                continue
            console.print(line)

    def _cmd_mode(self, args):
        """Cambia modo de output."""
        modes = ["table", "csv", "json", "line"]

        if not args:
            console.print(f"Modo actual: [cyan]{self.mode}[/]")
            console.print(f"Modos disponibles: {', '.join(modes)}")
            return

        mode = args[0].lower()
        if mode in modes:
            self.mode = mode
            console.print(f"[green]Modo cambiado a: {mode}[/]")
        else:
            console.print(f"[red]Modo inválido. Usa: {', '.join(modes)}[/]")

    def _cmd_import(self, args):
        """Importa CSV a tabla."""
        if len(args) < 2:
            console.print("[red]Uso: .import ARCHIVO.csv TABLA[/]")
            return

        import csv

        filepath, table = Path(args[0]), args[1]

        if not filepath.exists():
            console.print(f"[red]Archivo no encontrado: {filepath}[/]")
            return

        try:
            with open(filepath, newline="") as f:
                reader = csv.reader(f)
                headers = next(reader)

                # Crear tabla si no existe
                cols = ", ".join(f'"{h}" TEXT' for h in headers)
                self.conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols})')

                # Insertar datos
                placeholders = ", ".join("?" * len(headers))
                inserted = 0
                for row in reader:
                    self.conn.execute(
                        f'INSERT INTO "{table}" VALUES ({placeholders})', row
                    )
                    inserted += 1

                self.conn.commit()
                console.print(f"[green]OK[/] - {inserted} filas importadas a {table}")
        except Exception as e:
            console.print(f"[red]Error: {e}[/]")

    def _cmd_export(self, args):
        """Exporta tabla a CSV."""
        if len(args) < 2:
            console.print("[red]Uso: .export TABLA ARCHIVO.csv[/]")
            return

        import csv

        table, filepath = args[0], Path(args[1])

        try:
            cur = self.conn.execute(f'SELECT * FROM "{table}"')
            headers = [desc[0] for desc in cur.description]
            rows = cur.fetchall()

            with open(filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)

            console.print(f"[green]OK[/] - {len(rows)} filas exportadas a {filepath}")
        except Exception as e:
            console.print(f"[red]Error: {e}[/]")

    def _cmd_columns(self, args):
        """Lista columnas de una tabla."""
        if not args:
            console.print("[red]Uso: .columns TABLA[/]")
            return

        try:
            cur = self.conn.execute(f"PRAGMA table_info({args[0]})")
            rows = cur.fetchall()

            if rows:
                table = Table(show_header=True, header_style="bold")
                table.add_column("#")
                table.add_column("Columna")
                table.add_column("Tipo")
                table.add_column("Not Null")
                table.add_column("Default")
                table.add_column("PK")

                for row in rows:
                    table.add_row(
                        str(row[0]),
                        row[1],
                        row[2] or "ANY",
                        "✓" if row[3] else "",
                        str(row[4]) if row[4] is not None else "",
                        "✓" if row[5] else "",
                    )
                console.print(table)
            else:
                console.print(f"[red]Tabla no encontrada: {args[0]}[/]")
        except sqlite3.Error as e:
            console.print(f"[red]Error: {e}[/]")

    def _cmd_indexes(self, args):
        """Lista índices."""
        if args:
            cur = self.conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (args[0],),
            )
        else:
            cur = self.conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND sql IS NOT NULL"
            )

        rows = cur.fetchall()
        if rows:
            for name, sql in rows:
                console.print(f"[cyan]{name}[/]")
                console.print(Syntax(sql + ";", "sql"))
                console.print()
        else:
            console.print("[dim]No hay índices[/]")

    def _cmd_describe(self, args):
        """Describe tabla con estadísticas."""
        if not args:
            console.print("[red]Uso: .describe TABLA[/]")
            return

        table_name = args[0]

        # Schema
        console.print(f"\n[bold]Schema de {table_name}:[/]")
        self._cmd_columns([table_name])

        # Conteo
        try:
            cur = self.conn.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            count = cur.fetchone()[0]
            console.print(f"\n[bold]Filas:[/] {count:,}")
        except:
            pass

        # Índices
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table_name,),
        )
        indexes = [row[0] for row in cur]
        if indexes:
            console.print(f"[bold]Índices:[/] {', '.join(indexes)}")

    def _cmd_count(self, args):
        """Cuenta filas de tabla(s)."""
        tables = (
            args
            if args
            else [
                row[0]
                for row in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        )

        table = Table(show_header=True, header_style="bold")
        table.add_column("Tabla")
        table.add_column("Filas", justify="right")

        for t in tables:
            try:
                cur = self.conn.execute(f'SELECT COUNT(*) FROM "{t}"')
                count = cur.fetchone()[0]
                table.add_row(t, f"{count:,}")
            except:
                table.add_row(t, "[red]Error[/]")

        console.print(table)

    def _cmd_sample(self, args):
        """Muestra filas de ejemplo."""
        if not args:
            console.print("[red]Uso: .sample TABLA [N][/]")
            return

        table_name = args[0]
        limit = int(args[1]) if len(args) > 1 else 5

        try:
            self._execute_sql(f'SELECT * FROM "{table_name}" LIMIT {limit}')
        except:
            pass

    def _cmd_timer(self, args):
        """Activa/desactiva timer."""
        if args and args[0].lower() in ("on", "off"):
            self.timer_enabled = args[0].lower() == "on"
        else:
            self.timer_enabled = not self.timer_enabled

        state = "activado" if self.timer_enabled else "desactivado"
        console.print(f"Timer [cyan]{state}[/]")

    def _cmd_load(self, args):
        """Carga una extensión de SQLite."""
        if not args:
            console.print("[red]Uso: .load ARCHIVO [ENTRYPOINT][/]")
            console.print("[dim]Ejemplo: .load ./my_extension.so[/]")
            return

        filepath = args[0]
        entrypoint = args[1] if len(args) > 1 else None

        try:
            # Habilitar carga de extensiones
            self.conn.enable_load_extension(True)

            if entrypoint:
                self.conn.load_extension(filepath, entrypoint)
            else:
                self.conn.load_extension(filepath)

            console.print(f"[green]OK[/] - Extensión cargada: {filepath}")
        except sqlite3.OperationalError as e:
            console.print(f"[red]Error: {e}[/]")
        except AttributeError:
            console.print(
                "[red]Error: La carga de extensiones no está soportada en esta instalación de SQLite[/]"
            )

    def _cmd_changes(self, args):
        """Muestra cambios totales."""
        console.print(f"Cambios totales: [cyan]{self.conn.total_changes}[/]")

    def _cmd_parameter(self, args):
        """Gestiona parámetros para queries."""
        if not args:
            # Mostrar todos los parámetros
            if self.parameters:
                table = Table(show_header=True, header_style="bold")
                table.add_column("Parámetro")
                table.add_column("Valor")
                table.add_column("Tipo")
                for name, value in self.parameters.items():
                    table.add_row(f":{name}", repr(value), type(value).__name__)
                console.print(table)
            else:
                console.print("[dim]No hay parámetros definidos[/]")
            return

        subcmd = args[0].lower()

        if subcmd == "set" and len(args) >= 3:
            # .parameter set nombre valor
            name = args[1].lstrip(":@")
            value_str = " ".join(args[2:])

            # Intentar parsear el tipo
            value = self._parse_parameter_value(value_str)
            self.parameters[name] = value
            console.print(
                f"[green]OK[/] :{name} = {repr(value)} ({type(value).__name__})"
            )

        elif subcmd == "unset" and len(args) >= 2:
            # .parameter unset nombre
            name = args[1].lstrip(":@")
            if name in self.parameters:
                del self.parameters[name]
                console.print(f"[green]OK[/] Eliminado :{name}")
            else:
                console.print(f"[yellow]Parámetro no encontrado: :{name}[/]")

        elif subcmd == "clear":
            # .parameter clear
            count = len(self.parameters)
            self.parameters.clear()
            console.print(f"[green]OK[/] Eliminados {count} parámetros")

        elif subcmd == "list":
            # .parameter list (alias de sin argumentos)
            self._cmd_parameter([])

        else:
            console.print("[red]Uso:[/]")
            console.print("  .parameter                    - Lista parámetros")
            console.print("  .parameter set NOMBRE VALOR   - Define parámetro")
            console.print("  .parameter unset NOMBRE       - Elimina parámetro")
            console.print("  .parameter clear              - Elimina todos")

    def _parse_parameter_value(self, value_str):
        """Parsea string a tipo apropiado."""
        # NULL
        if value_str.upper() == "NULL":
            return None

        # Booleanos
        if value_str.upper() == "TRUE":
            return True
        if value_str.upper() == "FALSE":
            return False

        # Entero
        try:
            return int(value_str)
        except ValueError:
            pass

        # Float
        try:
            return float(value_str)
        except ValueError:
            pass

        # String (quitar comillas si las tiene)
        if (value_str.startswith('"') and value_str.endswith('"')) or (
            value_str.startswith("'") and value_str.endswith("'")
        ):
            return value_str[1:-1]

        return value_str

    def _cmd_quit(self, args):
        """Sale del CLI."""
        self.running = False

    def _cmd_help(self, args):
        """Muestra ayuda."""
        help_data = [
            (".tables", "Lista tablas y vistas"),
            (".schema [TABLA]", "Muestra CREATE statements"),
            (".columns TABLA", "Lista columnas con tipos"),
            (".indexes [TABLA]", "Lista índices"),
            (".describe TABLA", "Describe tabla con estadísticas"),
            (".count [TABLA...]", "Cuenta filas"),
            (".sample TABLA [N]", "Muestra N filas de ejemplo (default: 5)"),
            ("", ""),
            (".read ARCHIVO", "Ejecuta SQL desde archivo"),
            (".dump [TABLA]", "Exporta como SQL"),
            (".import CSV TABLA", "Importa CSV a tabla"),
            (".export TABLA CSV", "Exporta tabla a CSV"),
            ("", ""),
            (".parameter", "Lista parámetros definidos"),
            (".parameter set NAME VALUE", "Define parámetro (:NAME en queries)"),
            (".parameter unset NAME", "Elimina parámetro"),
            (".parameter clear", "Elimina todos los parámetros"),
            ("", ""),
            (".load ARCHIVO [ENTRY]", "Carga extensión SQLite (.so/.dll)"),
            ("", ""),
            (".mode [MODE]", "Cambia formato: table, csv, json, line"),
            (".timer [on|off]", "Activa/desactiva timer de queries"),
            (".changes", "Muestra total de cambios"),
            ("", ""),
            (".help", "Muestra esta ayuda"),
            (".quit", "Salir"),
        ]

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="cyan")
        table.add_column()

        for cmd, desc in help_data:
            table.add_row(cmd, desc)

        console.print("\n[bold]Comandos disponibles:[/]\n")
        console.print(table)
        console.print(
            "\n[dim]Tip: Las queries SQL terminan con ; y soportan múltiples líneas[/]"
        )
        console.print("[dim]Tip: Usa Tab para autocompletar y ↑↓ para historial[/]\n")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else ":memory:"

    try:
        cli = SQLiteCLI(db_path)
        cli.run()
    except Exception as e:
        console.print(f"[red]Error fatal: {e}[/]")
        sys.exit(1)
