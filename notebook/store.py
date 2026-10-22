import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets = self.root / 'assets'
        self.assets.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, subject TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL, base_docx TEXT);
            CREATE TABLE IF NOT EXISTS pages (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
              position INTEGER NOT NULL, source_number TEXT NOT NULL, filename TEXT NOT NULL,
              original TEXT NOT NULL, image TEXT NOT NULL, sha TEXT NOT NULL, phash TEXT NOT NULL,
              width INTEGER NOT NULL, height INTEGER NOT NULL, status TEXT NOT NULL,
              document TEXT NOT NULL, version INTEGER NOT NULL, error TEXT, duplicate_of TEXT,
              created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions (
              id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
              version INTEGER NOT NULL, snapshot TEXT NOT NULL, action TEXT NOT NULL, created TEXT NOT NULL,
              UNIQUE(page_id, version));
            CREATE TABLE IF NOT EXISTS corrections (
              id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
              block_id TEXT, question_id TEXT, message TEXT NOT NULL, before_text TEXT,
              after_text TEXT, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS examples (
              id TEXT PRIMARY KEY, subject TEXT NOT NULL, scope TEXT NOT NULL, text TEXT NOT NULL,
              original_text TEXT NOT NULL, context TEXT NOT NULL, image TEXT NOT NULL,
              page_id TEXT, block_id TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handwriting_notes (
              id TEXT PRIMARY KEY, scope TEXT NOT NULL, subject TEXT NOT NULL,
              text TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
              status TEXT NOT NULL, base_version INTEGER NOT NULL, created TEXT NOT NULL,
              updated TEXT NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS recognition_runs (
              id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
              document TEXT, error TEXT, provider TEXT NOT NULL, created TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.root / 'notebook.sqlite3', timeout=30)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                with db:
                    yield db
            finally:
                db.close()

    def path(self, relative):
        p = (self.root / relative).resolve()
        if not p.is_relative_to(self.assets):
            raise ValueError('Недопустимый путь файла')
        return p

    def save(self, data, suffix):
        name = f'assets/{uid()}{suffix}'
        path = self.path(name)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
        return name

    def seed_context_notes(self, notes):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='shared_context_v1'").fetchone(): return
            for note in notes:
                db.execute('INSERT INTO handwriting_notes VALUES(?,?,?,?,?)',(uid(),note['scope'],note['subject'],note['text'],now()))
            db.execute("INSERT INTO metadata VALUES('shared_context_v1','applied')")

    def recover_jobs(self):
        # Run only when the actual application starts, not when a test imports its factory.
        with self.connect() as db:
            running = db.execute("SELECT page_id FROM jobs WHERE status='running'").fetchall()
            db.execute("UPDATE jobs SET status='interrupted', error='Обработка прервана перезапуском. Повторите вручную.' WHERE status='running'")
            for row in running:
                db.execute("UPDATE pages SET status='error', error='Обработка прервана. Сохранённая версия не потеряна.' WHERE id=?", (row['page_id'],))

    def prune(self, paths):
        """Remove only specific files made unreachable by an explicit deletion/update."""
        for path in set(paths):
            if not path: continue
            with self.connect() as db:
                used = db.execute('SELECT 1 FROM pages WHERE original=? OR image=? LIMIT 1',(path,path)).fetchone()
                used = used or db.execute('SELECT 1 FROM examples WHERE image=? LIMIT 1',(path,)).fetchone()
                used = used or db.execute('SELECT 1 FROM projects WHERE base_docx=? LIMIT 1',(path,)).fetchone()
                used = used or db.execute('SELECT 1 FROM versions WHERE instr(snapshot,?)>0 LIMIT 1',(path,)).fetchone()
                if not used: self.path(path).unlink(missing_ok=True)

    def project(self, project_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if not row:
            raise KeyError('Проект не найден')
        return dict(row)

    def page(self, page_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
        if not row:
            raise KeyError('Страница не найдена')
        result = dict(row)
        result['document'] = json.loads(result['document'])
        return result

    def snapshot(self, db, page_id, action):
        row = dict(db.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone())
        db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)', (
            uid(), page_id, row['version'], dumps(row), action, now()))

    def update_page(self, page_id, document, action, expected_version, status='review', source_number=None, allow_processing=False):
        with self.connect() as db:
            row = db.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
            if not row:
                raise KeyError('Страница не найдена')
            if row['version'] != expected_version:
                raise Conflict('Страница изменена в другом окне. Обновите её.')
            if row['status'] == 'processing' and not allow_processing:
                raise Conflict('Дождитесь завершения распознавания. Правки сохранённой версии защищены.')
            number = row['source_number'] if source_number is None else str(source_number)[:80]
            db.execute('UPDATE pages SET document=?,status=?,version=version+1,source_number=?,error=NULL,updated=? WHERE id=?',
                       (dumps(document), status, number, now(), page_id))
            db.execute('UPDATE projects SET updated=? WHERE id=?', (now(), row['project_id']))
            self.snapshot(db, page_id, action)
        return self.page(page_id)


class Conflict(Exception):
    pass


def empty_document():
    return {'blocks': [], 'questions': [], 'notes': ''}
