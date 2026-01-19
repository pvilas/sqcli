# sqcli
A better sqlite3 CLI.

Have you ever wanted all the power and flexibility of the sqlite3 CLI, but with features like syntax highlighting, command history with arrow keys, and TAB autocompletion?

sqcli emulates the sqlite3 CLI experience while adding all these advantages. It supports standard commands like `.read`, `.parameter`, and more, ensuring maximum flexibility for your workflow.

The only changes are:

* Use `shift+left arrow` and `shift+right arrow` to navigate the history.
* When editing a sentence, you can send it by terminating the line with a ; or `Alt+Enter`

This software was built with the help of AI.

I hope you enjoy using it.

## Installation

```
git clone https://github.com/pvilas/sqcli
cd sqcli
uv init
uv run sqcli.py
```

or you can create the executable with

```
source create.sh
```
