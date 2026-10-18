"""Shared fixtures: a small PR diff and the head-commit source of the changed file."""

DIFF = "\n".join([
    "diff --git a/app/db.py b/app/db.py",
    "index 111..222 100644",
    "--- a/app/db.py",
    "+++ b/app/db.py",
    "@@ -2,6 +2,6 @@",
    " ",
    " ",
    " def get_user(name):",
    '-    query = "SELECT * FROM users WHERE name = %s"',
    "-    return db.execute(query, (name,))",
    "+    query = \"SELECT * FROM users WHERE name = '\" + name + \"'\"",
    "+    return db.execute(query)",
    " ",
    "diff --git a/README.md b/README.md",
    "new file mode 100644",
    "index 0000000..333",
    "--- /dev/null",
    "+++ b/README.md",
    "@@ -0,0 +1,2 @@",
    "+# Demo",
    "+hello",
    "diff --git a/old.py b/old.py",
    "deleted file mode 100644",
    "index 444..0000000",
    "--- a/old.py",
    "+++ /dev/null",
    "@@ -1,2 +0,0 @@",
    "-x = 1",
    "-y = 2",
    "",
])

DB_PY = "\n".join([
    "import sqlite3",          # 1
    "",                        # 2
    "",                        # 3
    "def get_user(name):",     # 4
    "    query = \"SELECT * FROM users WHERE name = '\" + name + \"'\"",  # 5
    "    return db.execute(query)",   # 6
    "",                        # 7
    "",                        # 8
    "class Repo:",             # 9
    "    def find(self, key):",       # 10
    "        return self.items.get(key)",  # 11
    "",
])
