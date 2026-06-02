# Glasses Inventory App v5.8
# Changes from v5.7:
# - Inline editor starts on SINGLE click (and still works on double click)
# - Inventory: add buttons to Delete selected item, Edit selected item, Adjust qty (+/-)
#
# Notes:
# - Deleting an inventory item will also delete its related sales & movements (FK cascade).
# - Editing an item updates brand/model/sku in the items table; sales/movements remain linked via item_id.

import sqlite3
import tkinter as tk


# ===== v7.0.1 helpers (no behavior change) =====
def is_placeholder_model(model: str) -> bool:
    """Placeholder models are empty/whitespace or single non-alphanumeric symbols like '.', '-', '_'"""
    m = (model or "").strip()
    if m == "":
        return True
    if len(m) == 1 and (not m.isalnum()):
        return True
    return False

def list_skus_for_brand_only(brand: str):
    """Return all distinct SKUs for a brand (brand-only filtering)."""
    b = (brand or "").strip()
    if not b:
        return []
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT sku FROM items WHERE brand=? ORDER BY sku",
            (b,),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0] is not None]
# ===== end v7.0.1 helpers =====

def treeview_sort_column(tv: ttk.Treeview, col: str, reverse: bool = False):
    """
    Sort ttk.Treeview rows by clicking a column header.
    - Keeps default insertion order until a header is clicked.
    - Toggles ascending/descending on repeated clicks.
    - Numeric columns (price/qty) sort numerically when possible.
    """
    def try_float(x):
        try:
            s = str(x).replace(",", "").replace("₪", "").strip()
            return float(s)
        except Exception:
            return None

    items = []
    for iid in tv.get_children(""):
        v = tv.set(iid, col)
        n = try_float(v)
        key = (0, n) if n is not None else (1, str(v).lower())
        items.append((key, iid))

    items.sort(key=lambda t: t[0], reverse=reverse)

    for idx, (_, iid) in enumerate(items):
        tv.move(iid, "", idx)

    # next click reverses
    tv.heading(col, command=lambda: treeview_sort_column(tv, col, not reverse))

def treeview_capture_default_order(tv):
    tv._default_order = tv.get_children("")

def treeview_reset_to_default(tv):
    order = getattr(tv, "_default_order", None)
    if not order:
        return
    for i, iid in enumerate(order):
        if tv.exists(iid):
            tv.move(iid, "", i)

# ===== v7.0.5 safety helper =====
def safe_entry_get(ent):
    """Safely read Entry content even if the widget was destroyed between events."""
    try:
        if ent is None:
            return ""
        # winfo_exists() can throw if underlying tk widget is gone
        if hasattr(ent, "winfo_exists") and ent.winfo_exists():
            return ent.get()
    except Exception:
        return ""
    return ""
# ===== end v7.0.5 =====

from tkinter import ttk, messagebox
from datetime import datetime, timedelta
from pathlib import Path
import shutil

APP_DIR = Path(__file__).resolve().parent
def _widget_is_descendant(widget, ancestor) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        try:
            widget = widget.master
        except Exception:
            break
    return False


def bind_outside_click_close(popup, allow_widgets):
    """Close popup when clicking anywhere outside entry/button/popup."""
    try:
        root = popup.anchor.winfo_toplevel()
    except Exception:
        return

    unbind_outside_click_close(popup)

    def handler(event):
        # If popup isn't visible, do nothing
        try:
            if not popup.visible():
                return
        except Exception:
            return

        try:
            w = root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None

        # Click inside allowed widgets -> keep open
        if w is not None and any(w is aw or _widget_is_descendant(w, aw) for aw in allow_widgets):
            return

        # Click inside popup window -> keep open
        try:
            if w is not None and _widget_is_descendant(w, popup.top):
                return
        except Exception:
            pass

        # Otherwise close
        try:
            popup.hide()
        except Exception:
            pass
        unbind_outside_click_close(popup)

    # Bind globally to all widgets in this app
    bind_id = root.bind_all("<Button-1>", handler, add="+")
    setattr(popup, "_outside_click_bind_id", bind_id)

def unbind_outside_click_close(popup):
    bind_id = getattr(popup, "_outside_click_bind_id", None)
    if not bind_id:
        return
    try:
        root = popup.anchor.winfo_toplevel()
        root.unbind_all("<Button-1>", bind_id)
    except Exception:
        pass
    setattr(popup, "_outside_click_bind_id", None)

DB_PATH = APP_DIR / "inventory.db"
BACKUP_DIR = APP_DIR / "Backups"
MAX_BACKUP_FOLDERS = 5
BACKUP_TIME_HOUR = 6
BACKUP_TIME_MINUTE = 0

def backup_folder_name_for_date(dt: datetime) -> str:
    return dt.strftime("%d-%m-%y")

def parse_backup_folder_date(name: str):
    try:
        return datetime.strptime(name, "%d-%m-%y")
    except Exception:
        return None

def _clear_directory_contents(path: Path):
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                pass

def create_daily_backup(force_date: datetime | None = None):
    backup_dt = force_date or datetime.now()

    if not DB_PATH.exists():
        return False, f"Database not found: {DB_PATH}"

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    target_name = backup_folder_name_for_date(backup_dt)
    target_dir = BACKUP_DIR / target_name

    existing_dated_dirs = []
    for child in BACKUP_DIR.iterdir():
        if child.is_dir():
            parsed = parse_backup_folder_date(child.name)
            if parsed is not None:
                existing_dated_dirs.append((parsed, child))

    if target_dir.exists():
        _clear_directory_contents(target_dir)
    elif len(existing_dated_dirs) < MAX_BACKUP_FOLDERS:
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        existing_dated_dirs.sort(key=lambda t: t[0])
        oldest_dir = existing_dated_dirs[0][1]
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        oldest_dir.rename(target_dir)
        _clear_directory_contents(target_dir)

    backup_db_path = target_dir / "inventory.db"

    src = None
    dst = None
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(backup_db_path)
        src.backup(dst)
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()

    return True, str(backup_db_path)

def has_backup_for_date(check_dt: datetime | None = None) -> bool:
    dt = check_dt or datetime.now()
    return (BACKUP_DIR / backup_folder_name_for_date(dt)).is_dir()

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def _col_exists(c, table, col):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols

def init_db():
    with connect() as c:
        c.executescript(
            "CREATE TABLE IF NOT EXISTS items ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " brand TEXT NOT NULL,"
            " sku TEXT NOT NULL,"
            " model TEXT NOT NULL,"
            " UNIQUE(brand, sku, model)"
            ");"
            "CREATE TABLE IF NOT EXISTS sales ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " item_id INTEGER NOT NULL,"
            " color TEXT,"
            " qty INTEGER NOT NULL,"
            " price REAL NOT NULL,"
            " seller TEXT,"
            " notes TEXT,"
            " month TEXT NOT NULL,"
            " created TEXT NOT NULL,"
            " FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE"
            ");"
            "CREATE TABLE IF NOT EXISTS movements ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " item_id INTEGER NOT NULL,"
            " qty_delta INTEGER NOT NULL,"
            " reason TEXT NOT NULL,"
            " created TEXT NOT NULL,"
            " FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE"
            ");"
            "CREATE TABLE IF NOT EXISTS goals ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " date TEXT NOT NULL,"
            " voucher TEXT,"
            " item_description TEXT,"
            " sale_price REAL NOT NULL DEFAULT 0,"
            " lead_from TEXT,"
            " seller TEXT,"
            " month TEXT NOT NULL,"
            " created TEXT NOT NULL"
            ");"
        )
        if not _col_exists(c, "sales", "kind"):
            c.execute("ALTER TABLE sales ADD COLUMN kind TEXT DEFAULT 'vision'")
        # monthly targets table: one row per (month, year) with an editable numeric target
        c.execute(
            "CREATE TABLE IF NOT EXISTS monthly_targets ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " month TEXT NOT NULL,"
            " year INTEGER NOT NULL,"
            " target REAL NOT NULL DEFAULT 0,"
            " UNIQUE(month, year)"
            ")"
        )

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

def get_or_create_item(c, brand, sku, model):
    brand = (brand or "").strip()
    sku = (sku or "").strip()
    model = (model or "").strip()
    if not brand or not sku or not model:
        raise ValueError("Brand/SKU/Model required")
    row = c.execute("SELECT id FROM items WHERE brand=? AND sku=? AND model=?", (brand, sku, model)).fetchone()
    if row:
        return int(row[0])
    cur = c.execute("INSERT INTO items(brand, sku, model) VALUES(?,?,?)", (brand, sku, model))
    return int(cur.lastrowid)

def inventory_on_hand(c, item_id: int) -> int:
    row = c.execute("SELECT COALESCE(SUM(qty_delta),0) FROM movements WHERE item_id=?", (item_id,)).fetchone()
    return int(row[0] if row else 0)

import re

_HE_MONTHS = [
    "ינואר","פברואר","מרץ","אפריל","מאי","יוני",
    "יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"
]
_EN_MONTHS = [
    "january","february","march","april","may","june",
    "july","august","september","october","november","december"
]

def month_to_key(month_str: str):
    """
    Convert 'ינואר 2026' / 'January 2026' to sortable key (year, month_index).
    Returns None if cannot parse.
    """
    s = (month_str or "").strip()
    if not s:
        return None

    s_low = s.lower()

    # year (4 digits)
    m = re.search(r"\b20\d{2}\b", s_low)
    if not m:
        return None
    year = int(m.group(0))

    # Hebrew month
    for i, hm in enumerate(_HE_MONTHS, start=1):
        if hm in s:
            return (year, i)

    # English month
    for i, em in enumerate(_EN_MONTHS, start=1):
        if em in s_low:
            return (year, i)

    return None

def months_in_db_range(month_from: str, month_to: str):
    """
    Returns a list of month strings from DB that fall within [from..to] inclusive,
    according to month_to_key() ordering. If parse fails -> returns [].
    """
    k_from = month_to_key(month_from)
    k_to = month_to_key(month_to)
    if not k_from or not k_to:
        return []

    months = list_months_from_db()
    keyed = []
    for m in months:
        k = month_to_key(m)
        if k:
            keyed.append((k, m))
    keyed.sort(key=lambda x: x[0])

    lo = min(k_from, k_to)
    hi = max(k_from, k_to)

    return [m for (k, m) in keyed if lo <= k <= hi]

def list_months_from_db_sorted():
    months = list_all_months_from_db()
    keyed = []
    rest = []
    for m in months:
        k = month_to_key(m)
        if k:
            keyed.append((k, m))
        else:
            rest.append(m)
    keyed.sort(key=lambda x: x[0])
    # Put un-parseable months at the end (still stable)
    return [m for _, m in keyed] + sorted(rest)

def normalize_month_text(s: str) -> str:
    """
    Normalize month strings:
    - Convert 2-digit year to 20xx (e.g., 'אפריל 26' -> 'אפריל 2026', 'April 26' -> 'April 2026')
    - Keep existing 4-digit 20xx years.
    - If no year found, return original trimmed string.
    """
    t = (s or "").strip()
    if not t:
        return ""

    # If already has 4-digit year 20xx, keep it
    m4 = re.search(r"\b20(\d{2})\b", t)
    if m4:
        return t

    # Replace standalone 2-digit year with 20xx (must be at end or separated)
    # Examples: "אפריל 26", "April 26", "Apr 26"
    m2 = re.search(r"\b(\d{2})\b", t)
    if not m2:
        return t

    yy = m2.group(1)
    yyyy = f"20{yy}"

    # Replace only that matched 2-digit token
    start, end = m2.span(1)
    return t[:start] + yyyy + t[end:]

def list_months_from_db():
    with connect() as c:
        rows = c.execute("SELECT DISTINCT month FROM sales ORDER BY month").fetchall()
    return [r[0] for r in rows if r and r[0]]

def list_months_from_goals_db():
    """Return distinct months that appear in the goals table."""
    with connect() as c:
        rows = c.execute("SELECT DISTINCT month FROM goals ORDER BY month").fetchall()
    return [r[0] for r in rows if r and r[0]]

def list_all_months_from_db():
    """Return distinct months from both sales AND goals tables, merged and deduplicated."""
    sales_months = set(list_months_from_db())
    goals_months = set(list_months_from_goals_db())
    combined = sales_months | goals_months
    return list(combined)

def extract_year_from_month_text(month_text: str):
    s = (month_text or "").strip()
    m = re.search(r"(20\d{2})", s)   # we only care about 20xx
    return m.group(1) if m else None

def strip_year_from_month_text(month_text: str):
    s = (month_text or "").strip()
    s = re.sub(r"\s*(20\d{2})\s*$", "", s).strip()
    return s

def extract_year_from_month_name(m: str):
    """Return int year from a month string like 'January 2026' or 'ינואר 2026'. None if missing."""
    m = (m or "").strip()
    if not m:
        return None
    # take last token that looks like a year
    parts = m.replace("-", " ").replace("_", " ").split()
    for tok in reversed(parts):
        if tok.isdigit():
            y = int(tok)
            if 2000 <= y <= 2099:
                return y
    return None

def list_years_from_months():
    """Distinct years found in sales months, sorted ascending."""
    years = set()
    for m in list_months_from_db_sorted():  # use your sorted list
        y = extract_year_from_month_name(m)
        if y and y not in years:
            years.add(y)
    return sorted(years)

def latest_year_from_months():
    ys = list_years_from_months()
    return ys[-1] if ys else None

def month_name_without_year(m: str):
    """Remove trailing year token; keep the month name part."""
    m = (m or "").strip()
    if not m:
        return ""
    parts = m.split()
    if parts and parts[-1].isdigit():
        return " ".join(parts[:-1]).strip()
    return m


def list_brands():
    with connect() as c:
        rows = c.execute("SELECT DISTINCT brand FROM items ORDER BY brand").fetchall()
    return [r[0] for r in rows if r and r[0]]


def list_sellers():
    """Distinct sellers already used in sales (non-empty)."""
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT seller FROM sales WHERE seller IS NOT NULL AND TRIM(seller)<>'' ORDER BY seller"
        ).fetchall()
    return [r[0] for r in rows if r and r[0]]


# ===== Yearly Summary helpers =====

def list_years_from_all_data():
    """All distinct years found in sales OR goals months, sorted ascending."""
    years = set()
    for m in list_all_months_from_db():
        y = extract_year_from_month_name(m)
        if y:
            years.add(y)
    return sorted(years)

def get_monthly_target(month_name: str, year: int) -> float:
    with connect() as c:
        row = c.execute(
            "SELECT target FROM monthly_targets WHERE month=? AND year=?",
            (month_name, year)
        ).fetchone()
    return float(row[0]) if row else 0.0

def set_monthly_target(month_name: str, year: int, target: float):
    with connect() as c:
        c.execute(
            "INSERT INTO monthly_targets(month, year, target) VALUES(?,?,?) "
            "ON CONFLICT(month, year) DO UPDATE SET target=excluded.target",
            (month_name, year, target)
        )

def get_yearly_summary(year: int):
    """
    Returns a list of 12 dicts, one per Hebrew month, with:
      - month_name: Hebrew month name (no year)
      - seller_totals: {seller: total goals amount}
      - grand_total: sum of all goals for this month+year
      - target: monthly target (editable)
      - remaining: target - grand_total
    All amounts come from the goals table (sale_price column).
    """
    result = []
    with connect() as c:
        # Distinct sellers for this year from goals table
        month_like = f"% {year}"
        sellers_rows = c.execute(
            "SELECT DISTINCT seller FROM goals "
            "WHERE month LIKE ? AND seller IS NOT NULL AND TRIM(seller)<>'' "
            "ORDER BY seller",
            (month_like,)
        ).fetchall()
        sellers = [r[0] for r in sellers_rows if r and r[0]]

        for i, he_month in enumerate(_HE_MONTHS, start=1):
            month_full = f"{he_month} {year}"
            # total goals per seller
            seller_totals = {}
            for s in sellers:
                row = c.execute(
                    "SELECT COALESCE(SUM(sale_price), 0) "
                    "FROM goals WHERE month=? AND COALESCE(seller,'')=?",
                    (month_full, s)
                ).fetchone()
                seller_totals[s] = float(row[0]) if row else 0.0
            # grand total across all sellers
            row = c.execute(
                "SELECT COALESCE(SUM(sale_price), 0) FROM goals WHERE month=?",
                (month_full,)
            ).fetchone()
            grand_total = float(row[0]) if row else 0.0

            target = get_monthly_target(he_month, year)
            result.append({
                "month_name": he_month,
                "month_full": month_full,
                "month_idx": i,
                "sellers": sellers,
                "seller_totals": seller_totals,
                "grand_total": grand_total,
                "target": target,
                "remaining": target - grand_total,
            })
    return result, sellers

# ===== end Yearly Summary helpers =====


def list_goal_vouchers():
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT voucher FROM goals WHERE voucher IS NOT NULL AND TRIM(voucher)<>'' ORDER BY voucher"
        ).fetchall()
    return [r[0] for r in rows if r and r[0]]


def list_goal_descriptions():
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT item_description FROM goals WHERE item_description IS NOT NULL AND TRIM(item_description)<>'' ORDER BY item_description"
        ).fetchall()
    return [r[0] for r in rows if r and r[0]]


def list_goal_leads():
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT lead_from FROM goals WHERE lead_from IS NOT NULL AND TRIM(lead_from)<>'' ORDER BY lead_from"
        ).fetchall()
    return [r[0] for r in rows if r and r[0]]


def normalize_goal_date_input(value: str, month_text: str) -> str:
    """Normalize goals date input to DD/MM/YY. If year missing, infer from month tab year."""
    s = (value or "").strip()
    if not s:
        return ""
    parts = [p for p in re.split(r"[./\-]+", s) if p]
    if len(parts) < 2:
        return ""
    try:
        day = int(parts[0])
        month = int(parts[1])
    except Exception:
        return ""
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return ""
    if len(parts) >= 3:
        try:
            yy = int(parts[2]) % 100
        except Exception:
            return ""
    else:
        y_full = extract_year_from_month_name(month_text or "") or datetime.now().year
        yy = y_full % 100
    return f"{day:02d}/{month:02d}/{yy:02d}"

def list_models_for_brand(brand: str):
    brand = (brand or "").strip()
    if not brand:
        return []
    with connect() as c:
        rows = c.execute("SELECT DISTINCT model FROM items WHERE brand=? ORDER BY model", (brand,)).fetchall()
    return [r[0] for r in rows if r and r[0]]

def list_skus_for_brand_model(brand: str, model: str):
    brand = (brand or "").strip()
    model = (model or "").strip()
    if not brand:
        return []
    with connect() as c:
        if model:
            rows = c.execute("SELECT DISTINCT sku FROM items WHERE brand=? AND model=? ORDER BY sku", (brand, model)).fetchall()
        else:
            rows = c.execute("SELECT DISTINCT sku FROM items WHERE brand=? ORDER BY sku", (brand,)).fetchall()
    return [r[0] for r in rows if r and r[0]]

def list_models_all():
    """Distinct models across inventory."""
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT model FROM items WHERE model IS NOT NULL AND TRIM(model)<>'' ORDER BY model"
        ).fetchall()
    return [r[0] for r in rows if r and r[0]]

def list_skus_all():
    """Distinct SKUs across inventory."""
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT sku FROM items WHERE sku IS NOT NULL AND TRIM(sku)<>'' ORDER BY sku"
        ).fetchall()
    return [r[0] for r in rows if r and r[0]]

def _ac_match(value: str, typed: str) -> bool:
    """Matching rules copied from Sales inline autocomplete: prefix, token-prefix, contains."""
    s = (value or "").lower()
    if not typed:
        return True
    if s.startswith(typed):
        return True
    for tok in s.replace('-', ' ').replace('_', ' ').split():
        if tok.startswith(typed):
            return True
    return typed in s

def create_autocomplete_entry(parent, var: tk.StringVar, values_fn, width=18, on_enter=None, pick_transform=None):
    """Reusable autocomplete widget: Entry + ▼ button + AutocompletePopup.
    - Keeps focus in entry while typing (popup does not steal focus)
    - Arrow keys navigate, mouse click selects
    - Enter/Tab accept selection when popup is open
    - Manual typing allowed (no restriction)
    Returns (container_frame, entry_widget).
    """
    _suppress_refresh_once = {"v": False}
    container = ttk.Frame(parent)
    entry = tk.Entry(container, textvariable=var, width=width, relief="solid", borderwidth=1)
    entry.pack(side="left", fill="x", expand=True)

    btn = ttk.Button(container, text="▼", width=2)
    btn.pack(side="left", padx=(2, 0))

    popup = AutocompletePopup(entry)
    _focusout_token = {"n": 0}

    def _show(values):
        container.update_idletasks()
        x = entry.winfo_rootx()
        y = entry.winfo_rooty() + entry.winfo_height()
        w = entry.winfo_width() + 26
        popup.show(x, y, w, values, on_pick=_pick)
        bind_outside_click_close(popup, [entry, btn])
        _focusout_token["n"] += 1

    def _pick(v):
        if callable(pick_transform):
            try:
                v = pick_transform(v)
            except Exception:
                pass
        var.set(v)
        entry.focus_set()
        entry.icursor(tk.END)
        _focusout_token["n"] += 1
        _hide_popup()
        
    def refresh(show_all=False):
        try:
            base_vals = list(values_fn() or [])
        except Exception:
            base_vals = []
        typed = var.get().strip().lower()
        vals = base_vals if show_all else [v for v in base_vals if _ac_match(v, typed)]
        # Show automatically if user typed something; always show when forced via ▼
        if vals and (typed or show_all or popup.visible()):
            _show(vals)
        else:
            _hide_popup()
            
    def on_keyrelease(e):
        if _suppress_refresh_once["v"]:
            _suppress_refresh_once["v"] = False
            return
        if e.keysym in ("Down", "Up"):
            return
        refresh(show_all=False)
    entry.bind("<KeyRelease>", on_keyrelease)

    def on_down(_e):
        if not popup.visible():
            refresh(show_all=True)   # open list
            return "break"           # IMPORTANT: don't move on the same keypress
        popup.move(1)
        return "break"

    
    def on_up(_e):
        if not popup.visible():
            refresh(show_all=True)
            return "break"
        popup.move(-1)
        return "break"


    def on_return(_e):
        if popup.visible():
            popup.pick_active()   # this calls _pick(...)
            _hide_popup()         # force close (prevents "single item leftover")
            return "break"
        if callable(on_enter):
            on_enter()
            return "break"
        return None

    def on_tab(_e):
        if popup.visible():
            popup.pick_active()
            _hide_popup()
            entry.after_idle(lambda: entry.tk_focusNext().focus_set())
            return "break"
        typed = var.get().strip().lower()
        if typed:
            try:
                base_vals = list(values_fn() or [])
            except Exception:
                base_vals = []
            matches = [v for v in base_vals if _ac_match(v, typed)]
            if len(matches) == 1:
                if callable(pick_transform):
                    try:
                        accepted = pick_transform(matches[0])
                    except Exception:
                        accepted = matches[0]
                else:
                    accepted = matches[0]
                var.set(accepted)
                entry.icursor(tk.END)
        entry.after_idle(lambda: entry.tk_focusNext().focus_set())
        return "break"


    entry.bind("<KeyRelease>", on_keyrelease)
    entry.bind("<Down>", on_down)
    entry.bind("<Up>", on_up)
    entry.bind("<Return>", on_return)
    entry.bind("<Tab>", on_tab)
    def _hide_popup():
        popup.hide()
        unbind_outside_click_close(popup)
    
    entry.bind("<Escape>", lambda _e: (_hide_popup(), "break"))

    
    def _focusout_hide_guard():
        # If popup is visible, user may be interacting with it (mouse / arrows).
        # Don't hide in that case.
        if popup.visible():
            return
        _hide_popup()
    
    def _on_focus_out(_e):
        # If popup is open, do not schedule a hide — arrow navigation/click selection
        # can momentarily trigger FocusOut on some Tk builds.
        if popup.visible():
            return
        entry.after(80, _focusout_hide_guard)
    
    def _schedule_focusout_hide():
        _focusout_token["n"] += 1
        my_token = _focusout_token["n"]
    
        def _later():
            # if a newer focusout happened, ignore this one
            if my_token != _focusout_token["n"]:
                return
            # if popup is visible, user is interacting with it -> keep open
            if popup.visible():
                return
            _hide_popup()
    
        entry.after(120, _later)
    
    entry.bind("<FocusOut>", lambda _e: _schedule_focusout_hide())

    btn.configure(command=lambda: refresh(show_all=True))
    btn.bind("<Tab>", lambda _e: (entry.tk_focusNext().focus_set(), "break"))
    btn.bind("<Shift-Tab>", lambda _e: (entry.tk_focusPrev().focus_set(), "break"))
    return container, entry

class AutocompletePopup:
    """Reliable autocomplete dropdown for Windows using a Listbox in a borderless Toplevel."""
    def __init__(self, anchor_widget):
        self.anchor = anchor_widget
        self.top = tk.Toplevel(anchor_widget)
        self.top.withdraw()
        self.top.overrideredirect(True)
        try:
            self.top.attributes("-topmost", True)
        except Exception:
            pass

        frm = ttk.Frame(self.top, padding=0, borderwidth=1, relief="solid")
        frm.pack(fill="both", expand=True)
        self.lb = tk.Listbox(frm, activestyle="dotbox", exportselection=False)
        self.lb.pack(fill="both", expand=True)

        self._values = []
        self._on_pick = None

        self.lb.bind("<Button-1>", self._on_click)
        self.lb.bind("<ButtonRelease-1>", self._on_release)
        self.lb.bind("<Double-Button-1>", lambda _e: self.pick_active())
        self.lb.bind("<Return>", lambda _e: self.pick_active())

    def hide(self):
        self.top.withdraw()

    def visible(self):
        return self.top.state() == "normal"

    def show(self, x, y, w, values, on_pick):
        self._values = list(values)
        self._on_pick = on_pick

        self.lb.delete(0, tk.END)
        for v in self._values:
            self.lb.insert(tk.END, v)

        # size: show up to 8 rows
        rows = min(8, max(1, len(self._values)))
        self.lb.config(height=rows)

        # rough row height for default Windows Tk: 18-20px
        h = rows * 20
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        self.top.deiconify()
        try:
            self.top.lift()
        except Exception:
            pass

        # select first item by default
        if self._values:
            self.lb.selection_clear(0, tk.END)
            self.lb.selection_set(0)
            self.lb.activate(0)

    def move(self, delta):
        if not self.visible() or self.lb.size() == 0:
            return
        cur = self.lb.curselection()
        idx = cur[0] if cur else 0
        idx = max(0, min(self.lb.size() - 1, idx + delta))
        self.lb.selection_clear(0, tk.END)
        self.lb.selection_set(idx)
        self.lb.activate(idx)
        self.lb.see(idx)

    def pick_active(self):
        if not self.visible():
            return
        cur = self.lb.curselection()
        if not cur:
            return
        val = self.lb.get(cur[0])
        if self._on_pick:
            self._on_pick(val)
        self.hide()

    def _on_click(self, e):
        # highlight the row under the cursor
        try:
            idx = self.lb.nearest(e.y)
            self.lb.selection_clear(0, tk.END)
            self.lb.selection_set(idx)
            self.lb.activate(idx)
        except Exception:
            pass
        return "break"

    def _on_release(self, e):
        # pick the row under the cursor on mouse release (after Tk updates selection)
        try:
            idx = self.lb.nearest(e.y)
            self.lb.selection_clear(0, tk.END)
            self.lb.selection_set(idx)
            self.lb.activate(idx)
        except Exception:
            pass
        try:
            self.lb.after_idle(self.pick_active)
        except Exception:
            self.pick_active()
        return "break"


class InlineCellEditor:
    """
    Overlay editor over a Treeview cell.
    - Single click + double click edit.
    - For combo fields: Entry + autocomplete popup (prefix + token prefix + contains).
    - Silent validation (no popups).
    """
    def __init__(self, app, tree: ttk.Treeview, col_order: list[str], col_specs: dict[str, dict], single_click=True):
        self.app = app
        self.tree = tree
        self.col_order = col_order
        self.col_specs = col_specs
        self.widget = None
        self._ignore_next_click = False
        self.popup = AutocompletePopup(tree)

        tree.bind("<Double-1>", self._on_double, add="+")
        if single_click:
            tree.bind("<Button-1>", self._on_single_click, add="+")
        tree.bind("<MouseWheel>", lambda _e: self.popup.hide(), add="+")
        tree.bind("<Button-4>", lambda _e: self.popup.hide(), add="+")
        tree.bind("<Button-5>", lambda _e: self.popup.hide(), add="+")

    def _hit_cell(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return None, None
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row or not col:
            return None, None
        idx = int(col.replace("#", "")) - 1
        if idx < 0 or idx >= len(self.col_order):
            return None, None
        return row, self.col_order[idx]

    def _on_double(self, event):
        row, col_id = self._hit_cell(event)
        if not row or not col_id:
            return
        self._ignore_next_click = True
        self.tree.after(120, lambda: setattr(self, "_ignore_next_click", False))
        self.start_edit(row, col_id)

    def _on_single_click(self, event):
        if self._ignore_next_click:
            return
        row, col_id = self._hit_cell(event)
        if not row or not col_id:
            return
        self.tree.after(1, lambda: self.start_edit(row, col_id))

    def start_edit(self, row_iid: str, col_id: str):
        if col_id not in self.col_specs:
            return
        self.popup.hide()

        if self.widget is not None:
            try:
                self.widget.destroy()
            except Exception:
                pass
            self.widget = None

        col_idx = self.col_order.index(col_id) + 1
        col = f"#{col_idx}"
        bbox = self.tree.bbox(row_iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = self.tree.set(row_iid, col)
        spec = self.col_specs[col_id]

        def finish(save: bool, move_delta):
            if self.widget is None:
                return
            self.popup.hide()

            if save:
                new_val = str(safe_entry_get(ent)).strip()
                if spec.get("type") == "combo" and spec.get("restrict", False):
                    allowed = spec.get("values_fn", lambda _row: [])(row_iid)
                    # silent: do not save invalid value
                    if new_val and new_val not in allowed:
                        ent.focus()
                        return
                # If entry was intentionally cleared (e.g. kind field) and
                # user pressed Tab without picking, keep the original value
                if not new_val and spec.get("keep_if_blank", False):
                    new_val = value
                self.tree.set(row_iid, col, new_val)
                self.app.on_cell_edited(self.tree, row_iid, col_id, new_val)

            try:
                self.widget.destroy()
            except Exception:
                pass
            self.widget = None

            if move_delta is not None:
                self._move_focus(row_iid, col_id, move_delta)

        if spec.get("type") == "combo":
            var = tk.StringVar(value=value)

            # Container so we can add a dropdown button (▼) on the right
            container = tk.Frame(self.tree)
            container.place(x=x, y=y, width=w, height=h)

            btn_w = 22
            ent = tk.Entry(container, textvariable=var, bd=0, highlightthickness=0)
            ent.place(x=0, y=0, width=max(10, w - btn_w), height=h)

            drop_btn = tk.Button(
                container, text="▼", bd=0, padx=0, pady=0,
                command=lambda: refresh_popup(force_all=True, show_all=True)
            )
            drop_btn.place(x=w - btn_w, y=0, width=btn_w, height=h)

            ent.focus()
            if col_id == "kind":
                # Clear the text so typed="" and Down shows all options immediately
                var.set("")
                ent.icursor(0)
            elif col_id in ("item_description", "lead_from", "price", "sale_price"):
                ent.selection_range(0, tk.END)
                ent.icursor(tk.END)
            else:
                ent.icursor(tk.END)
            self.widget = container

            def match(v: str, typed: str) -> bool:
                s = (v or "").lower()
                if not typed:
                    return True
                if s.startswith(typed):
                    return True
                for tok in s.replace('-', ' ').replace('_', ' ').split():
                    if tok.startswith(typed):
                        return True
                return typed in s

            _suppress_refresh_once = {"v": False}

            def refresh_popup(force_all: bool = False, show_all: bool = False):
                typed = var.get().strip().lower()
                base = spec.get("values_fn", lambda _row: [])(row_iid)
                filt = list(base) if show_all else [v for v in base if match(v, typed)]

                # show when user typed, OR when forced by dropdown button
                if filt and (typed or force_all):
                    rx = self.tree.winfo_rootx() + x
                    ry = self.tree.winfo_rooty() + y + h
                    self.popup.show(rx, ry, w, filt, on_pick=_pick)
                    bind_outside_click_close(self.popup, [ent, drop_btn, container])
                else:
                    self.popup.hide()
                    unbind_outside_click_close(self.popup)

            def _ensure_popup_has_selection():
                """Make sure popup has an active selection before moving/picking."""
                if not self.popup.visible():
                    return
                try:
                    lb = self.popup.lb
                    if lb.size() == 0:
                        return
                    cur = lb.curselection()
                    if not cur:
                        lb.selection_clear(0, tk.END)
                        lb.selection_set(0)
                        lb.activate(0)
                        lb.see(0)
                except Exception:
                    pass
            
            _force_show_all = {"v": False}

            def on_down_key(_e):
                if not self.popup.visible():
                    _force_show_all["v"] = True
                    refresh_popup(force_all=True, show_all=True)
                    _force_show_all["v"] = False
                    return "break"
                _ensure_popup_has_selection()
                self.popup.move(1)
                return "break"

            def on_up_key(_e):
                if not self.popup.visible():
                    _force_show_all["v"] = True
                    refresh_popup(force_all=True, show_all=True)
                    _force_show_all["v"] = False
                    return "break"
                _ensure_popup_has_selection()
                self.popup.move(-1)
                return "break"

            def on_accept_key(e, move_delta):
                if self.popup.visible():
                    self.popup.pick_active()
                    self.popup.hide()
                    unbind_outside_click_close(self.popup)
                    return "break"
                finish(True, move_delta)
                return "break"
            
            ent.bind("<Return>", lambda e: on_accept_key(e, 1))
            ent.bind("<Tab>",    lambda e: on_accept_key(e, 1))
            ent.bind("<Button-1>", lambda e: on_accept_key(e, 1))

            

            def _pick(v):
                _suppress_refresh_once["v"] = True
                var.set(v)
                ent.icursor(tk.END)
                self.popup.hide()
                unbind_outside_click_close(self.popup)
                finish(True, 1)

            def _should_ignore_refresh(event):
                # When popup is open, do not refresh on navigation/accept keys
                nav = {"Up", "Down", "Return", "Tab", "ISO_Left_Tab", "Escape"}
                try:
                    if event.keysym in nav and self.popup.visible():
                        return True
                except Exception:
                    pass
                return False
            
            def on_keyrelease(event):
                if _should_ignore_refresh(event):
                    return
                refresh_popup()
            
            def on_keypress(event):
                if _should_ignore_refresh(event):
                    return
                if event.keysym in ("Down", "Up"):
                    return
                self.tree.after(0, refresh_popup)
            
            ent.bind("<KeyRelease>", on_keyrelease)
            ent.bind("<KeyPress>", on_keypress)


            ent.bind("<Down>", on_down_key)
            ent.bind("<Up>", on_up_key)
            ent.bind("<Return>", lambda e: on_accept_key(e, 1))
            ent.bind("<Tab>", lambda e: on_accept_key(e, 1))
            ent.bind("<ISO_Left_Tab>", lambda e: on_accept_key(e, -1))
            ent.bind("<Shift-Tab>", lambda e: on_accept_key(e, -1))
            ent.bind("<Escape>", lambda _e: (finish(False, None), "break"))
            ent.bind("<FocusOut>", lambda _e: self.tree.after(80, lambda: (None if (not getattr(ent, "winfo_exists", lambda: 0)()) else (None if self.popup.visible() else finish(True, None)))))

        else:
            ent = tk.Entry(self.tree)
            ent.place(x=x, y=y, width=w, height=h)
            ent.insert(0, value)
            ent.focus()
            if col_id in ("price", "sale_price", "date", "voucher", "item_description", "lead_from"):
                try:
                    ent.selection_range(0,tk.END)
                    ent.icursor(tk.END)
                except Exception:
                    pass
            ent.icursor(tk.END)
            ent.bind("<Return>", lambda _e: (finish(True, 1), "break"))
            ent.bind("<Tab>", lambda _e: (finish(True, 1), "break"))
            ent.bind("<Button-1>", lambda e: (finish(True, 1), "break"))
            ent.bind("<ISO_Left_Tab>", lambda _e: (finish(True, -1), "break"))
            ent.bind("<Shift-Tab>", lambda _e: (finish(True, -1), "break"))
            ent.bind("<Escape>", lambda _e: (finish(False, None), "break"))
            ent.bind("<FocusOut>", lambda _e: finish(True, None))
            self.widget = ent

    def _move_focus(self, row_iid: str, col_id: str, delta: int):
        try:
            idx = self.col_order.index(col_id)
        except ValueError:
            return
        new_idx = max(0, min(len(self.col_order) - 1, idx + delta))
        # If already at the edge, do not reopen the same cell.
        # Just finish the edit and stop.
        if new_idx == idx and ((delta > 0 and idx == len(self.col_order) - 1) or (delta < 0 and idx == 0)):
            return
        # v7.0.5.1: row may be deleted during commit; guard
        try:
            if not self.tree.exists(row_iid):
                return
        except Exception:
            return
        if not self.tree.exists(row_iid):
            return
        try:
            self.tree.selection_set(row_iid)
            self.tree.focus(row_iid)
            self.start_edit(row_iid, self.col_order[new_idx])
        except Exception:
            return




class _SingletonIdleDialog(tk.Toplevel):
    """
    Base class providing two behaviors for tool dialogs:

    1. SINGLETON — only one instance per subclass at a time.
       Clicking the button while a window is open brings it to front.

    2. IDLE AUTO-CLOSE — closes after IDLE_SECONDS of no mouse/keyboard
       activity. A countdown label at the bottom shows remaining time,
       turning orange in the last 30 seconds.

    Usage pattern in subclass:
        def __init__(self, app):
            super().__init__(app)
            if getattr(self, "_singleton_abort", False):
                return
            # ... rest of init ...
    """

    IDLE_SECONDS = 180
    _instances   = {}           # subclass -> open window

    def __init__(self, app):
        cls = type(self)

        # --- Singleton check BEFORE creating window ---
        existing = _SingletonIdleDialog._instances.get(cls)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    existing.attributes("-topmost", True)
                    existing.after(100, lambda: existing.attributes("-topmost", False))
                    self._singleton_abort = True
                    # We must still call super().__init__ because Python
                    # requires it, but we immediately hide+destroy
                    super().__init__(app)
                    self.withdraw()
                    self.after_idle(self.destroy)
                    return
            except Exception:
                pass

        self._singleton_abort = False
        super().__init__(app)
        _SingletonIdleDialog._instances[cls] = self

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- Idle countdown ---
        self._idle_after_id = None
        self._idle_remaining = self.IDLE_SECONDS

        self._idle_bar = tk.Frame(self, bg="#F0F0F0")
        self._idle_bar.pack(side="bottom", fill="x")
        self._idle_lbl = tk.Label(
            self._idle_bar, text="", bg="#F0F0F0",
            font=("Segoe UI", 8), fg="#888888", anchor="e"
        )
        self._idle_lbl.pack(side="right", padx=6, pady=2)

        for event in ("<Motion>", "<ButtonPress>", "<KeyPress>"):
            self.bind_all(event, self._reset_idle, add="+")

        self._tick_idle()

    def _reset_idle(self, event=None):
        self._idle_remaining = self.IDLE_SECONDS
        try:
            if self._idle_lbl.winfo_exists():
                self._idle_lbl.configure(fg="#888888")
        except Exception:
            pass

    def _tick_idle(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._idle_remaining -= 1
        if self._idle_remaining <= 0:
            self._on_close()
            return
        mins, secs = divmod(self._idle_remaining, 60)
        text = f"Auto-close in {mins}:{secs:02d}  |  סגירה אוטומטית בעוד {mins}:{secs:02d}"
        color = "#CC5500" if self._idle_remaining <= 30 else "#888888"
        try:
            self._idle_lbl.configure(text=text, fg=color)
        except Exception:
            return
        self._idle_after_id = self.after(1000, self._tick_idle)

    def _on_close(self):
        cls = type(self)
        _SingletonIdleDialog._instances.pop(cls, None)
        if self._idle_after_id:
            try:
                self.after_cancel(self._idle_after_id)
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass


class SearchDialog(_SingletonIdleDialog):
    def __init__(self, app):
        super().__init__(app)
        if getattr(self, "_singleton_abort", False):
            return
        self.app = app
        self.title("Search / חיפוש")
        self.geometry("1550x860")

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        self.brand_var = tk.StringVar()
        self.sku_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.year_var = tk.StringVar()
        self.month_from_var = tk.StringVar()
        self.month_to_var = tk.StringVar()
        self.kind_var = tk.StringVar()
        self.seller_var = tk.StringVar()
        self.goal_voucher_var = tk.StringVar()
        self.goal_desc_var = tk.StringVar()
        self.goal_lead_var = tk.StringVar()
        self.price_min_var = tk.StringVar()
        self.price_max_var = tk.StringVar()
        self.date_from_var = tk.StringVar()
        self.date_to_var = tk.StringVar()

        def month_values_for_selected_year():
            y = (self.year_var.get() or "").strip()
            months = list_months_from_db_sorted()
            if not y:
                return months
            out = []
            for m in months:
                if extract_year_from_month_text(m) == y:
                    name = strip_year_from_month_text(m)
                    if name and name not in out:
                        out.append(name)
            return out

        def month_pick_transform(display_value: str):
            y = (self.year_var.get() or "").strip()
            v = (display_value or "").strip()
            if not y:
                return v
            if extract_year_from_month_text(v):
                return v
            return f"{v} {y}".strip()

        def add_field(row, col, text, var, width=18, values_fn=None, pick_transform=None, combo_values=None):
            ttk.Label(top, text=text).grid(row=row, column=col*2, sticky="w", padx=(0,6), pady=(0,6))

            if combo_values is not None:
                e = ttk.Combobox(top, textvariable=var, values=combo_values, width=width, state="readonly")
                e.grid(row=row, column=col*2+1, sticky="w", padx=(0,14), pady=(0,6))
                return e

            if values_fn is None:
                e = ttk.Entry(top, textvariable=var, width=width)
                e.grid(row=row, column=col*2+1, sticky="w", padx=(0,14), pady=(0,6))
                return e

            container, e = create_autocomplete_entry(
                top,
                var,
                values_fn,
                width=width,
                on_enter=self.run_search,
                pick_transform=pick_transform,
            )
            container.grid(row=row, column=col*2+1, sticky="w", padx=(0,14), pady=(0,6))
            return e

        add_field(0, 0, "Brand (מותג)", self.brand_var, width=13, values_fn=lambda: list_brands())
        add_field(0, 1, "Model (דגם)", self.model_var, width=13,
                  values_fn=lambda: (list_models_for_brand(self.brand_var.get().strip())
                                     if self.brand_var.get().strip() else list_models_all()))
        add_field(0, 2, "SKU (מק״ט)", self.sku_var, width=11,
                  values_fn=lambda: (list_skus_for_brand_model(self.brand_var.get().strip(), self.model_var.get().strip())
                                     if self.brand_var.get().strip() else list_skus_all()))
        add_field(0, 3, "Type (שמש/ראייה)", self.kind_var, width=13,
                  values_fn=lambda: ["Vision (ראיה)", "Sunglasses (שמש)"])
        add_field(0, 4, "Seller (מוכר)", self.seller_var, width=11, values_fn=lambda: list_sellers())

        years = [str(y) for y in list_years_from_months()]
        if years:
            self.year_var.set(years[-1])

        add_field(1, 0, "Year (שנה)", self.year_var, width=8, values_fn=lambda: [str(y) for y in list_years_from_months()])
        add_field(1, 1, "From month (מחודש)", self.month_from_var, width=11,
                  values_fn=lambda: month_values_for_selected_year(),
                  pick_transform=month_pick_transform)
        add_field(1, 2, "To month (עד חודש)", self.month_to_var, width=11,
                  values_fn=lambda: month_values_for_selected_year(),
                  pick_transform=month_pick_transform)
        add_field(1, 3, "Min price (מחיר מינ)", self.price_min_var, width=9)
        add_field(1, 4, "Max price (מחיר מקס)", self.price_max_var, width=9)

        add_field(2, 0, "Voucher (ואוצ'ר)", self.goal_voucher_var, width=13, values_fn=lambda: list_goal_vouchers())
        add_field(2, 1, "Description (תיאור מוצר)", self.goal_desc_var, width=18, values_fn=lambda: list_goal_descriptions())
        add_field(2, 2, "Lead from (מקור הגעה)", self.goal_lead_var, width=13, values_fn=lambda: list_goal_leads())
        add_field(2, 3, "Date from (DD/MM/YY)", self.date_from_var, width=11)
        add_field(2, 4, "Date to (DD/MM/YY)", self.date_to_var, width=11)

        btn_frame = ttk.Frame(top)
        btn_frame.grid(row=3, column=0, columnspan=10, sticky="w", padx=(0,0), pady=(4,6))
        ttk.Button(btn_frame, text="Search / חפש", command=self.run_search).pack(side="left", padx=(0,8))
        ttk.Button(btn_frame, text="Reset sort / איפוס מיון", command=self._reset_active_sort).pack(side="left", padx=(0,8))
        ttk.Button(btn_frame, text="Back / חזור", command=self.destroy).pack(side="left")

        self.bind("<Return>", lambda _e: self.run_search())

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)

        self.sales_frame = ttk.Frame(self.nb)
        self.goals_frame = ttk.Frame(self.nb)
        self.inv_frame = ttk.Frame(self.nb)
        self.nb.add(self.goals_frame, text="Goals / יעדים")
        self.nb.add(self.sales_frame, text="Sales / מכירות")
        self.nb.add(self.inv_frame, text="Inventory / מלאי")
        self.sales_summary = ttk.Label(self.sales_frame, text="", padding=(6, 4))
        self.sales_summary.pack(anchor="w")
        self.goals_summary = ttk.Label(self.goals_frame, text="", padding=(6, 4))
        self.goals_summary.pack(anchor="w")

        self.inv_tree = ttk.Treeview(self.inv_frame, columns=("brand","model","sku","onhand"), show="headings")
        for c,t,w in [("brand","Brand (מותג)",220),("model","Model (דגם)",200),("sku","SKU (מק״ט)",160),("onhand","On Hand (במלאי)",140)]:
            self.inv_tree.heading(c, text=t, command=lambda cc=c: treeview_sort_column(self.inv_tree, cc, False))
            self.inv_tree.column(c, width=w)
        self.inv_tree.pack(fill="both", expand=True)

        self.sales_tree = ttk.Treeview(self.sales_frame, columns=("month","brand","model","sku","color","kind","qty","price","seller","notes"), show="headings")
        cols = [
            ("month","Month (חודש)",120),
            ("brand","Brand (מותג)",120),
            ("model","Model (דגם)",120),
            ("sku","SKU (מק״ט)",120),
            ("color","Color (צבע)",120),
            ("kind","Type (שמש/ראייה)",120),
            ("qty","Qty (כמות)",90),
            ("price","Sale price (מחיר מכירה)",120),
            ("seller","Seller (מוכר)",120),
            ("notes","Notes (הערות)",120),
        ]
        for c,t,w in cols:
            self.sales_tree.heading(c, text=t, command=lambda cc=c: treeview_sort_column(self.sales_tree, cc, False))
            self.sales_tree.column(c, width=w)
        self.sales_tree.pack(fill="both", expand=True)

        self.goals_tree = ttk.Treeview(self.goals_frame, columns=("month","date","voucher","item_description","sale_price","lead_from","seller"), show="headings")
        goal_cols = [
            ("month","Month (חודש)",120),
            ("date","Date (תאריך)",100),
            ("voucher","Voucher (ואוצ'ר)",110),
            ("item_description","Item Description (תיאור מוצר)",300),
            ("sale_price","Sale Price (סכום מכירה)",130),
            ("lead_from","Lead from (מקור הגעה)",170),
            ("seller","Seller (מוכר)",120),
        ]
        for c,t,w in goal_cols:
            self.goals_tree.heading(c, text=t, command=lambda cc=c: treeview_sort_column(self.goals_tree, cc, False))
            self.goals_tree.column(c, width=w)
        self.goals_tree.pack(fill="both", expand=True)

        self.run_search()

    def _reset_active_sort(self):
        try:
            idx = self.nb.index("current")
        except Exception:
            return
        if idx == 0:
            treeview_reset_to_default(self.sales_tree)
        elif idx == 1:
            treeview_reset_to_default(self.goals_tree)
        else:
            treeview_reset_to_default(self.inv_tree)

    def run_search(self):
        brand = self.brand_var.get().strip()
        sku = self.sku_var.get().strip()
        model = self.model_var.get().strip()
        year = self.year_var.get().strip()
        month_from = self.month_from_var.get().strip()
        month_to = self.month_to_var.get().strip()
        kind = self.kind_var.get().strip()
        seller = self.seller_var.get().strip()
        goal_voucher = self.goal_voucher_var.get().strip()
        goal_desc = self.goal_desc_var.get().strip()
        goal_lead = self.goal_lead_var.get().strip()
        price_min_s = self.price_min_var.get().strip()
        price_max_s = self.price_max_var.get().strip()
        date_from_s = normalize_goal_date_input(self.date_from_var.get().strip(), "")
        date_to_s   = normalize_goal_date_input(self.date_to_var.get().strip(), "")
        def _date_to_int(d):
            try:
                parts = d.split("/")
                dd, mm, yy = int(parts[0]), int(parts[1]), int(parts[2])
                return (2000 + yy) * 10000 + mm * 100 + dd
            except Exception:
                return None
        date_from_int = _date_to_int(date_from_s) if date_from_s else None
        date_to_int   = _date_to_int(date_to_s)   if date_to_s   else None
        try:
            price_min = float(price_min_s) if price_min_s else None
        except Exception:
            price_min = None
        try:
            price_max = float(price_max_s) if price_max_s else None
        except Exception:
            price_max = None

        if self.month_from_var.get().strip():
            self.month_from_var.set(normalize_month_text(self.month_from_var.get()))
        if self.month_to_var.get().strip():
            self.month_to_var.set(normalize_month_text(self.month_to_var.get()))

        month_from = self.month_from_var.get().strip()
        month_to = self.month_to_var.get().strip()

        months_all = list_months_from_db_sorted()
        if year:
            months_year = [m for m in months_all if str(extract_year_from_month_name(m) or "") == year]
        else:
            months_year = months_all

        def _idx(lst, val):
            try:
                return lst.index(val)
            except ValueError:
                return None

        if month_from or month_to:
            i_from = _idx(months_year, month_from) if month_from else 0
            i_to = _idx(months_year, month_to) if month_to else (len(months_year) - 1)
            if i_from is None:
                i_from = 0
            if i_to is None:
                i_to = len(months_year) - 1
            if i_from > i_to:
                i_from, i_to = i_to, i_from
            allowed_months = months_year[i_from:i_to+1]
        else:
            allowed_months = months_year

        self.inv_tree.delete(*self.inv_tree.get_children())
        with connect() as c:
            q = "SELECT id, brand, sku, model FROM items WHERE 1=1"
            params = []
            if brand:
                q += " AND brand LIKE ?"; params.append(f"%{brand}%")
            if sku:
                q += " AND sku LIKE ?"; params.append(f"%{sku}%")
            if model:
                q += " AND model LIKE ?"; params.append(f"%{model}%")
            q += " ORDER BY brand, model, sku"
            for item_id, b, s, m in c.execute(q, params).fetchall():
                self.inv_tree.insert("", "end", values=(b, m, s, inventory_on_hand(c, int(item_id))))
        treeview_capture_default_order(self.inv_tree)

        self.sales_tree.delete(*self.sales_tree.get_children())
        with connect() as c:
            q = (
                "SELECT s.month, i.brand, i.model, i.sku, s.color, COALESCE(s.kind,'vision'), s.qty, s.price, s.seller, s.notes, s.id "
                "FROM sales s JOIN items i ON i.id=s.item_id WHERE 1=1"
            )
            params = []
            if allowed_months:
                placeholders = ",".join("?" for _ in allowed_months)
                q += f" AND s.month IN ({placeholders})"
                params.extend(allowed_months)
            if brand:
                q += " AND i.brand LIKE ?"; params.append(f"%{brand}%")
            if sku:
                q += " AND i.sku LIKE ?"; params.append(f"%{sku}%")
            if model:
                q += " AND i.model LIKE ?"; params.append(f"%{model}%")
            if kind:
                q += " AND COALESCE(s.kind,'vision')=?"; params.append(kind)
            if seller:
                q += " AND COALESCE(s.seller,'') LIKE ?"; params.append(f"%{seller}%")
            if price_min is not None:
                q += " AND s.price >= ?"; params.append(price_min)
            if price_max is not None:
                q += " AND s.price <= ?"; params.append(price_max)
            q += " ORDER BY s.id DESC"
            rows = c.execute(q, params).fetchall()
            def _sort_key(r):
                k = month_to_key(r[0]) or (9999, 99)
                return (k[0], k[1], -int(r[-1]))
            rows.sort(key=_sort_key)
            total_qty = 0
            total_amount = 0.0
            for r in rows:
                self.sales_tree.insert("", "end", values=r[:-1])
                try:
                    qty = int(r[6] or 0)
                except Exception:
                    qty = 0
                try:
                    price = float(r[7] or 0.0)
                except Exception:
                    price = 0.0
                total_qty += qty
                total_amount += qty * price
        self.sales_summary.config(text=f"Total items: {total_qty} | Total sales: ₪{total_amount:,.2f}")
        treeview_capture_default_order(self.sales_tree)

        self.goals_tree.delete(*self.goals_tree.get_children())
        with connect() as c:
            q = "SELECT month, date, voucher, item_description, sale_price, lead_from, seller FROM goals WHERE 1=1"
            params = []
            if allowed_months:
                placeholders = ",".join("?" for _ in allowed_months)
                q += f" AND month IN ({placeholders})"
                params.extend(allowed_months)
            if seller:
                q += " AND COALESCE(seller,'') LIKE ?"; params.append(f"%{seller}%")
            if goal_voucher:
                q += " AND COALESCE(voucher,'') LIKE ?"; params.append(f"%{goal_voucher}%")
            if goal_desc:
                q += " AND COALESCE(item_description,'') LIKE ?"; params.append(f"%{goal_desc}%")
            if goal_lead:
                q += " AND COALESCE(lead_from,'') LIKE ?"; params.append(f"%{goal_lead}%")
            if price_min is not None:
                q += " AND sale_price >= ?"; params.append(price_min)
            if price_max is not None:
                q += " AND sale_price <= ?"; params.append(price_max)
            q += " ORDER BY id DESC"
            rows = c.execute(q, params).fetchall()
        if date_from_int is not None or date_to_int is not None:
            filtered = []
            for r in rows:
                d_int = _date_to_int(normalize_goal_date_input(r[1] or "", ""))
                if d_int is None:
                    filtered.append(r); continue
                if date_from_int is not None and d_int < date_from_int:
                    continue
                if date_to_int is not None and d_int > date_to_int:
                    continue
                filtered.append(r)
            rows = filtered
        total_goals = 0.0
        total_goal_rows = 0
        for r in rows:
            self.goals_tree.insert("", "end", values=r)
            total_goal_rows += 1
            try:
                total_goals += float(r[4] or 0)
            except Exception:
                pass
        self.goals_summary.config(text=f"Total rows: {total_goal_rows} | Total goals amount: ₪{total_goals:,.2f}")
        treeview_capture_default_order(self.goals_tree)




class YearlySummaryDialog(_SingletonIdleDialog):
    """
    Side-by-side yearly comparison: two panels, each showing 12 months with
    per-seller totals, monthly total, editable target, and remaining.
    Year is selectable via a clickable label at the top of each panel.

    Layout uses grid geometry with fixed pixel widths so Hebrew text and
    ₪ symbols never break column alignment.
    """

    # Colors
    _CLR_HEADER_YEAR   = "#FFFF00"   # yellow  – year header
    _CLR_HEADER_COLS   = "#00B0F0"   # blue    – column headers
    _CLR_REMAINING_NEG = "#FF9999"   # light red   – missed target
    _CLR_REMAINING_POS = "#C6EFCE"   # light green – hit target
    _CLR_MONTH         = "#FFE699"   # orange  – month name cell
    _CLR_TOTAL_FOOTER  = "#BDD7EE"   # blue    – annual totals row
    _CLR_TARGET_BG     = "#FFF2CC"   # pale yellow – editable target
    _CLR_ROW_EVEN      = "#FFFFFF"
    _CLR_ROW_ODD       = "#F5F5F5"

    # Pixel widths per column type
    _PX = {
        "month":     70,
        "seller":    70,
        "total":     70,
        "target":    80,
        "remaining": 80,
    }
    _ROW_H = 30   # row height in pixels

    def __init__(self, app):
        super().__init__(app)
        if getattr(self, "_singleton_abort", False):
            return
        self.app = app
        self.title("Yearly Summary / סיכום יעדים שנתי")
        self.geometry("1550x820")
        self.minsize(900, 500)

        all_years = list_years_from_all_data()
        if not all_years:
            all_years = [datetime.now().year]
        y1 = all_years[-1]
        y2 = all_years[-2] if len(all_years) >= 2 else y1
        self._year_vars = [tk.IntVar(value=y1), tk.IntVar(value=y2)]

        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill="x", pady=(0, 6))
        ttk.Button(btn_row, text="Refresh / רענן", command=self._refresh_both).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Close / סגור",   command=self.destroy).pack(side="left", padx=4)
        tk.Label(btn_row, text="  |  לחץ על השנה כדי לשנות אותה  •  Click the year to change it",
                 font=("Segoe UI", 9), fg="#555555").pack(side="left", padx=12)
        tk.Label(btn_row, text="הערכים לקוחים מיעדים בלבד, לא ממכירות  •  Values taken from goals only, not sales",
                 font=("Segoe UI", 9, "italic"), fg="#CC6600").pack(side="left", padx=4)

        panels_frame = ttk.Frame(outer)
        panels_frame.pack(fill="both", expand=True)
        panels_frame.columnconfigure(0, weight=1)
        panels_frame.columnconfigure(1, weight=1)
        panels_frame.rowconfigure(0, weight=1)

        self._panels = []
        for col_idx in range(2):
            pf = tk.Frame(panels_frame, relief="groove", bd=1)
            pf.grid(row=0, column=col_idx, sticky="nsew",
                    padx=(0, 6) if col_idx == 0 else (0, 0))
            pf.rowconfigure(1, weight=1)
            pf.columnconfigure(0, weight=1)
            panel = self._build_panel(pf, col_idx)
            self._panels.append(panel)

        self._refresh_both()

    # ------------------------------------------------------------------
    # Panel scaffold
    # ------------------------------------------------------------------
    def _build_panel(self, parent, panel_idx):
        p = {"panel_idx": panel_idx}

        # ---- Yellow year-header bar ----
        hdr = tk.Frame(parent, bg=self._CLR_HEADER_YEAR, height=36)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)

        year_btn = tk.Button(
            hdr,
            textvariable=self._year_vars[panel_idx],
            bg=self._CLR_HEADER_YEAR,
            font=("Segoe UI", 13, "bold"),
            relief="flat", bd=0,
            cursor="hand2",
            activebackground="#FFEE00",
        )
        year_btn.place(relx=0.5, rely=0.5, anchor="center")
        year_btn.bind("<Button-1>", lambda e, idx=panel_idx: self._pick_year(idx))
        p["year_btn"] = year_btn

        # ---- Scrollable canvas ----
        canvas_frame = tk.Frame(parent)
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bg="white")
        vsb = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        inner = tk.Frame(canvas, bg="white")
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>", lambda e: (
            canvas.configure(scrollregion=canvas.bbox("all")),
            canvas.itemconfig(win_id, width=canvas.winfo_width()),
        ))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _wheel)
        inner.bind("<MouseWheel>", _wheel)

        p["inner"]  = inner
        p["canvas"] = canvas
        return p

    # ------------------------------------------------------------------
    # Year picker
    # ------------------------------------------------------------------
    def _pick_year(self, panel_idx):
        popup = tk.Toplevel(self)
        popup.title("Select Year / בחר שנה")
        popup.geometry("170x210")
        popup.resizable(False, False)
        popup.grab_set()

        all_years = list_years_from_all_data() or [datetime.now().year]
        ttk.Label(popup, text="בחר שנה / Choose year",
                  font=("Segoe UI", 10, "bold")).pack(pady=6)
        lb = tk.Listbox(popup, height=8, exportselection=False, font=("Segoe UI", 11))
        lb.pack(fill="both", expand=True, padx=8, pady=4)
        for y in reversed(all_years):
            lb.insert(tk.END, str(y))
        cur = self._year_vars[panel_idx].get()
        for i, y in enumerate(reversed(all_years)):
            if y == cur:
                lb.selection_set(i); lb.see(i); break

        def _pick():
            sel = lb.curselection()
            if sel:
                self._year_vars[panel_idx].set(int(lb.get(sel[0])))
                self._refresh_panel(self._panels[panel_idx])
            popup.destroy()

        lb.bind("<Double-Button-1>", lambda _e: _pick())
        ttk.Button(popup, text="OK", command=_pick).pack(pady=4)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def _refresh_both(self):
        for p in self._panels:
            self._refresh_panel(p)

    def _refresh_panel(self, p):
        inner     = p["inner"]
        canvas    = p["canvas"]
        panel_idx = p["panel_idx"]
        year      = self._year_vars[panel_idx].get()

        for w in inner.winfo_children():
            w.destroy()

        summary, sellers = get_yearly_summary(year)
        seller_cols = sellers
        all_cols    = ["month"] + seller_cols + ["total", "target", "remaining"]

        col_labels = {"month": str(year), "total": "סהכ",
                      "target": "יעד חודשי", "remaining": "נותר עד יעד"}
        for s in seller_cols:
            col_labels[s] = s

        # pixel width per column
        def _pw(col):
            if col == "month":     return self._PX["month"]
            if col == "total":     return self._PX["total"]
            if col == "target":    return self._PX["target"]
            if col == "remaining": return self._PX["remaining"]
            return self._PX["seller"]

        col_widths = [_pw(c) for c in all_cols]

        # configure inner frame columns
        for ci, w in enumerate(col_widths):
            inner.columnconfigure(ci, minsize=w, weight=0)

        # ---- header row (grid row 0) ----
        for ci, col in enumerate(all_cols):
            bg = self._CLR_HEADER_YEAR if col == "month" else self._CLR_HEADER_COLS
            lbl = tk.Label(
                inner, text=col_labels[col], bg=bg,
                font=("Segoe UI", 10, "bold"),
                anchor="center", relief="ridge", bd=1,
                width=0,   # width managed by grid minsize
            )
            lbl.grid(row=0, column=ci, sticky="nsew", ipadx=2, ipady=4)

        # ---- data rows ----
        for ri, month_data in enumerate(summary):
            grid_row = ri + 1
            bg = self._CLR_ROW_EVEN if ri % 2 == 0 else self._CLR_ROW_ODD
            self._fill_data_row(inner, grid_row, col_widths,
                                month_data, all_cols, seller_cols, bg, year)

        # ---- totals row ----
        self._fill_total_row(inner, len(summary) + 1, summary, all_cols, seller_cols)

        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

    # ------------------------------------------------------------------
    # Row fill helpers
    # ------------------------------------------------------------------
    def _cell(self, parent, grid_row, col_idx, text, bg,
              bold=False, cursor="", tag=None):
        lbl = tk.Label(
            parent, text=text, bg=bg,
            font=("Segoe UI", 9, "bold" if bold else "normal"),
            anchor="center", relief="ridge", bd=1,
            width=0,
        )
        lbl.grid(row=grid_row, column=col_idx, sticky="nsew", ipadx=2, ipady=0)
        parent.rowconfigure(grid_row, minsize=self._ROW_H)
        if cursor:
            lbl.configure(cursor=cursor)
        if tag:
            lbl._tag = tag
        return lbl

    def _fill_data_row(self, parent, grid_row, col_widths,
                       month_data, all_cols, seller_cols, bg, year):
        for ci, col in enumerate(all_cols):
            if col == "month":
                self._cell(parent, grid_row, ci,
                           month_data["month_name"], self._CLR_MONTH, bold=True)

            elif col in seller_cols:
                val = month_data["seller_totals"].get(col, 0.0)
                self._cell(parent, grid_row, ci,
                           f"₪{val:,.0f}" if val else "0", bg)

            elif col == "total":
                gt = month_data["grand_total"]
                self._cell(parent, grid_row, ci,
                           f"₪{gt:,.0f}" if gt else "0", bg, bold=True)

            elif col == "target":
                tgt      = month_data["target"]
                tgt_text = f"₪{tgt:,.0f}" if tgt else "0"
                he_month = month_data["month_name"]
                lbl = self._cell(parent, grid_row, ci,
                                 tgt_text, self._CLR_TARGET_BG,
                                 bold=True, cursor="hand2")
                lbl.bind("<Button-1>",
                         lambda e, hm=he_month, yr=year, lw=lbl:
                             self._inline_edit_target(lw, hm, yr))

            elif col == "remaining":
                rem    = month_data["remaining"]
                tgt    = month_data["target"]
                rem_bg = (self._CLR_REMAINING_POS if rem < 0
                          else (self._CLR_REMAINING_NEG if tgt > 0 else bg))
                self._cell(parent, grid_row, ci,
                           f"₪{rem:,.0f}", rem_bg, bold=(rem < 0))

    def _fill_total_row(self, parent, grid_row, summary, all_cols, seller_cols):
        for ci, col in enumerate(all_cols):
            if col == "month":
                self._cell(parent, grid_row, ci, 'סה"כ שנתי',
                           self._CLR_TOTAL_FOOTER, bold=True)
            elif col in seller_cols:
                total = sum(m["seller_totals"].get(col, 0.0) for m in summary)
                self._cell(parent, grid_row, ci,
                           f"₪{total:,.0f}" if total else "0",
                           self._CLR_TOTAL_FOOTER, bold=True)
            elif col == "total":
                total = sum(m["grand_total"] for m in summary)
                self._cell(parent, grid_row, ci,
                           f"₪{total:,.0f}" if total else "0",
                           self._CLR_TOTAL_FOOTER, bold=True)
            elif col == "target":
                total = sum(m["target"] for m in summary)
                self._cell(parent, grid_row, ci,
                           f"₪{total:,.0f}" if total else "0",
                           self._CLR_TARGET_BG, bold=True)
            elif col == "remaining":
                total  = sum(m["remaining"] for m in summary)
                rem_bg = (self._CLR_REMAINING_POS if total < 0
                          else self._CLR_REMAINING_NEG)
                self._cell(parent, grid_row, ci,
                           f"₪{total:,.0f}", rem_bg, bold=True)

    # ------------------------------------------------------------------
    # Inline target edit — uses place() overlay, never touches pack/grid
    # ------------------------------------------------------------------
    def _inline_edit_target(self, label_widget, he_month, year):
        old_val  = get_monthly_target(he_month, year)
        old_text = str(int(old_val)) if old_val == int(old_val) else str(old_val)

        # Get label geometry relative to its parent (the inner grid frame)
        label_widget.update_idletasks()
        x = label_widget.winfo_x()
        y = label_widget.winfo_y()
        w = label_widget.winfo_width()
        h = label_widget.winfo_height()

        entry = tk.Entry(
            label_widget.master,
            font=("Segoe UI", 9, "bold"),
            justify="center",
            bg="#FFFACD",
            relief="solid", bd=1,
        )
        entry.insert(0, old_text)
        entry.select_range(0, tk.END)
        # Overlay exactly on top of the label — no layout shift at all
        entry.place(x=x, y=y, width=w, height=h)
        entry.lift()
        entry.focus_set()

        committed = [False]

        def _commit(e=None):
            if committed[0]:
                return
            committed[0] = True
            try:
                raw = entry.get().strip().replace(",", "").replace("₪", "")
                new_val = float(raw) if raw else 0.0
            except ValueError:
                new_val = old_val
            entry.place_forget()
            entry.destroy()
            set_monthly_target(he_month, year, new_val)
            # find which panel owns this year and refresh it
            for p in self._panels:
                if self._year_vars[p["panel_idx"]].get() == year:
                    self._refresh_panel(p)
                    break

        def _cancel(e=None):
            if committed[0]:
                return
            committed[0] = True
            entry.place_forget()
            entry.destroy()

        entry.bind("<Return>",  _commit)
        entry.bind("<KP_Enter>", _commit)
        entry.bind("<FocusOut>", _commit)
        entry.bind("<Escape>",  _cancel)


class ManageMonthsDialog(_SingletonIdleDialog):
    """
    Unified month management window — one tab per year, each showing that
    year's months. Operates on both sales and goals tables at once.
    """
    def __init__(self, app):
        super().__init__(app)
        if getattr(self, "_singleton_abort", False):
            return
        self.app = app
        self.title("Manage Months / ניהול חודשים")
        self.geometry("540x460")
        self.resizable(False, False)

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        # ---- Year notebook (left side) ----
        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        ttk.Label(left, text="Months (חודשים):", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 4))

        self.year_nb = ttk.Notebook(left)
        self.year_nb.pack(fill="both", expand=True)
        self.year_nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

        # listbox dict: year -> Listbox widget
        self._year_listboxes = {}

        # ---- Right side: actions ----
        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="n")

        r = 0
        ttk.Label(right, text="Add new month / הוסף חודש:").grid(row=r, column=0, sticky="w"); r+=1
        self.add_e = ttk.Entry(right, width=24)
        self.add_e.grid(row=r, column=0, sticky="we", pady=(2, 0)); r+=1
        self.add_e.bind("<Return>", lambda e: self.add())
        ttk.Button(right, text="➕ Add / הוסף", command=self.add).grid(
            row=r, column=0, sticky="we", pady=(4, 14)); r+=1

        ttk.Label(right, text="Rename selected / שנה שם:").grid(row=r, column=0, sticky="w"); r+=1
        self.ren_e = ttk.Entry(right, width=24)
        self.ren_e.grid(row=r, column=0, sticky="we", pady=(2, 0)); r+=1
        self.ren_e.bind("<Return>", lambda e: self.rename())
        ttk.Button(right, text="✏️ Rename / שנה", command=self.rename).grid(
            row=r, column=0, sticky="we", pady=(4, 14)); r+=1

        ttk.Label(right, text="Delete selected month / מחק חודש:").grid(row=r, column=0, sticky="w"); r+=1
        ttk.Label(right, text="(removes all sales & goals for that month)",
                  font=("Segoe UI", 8), foreground="gray").grid(row=r, column=0, sticky="w"); r+=1
        ttk.Button(right, text="❌ Delete Month / מחק חודש", command=self.delete).grid(
            row=r, column=0, sticky="we", pady=(4, 0)); r+=1

        ttk.Button(right, text="Close / סגור", command=self.destroy).grid(
            row=r, column=0, sticky="e", pady=(24, 0))

        outer.columnconfigure(0, weight=1)

        self.reload()

    # ------------------------------------------------------------------
    def _all_months_by_year(self):
        """Return dict {year: [sorted month strings]} from DB + UI."""
        months = set(list_all_months_from_db())
        # also include months only in UI (not yet committed to DB)
        months |= set(self.app.months_in_ui())
        months |= set(self.app.goals_months_in_ui())
        by_year = {}
        for m in months:
            y = extract_year_from_month_name(m)
            if y:
                by_year.setdefault(y, []).append(m)
        def key_fn(m):
            k = month_to_key(m)
            return k if k else (9999, 99, m)
        for y in by_year:
            by_year[y].sort(key=key_fn)
        return by_year

    def reload(self, keep_year=None, keep_month=None):
        """Rebuild year tabs. Restore focus to keep_year/keep_month if given."""
        by_year = self._all_months_by_year()
        years = sorted(by_year.keys(), reverse=True)  # newest first

        # Remember current state if not specified
        if keep_year is None:
            keep_year = self._current_year()
        if keep_month is None:
            keep_month = self.selected()

        # Destroy existing tabs
        for tab in self.year_nb.tabs():
            self.year_nb.forget(tab)
        self._year_listboxes.clear()

        if not years:
            return

        target_tab_idx = 0
        for i, year in enumerate(years):
            frm = ttk.Frame(self.year_nb)
            self.year_nb.add(frm, text=str(year))

            lb = tk.Listbox(frm, height=12, width=16, font=("Segoe UI", 10),
                            exportselection=False)
            lb.pack(fill="both", expand=True, padx=4, pady=4)
            lb.bind("<<ListboxSelect>>", self._on_select)
            self._year_listboxes[year] = lb

            for m in by_year[year]:
                lb.insert(tk.END, m)

            if year == keep_year:
                target_tab_idx = i

        # Switch to the right tab
        self.year_nb.select(target_tab_idx)

        # Restore listbox selection
        if keep_month:
            year_of_month = extract_year_from_month_name(keep_month)
            lb = self._year_listboxes.get(year_of_month)
            if lb:
                for i in range(lb.size()):
                    if lb.get(i) == keep_month:
                        lb.selection_set(i)
                        lb.see(i)
                        self.ren_e.delete(0, tk.END)
                        self.ren_e.insert(0, keep_month)
                        break

    def _current_year(self):
        """Return the year integer of the currently visible notebook tab, or None."""
        try:
            tab_text = self.year_nb.tab(self.year_nb.select(), "text")
            return int(tab_text)
        except Exception:
            return None

    def _on_tab_change(self, e=None):
        # Clear rename entry when switching years
        self.ren_e.delete(0, tk.END)

    def _on_select(self, e=None):
        month = self.selected()
        if month:
            self.ren_e.delete(0, tk.END)
            self.ren_e.insert(0, month)

    def selected(self):
        """Return the selected month string from whichever year tab is active."""
        year = self._current_year()
        lb = self._year_listboxes.get(year)
        if lb:
            sel = lb.curselection()
            if sel:
                return lb.get(sel[0])
        return None

    def add(self):
        name = normalize_month_text(self.add_e.get())
        if not name:
            messagebox.showwarning("Missing", "Enter month name / הכנס שם חודש")
            return
        self.app.ensure_month_tab(name)
        self.app.ensure_goal_month_tab(name)
        self.app.select_month_tab(name)
        self.app.select_goal_month_tab(name)
        self.add_e.delete(0, tk.END)
        year = extract_year_from_month_name(name)
        self.reload(keep_year=year, keep_month=name)

    def rename(self):
        old = self.selected()
        new = normalize_month_text(self.ren_e.get())
        if not old:
            messagebox.showwarning("Nothing selected", "בחר חודש / Select a month first")
            return
        if not new or new == old:
            messagebox.showwarning("Missing", "Enter a different new name / הכנס שם חדש שונה")
            return
        with connect() as c:
            c.execute("UPDATE sales SET month=? WHERE month=?", (new, old))
            c.execute("UPDATE goals SET month=? WHERE month=?", (new, old))
        self.app.rename_month_tab(old, new)
        self.app.rename_goal_month_tab(old, new)
        year = extract_year_from_month_name(new)
        self.reload(keep_year=year, keep_month=new)

    def delete(self):
        month = self.selected()
        if not month:
            messagebox.showwarning("Nothing selected", "בחר חודש למחיקה / Select a month to delete")
            return
        confirm = messagebox.askyesno(
            "אישור מחיקה / Confirm Delete",
            f"מחיקת חודש: {month}\n\nפעולה זו תמחק את כל המכירות והיעדים של חודש זה.\nThis will permanently delete all sales AND goals for {month}.\n\nCannot be undone. Continue?",
            icon="warning"
        )
        if confirm:
            year = self._current_year()
            self.app.delete_month_tab(month)
            self.app.delete_goal_month_tab(month)
            self.reload(keep_year=year)


# Goals toolbar uses the same unified dialog
ManageGoalsMonthsDialog = ManageMonthsDialog

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.setup_styles()
        # v7.0.2: per-sale-row resolved item cache (row_iid -> item_id). No behavior change yet.
        self._sale_row_resolved_cache = {}
        APP_VERSION = "V8"
        self.title(f"Lior Optica Sales & Inventory – {APP_VERSION}")
        self.geometry("1550x900")
        init_db()

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        self.sales_tab = ttk.Frame(self.nb)
        self.goals_tab = ttk.Frame(self.nb)
        self.inv_tab = ttk.Frame(self.nb)
        self.nb.add(self.goals_tab, text="Goals / יעדים")
        self.nb.add(self.sales_tab, text="Sales / מכירות")
        self.nb.add(self.inv_tab, text="Inventory / מלאי")

        self.sales_months = None
        self.month_tab_frames = {}
        self.month_tab_summaries = {}
        self.month_tab_trees = {}
        self.month_tab_editors = {}
        self._new_row_seq = 0
        self._last_goal_row_iid = None

        self.goals_months = None
        self.goals_month_tab_frames = {}
        self.goals_month_tab_summaries = {}
        self.goals_month_tab_trees = {}
        self.goals_month_tab_editors = {}
        self._new_goal_row_seq = 0

        self.tip_messages = [
            "Tip: Click a cell to edit. TAB to move.",
            "Tip: Use + on the keyboard to quickly add a new row.",
            "Tip: Click a column header to sort, then use Reset sort to restore the original order.",
            "Tip: Every day when the app is launched for the first time, a backup of the database is saved.",
            "Tip: You can use the 'Search' button to filter every data possible."
        ]
        self._tip_index = 0
        self.tip_var = tk.StringVar(value=self.tip_messages[0])
        self._tip_after_id = None
        self._tip_restore_after_id = None

        self.build_sales()
        self.build_goals()
        self.build_inventory()

        self.bind_all("+", lambda _e: self._add_row_if_current_tab())
        self.bind_all("<Key-plus>", lambda _e: self._add_row_if_current_tab())
        self.bind_all("<KP_Add>", lambda _e: self._add_row_if_current_tab())

        self._reclass_confirmed = {}
        self._reclass_prompt_inflight = set()
        self._reclass_prompt_done = set()

        self._backup_after_id = None
        self._start_backup_scheduler()
        self._start_tip_rotation()

    def _start_backup_scheduler(self):
        now = datetime.now()
        morning_target = now.replace(
            hour=BACKUP_TIME_HOUR,
            minute=BACKUP_TIME_MINUTE,
            second=0,
            microsecond=0
        )

        # If app opens after 06:00 and today's backup does not exist yet,
        # create it immediately.
        if now >= morning_target and not has_backup_for_date(now):
            try:
                ok, _ = create_daily_backup(now)
                if ok:
                    self.show_temporary_tip("Database backed up successfully!")
            except Exception:
                pass

        self._schedule_next_backup()

    def _schedule_next_backup(self):
        if self._backup_after_id is not None:
            try:
                self.after_cancel(self._backup_after_id)
            except Exception:
                pass
            self._backup_after_id = None

        now = datetime.now()

        next_run = now.replace(hour=16, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)

        delay_ms = max(1000, int((next_run - now).total_seconds() * 1000))
        self._backup_after_id = self.after(delay_ms, self._run_scheduled_backup)

    def _run_scheduled_backup(self):
        try:
            ok, _ = create_daily_backup(datetime.now())
            if ok:
                self.show_temporary_tip("Database backed up successfully!")
        except Exception:
            pass
        finally:
            self._backup_after_id = None
            self._schedule_next_backup()

    def _start_tip_rotation(self):
        self._schedule_next_tip_rotation()

    def _schedule_next_tip_rotation(self, delay_ms=12000):
        if self._tip_after_id is not None:
            try:
                self.after_cancel(self._tip_after_id)
            except Exception:
                pass
        self._tip_after_id = self.after(delay_ms, self._advance_tip_message)

    def _advance_tip_message(self):
        if self._tip_restore_after_id is not None:
            self._schedule_next_tip_rotation()
            return
        if self.tip_messages:
            self._tip_index = (self._tip_index + 1) % len(self.tip_messages)
            self.tip_var.set(self.tip_messages[self._tip_index])
        self._schedule_next_tip_rotation()

    def show_temporary_tip(self, message: str, duration_ms: int = 8000):
        self.tip_var.set(message)
        if self._tip_restore_after_id is not None:
            try:
                self.after_cancel(self._tip_restore_after_id)
            except Exception:
                pass
        self._tip_restore_after_id = self.after(duration_ms, self._restore_rotating_tip)

    def _restore_rotating_tip(self):
        self._tip_restore_after_id = None
        if self.tip_messages:
            self.tip_var.set(self.tip_messages[self._tip_index])
        self._schedule_next_tip_rotation()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("vista")
    
        bg = "#f5f6fa"
    
        self.configure(bg=bg)
    
        style.configure(".", background=bg)
        style.configure("TFrame", background=bg)
        style.configure("TNotebook", background=bg)
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI", 10))
        style.map(
            "TNotebook.Tab",
            foreground=[("selected", "blue")],
            font=[("selected", ("Segoe UI", 10, "bold"))]
        )
    
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=28)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
    
        style.configure("TButton", padding=(10, 6))
        style.configure("BigAdd.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 10))
        style.configure(
            "TNotebook.Tab",
            padding=(14, 10),
            font=("Segoe UI", 10)
        )
        
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#ffffff")],
            foreground=[("selected", "blue")],
            font=[("selected", ("Segoe UI", 10, "bold"))],
            relief=[("selected", "raised"), ("!selected", "flat")]
        )

    # ----- Sales -----

    def build_sales(self):
        top = ttk.Frame(self.sales_tab, padding=6)
    
        top.pack(fill="x")

        add_row_frame = ttk.Frame(self.sales_tab)
        add_row_frame.pack(fill="x", pady=(6, 2))

        ttk.Button(
            add_row_frame,
            text="➕ Add Sale Row / שורת מכירה חדשה",
            command=self.add_sale_row
        ).pack(side="left", padx=6)

        add_btn = add_row_frame.winfo_children()[0]
        add_btn.configure(style="BigAdd.TButton")

        self.tip_label = tk.Label(
            add_row_frame,
            textvariable=self.tip_var,
            fg="green",
            font=("Segoe UI", 10, "bold")
        )
        self.tip_label.pack(side="left", padx=16)

        ttk.Button(top, text="Manage Months / ניהול חודשים", command=lambda: ManageMonthsDialog(self)).pack(side="left", padx=6)
        ttk.Button(top, text="Search / חיפוש", command=lambda: SearchDialog(self)).pack(side="left", padx=20)
        ttk.Button(top, text="📊 Yearly Summary / סיכום יעדים שנתי", command=lambda: YearlySummaryDialog(self)).pack(side="left", padx=8)
        ttk.Button(top, text="Delete Sale / מחק מכירה", command=self.delete_selected_sale).pack(side="left", padx=8)
        ttk.Button(
            top,
            text="Reset sort / איפוס מיון",
            command=lambda: treeview_reset_to_default(self.month_tab_trees[self.current_month()])
        ).pack(side="left", padx=8)

        self.sales_months = ttk.Notebook(self.sales_tab)
        self.sales_months.pack(fill="both", expand=True, padx=6, pady=6)

        cur_year = datetime.now().year
        months = [m for m in list_months_from_db_sorted()
                  if extract_year_from_month_name(m) == cur_year]
        for m in months:
            self.ensure_month_tab(m)
        self.reorder_month_tabs()
        self.refresh_all_months()

        # Auto-open current month tab
        cur = self._current_month_label()
        self.ensure_month_tab(cur)
        self.reorder_month_tabs()
        self.select_month_tab(cur)

    def _add_row_if_current_tab(self):
        try:
            current_tab = self.nb.select()
            if current_tab == str(self.sales_tab):
                self.add_sale_row()
            elif current_tab == str(self.goals_tab):
                self.add_goal_row()
        except Exception:
            pass

    def build_goals(self):
        top = ttk.Frame(self.goals_tab, padding=6)
        top.pack(fill="x")

        add_row_frame = ttk.Frame(self.goals_tab)
        add_row_frame.pack(fill="x", pady=(6, 2))
        ttk.Button(add_row_frame, text="➕ Add Goal Row / שורת יעד חדשה", command=self.add_goal_row).pack(side="left", padx=6)
        add_btn = add_row_frame.winfo_children()[0]
        add_btn.configure(style="BigAdd.TButton")

        tk.Label(
            add_row_frame,
            textvariable=self.tip_var,
            fg="green",
            font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=16)

        ttk.Button(top, text="Manage Months / ניהול חודשים", command=lambda: ManageGoalsMonthsDialog(self)).pack(side="left", padx=6)
        ttk.Button(top, text="Search / חיפוש", command=lambda: SearchDialog(self)).pack(side="left", padx=20)
        ttk.Button(top, text="📊 Yearly Summary / סיכום יעדים שנתי", command=lambda: YearlySummaryDialog(self)).pack(side="left", padx=8)
        ttk.Button(top, text="Delete Goal / מחק שורה", command=self.delete_selected_goal).pack(side="left", padx=8)
        ttk.Button(top, text="Reset sort / איפוס מיון", command=lambda: treeview_reset_to_default(self.goals_month_tab_trees[self.current_goal_month()])).pack(side="left", padx=8)

        self.goals_months = ttk.Notebook(self.goals_tab)
        self.goals_months.pack(fill="both", expand=True, padx=6, pady=6)

        cur_year = datetime.now().year
        months = [m for m in list_months_from_db_sorted()
                  if extract_year_from_month_name(m) == cur_year]
        for m in months:
            self.ensure_goal_month_tab(m)
        self.reorder_goal_month_tabs()
        self.refresh_all_goal_months()

        cur = self._current_month_label()
        self.ensure_goal_month_tab(cur)
        self.reorder_goal_month_tabs()
        self.select_goal_month_tab(cur)

    def goals_months_in_ui(self):
        return list(self.goals_month_tab_frames.keys())

    def ensure_goal_month_tab(self, month_name):
        month_name = month_name.strip()
        if not month_name:
            return
        if month_name in self.goals_month_tab_frames:
            return
        frame = ttk.Frame(self.goals_months)
        self.goals_month_tab_frames[month_name] = frame
        self.goals_months.add(frame, text=month_name)
        self.reorder_goal_month_tabs()
        self._build_goals_tree(frame, month_name)

    def select_goal_month_tab(self, month_name):
        if month_name in self.goals_month_tab_frames:
            self.goals_months.select(self.goals_month_tab_frames[month_name])

    def rename_goal_month_tab(self, old, new):
        if old not in self.goals_month_tab_frames:
            return
        frame = self.goals_month_tab_frames.pop(old)
        self.goals_month_tab_frames[new] = frame
        self.goals_months.tab(frame, text=new)
        self.reorder_goal_month_tabs()
        self.goals_month_tab_summaries[new] = self.goals_month_tab_summaries.pop(old)
        self.goals_month_tab_trees[new] = self.goals_month_tab_trees.pop(old)
        self.goals_month_tab_editors[new] = self.goals_month_tab_editors.pop(old)
        self.refresh_goal_month(new)

    def current_goal_month(self):
        tab_id = self.goals_months.select()
        return self.goals_months.tab(tab_id, "text") if tab_id else None

    def reorder_goal_month_tabs(self):
        if not self.goals_months:
            return
        current = list(self.goals_month_tab_frames.keys())
        if not current:
            return
        def key_fn(m):
            k = month_to_key(m)
            return k if k else (9999, 99)
        ordered = sorted(current, key=key_fn)
        for m in ordered:
            frame = self.goals_month_tab_frames[m]
            try:
                self.goals_months.forget(frame)
            except Exception:
                pass
        for m in ordered:
            frame = self.goals_month_tab_frames[m]
            self.goals_months.add(frame, text=m)

    def _build_goals_tree(self, parent, month):
        summary = ttk.Label(parent, text="", padding=(6, 4))
        summary.pack(anchor="w")

        columns = ("date", "voucher", "item_description", "sale_price", "lead_from", "seller")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=18)
        headers = [
            ("date", "Date / תאריך"),
            ("voucher", "Voucher / ואוצ'ר"),
            ("item_description", "Item Description / תיאור מוצר"),
            ("sale_price", "Sale Price / סכום מכירה"),
            ("lead_from", "Lead from / מקור הגעה"),
            ("seller", "Seller / מוכר"),
        ]
        widths = {"date": 120, "voucher": 120, "item_description": 260, "sale_price": 140, "lead_from": 180, "seller": 140}
        for c,t in headers:
            tree.heading(c, text=t, command=lambda cc=c: treeview_sort_column(tree, cc, False))
            tree.column(c, width=widths.get(c, 160))

        tree.pack(side="left", fill="both", expand=True, padx=(6,0), pady=6)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscroll=vsb.set)
        vsb.pack(side="right", fill="y", padx=(0,6), pady=6)

        self.goals_month_tab_summaries[month] = summary
        self.goals_month_tab_trees[month] = tree

        col_order = ["date", "voucher", "item_description", "sale_price", "lead_from", "seller"]
        col_specs = {
            "date": {"type": "entry"},
            "voucher": {"type": "entry"},
            "item_description": {"type": "combo", "restrict": False, "values_fn": lambda _row: ["מולטי", "מרחק", "קריאה", "רשיון", "אופיס", "מסגרת והעברת עדשות", "יתרה", "בדיקה", "על חשבון", "משקפי שמש", "עדשות מגע"]},
            "sale_price": {"type": "entry"},
            "lead_from": {"type": "combo", "restrict": False, "values_fn": lambda _row: ["חוזר", "מזדמן", "אינטרנט", "תיקון", "המלצה", "וואטסאפ"]},
            "seller": {"type":"combo", "restrict": False, "values_fn": lambda _row: list_sellers()},
        }
        editor = InlineCellEditor(self, tree, col_order, col_specs, single_click=True)
        self.goals_month_tab_editors[month] = editor

        def _remember_goal_row_on_click(event):
            row = tree.identify_row(event.y)
            if row:
                self._last_goal_row_iid = row
                tree.selection_set(row)
                tree.focus(row)

        tree.bind("<Button-1>", _remember_goal_row_on_click, add="+")

    def refresh_all_goal_months(self):
        cur_year = datetime.now().year
        for m in list_months_from_db_sorted():
            if extract_year_from_month_name(m) == cur_year:
                self.ensure_goal_month_tab(m)
        for m in list_months_from_goals_db():
            if extract_year_from_month_name(m) == cur_year:
                self.ensure_goal_month_tab(m)
        for m in list(self.goals_month_tab_frames.keys()):
            self.refresh_goal_month(m)

    def refresh_goal_month(self, month):
        tree = self.goals_month_tab_trees.get(month)
        summary = self.goals_month_tab_summaries.get(month)
        if not tree or not summary:
            return
        tree.delete(*tree.get_children())
        total_amount, count_rows = 0.0, 0
        with connect() as c:
            rows = c.execute(
                "SELECT id, date, voucher, item_description, sale_price, lead_from, seller FROM goals WHERE month=? ORDER BY id DESC",
                (month,)
            ).fetchall()
        for r in rows:
            goal_id = str(r[0])
            date, voucher, item_description, sale_price, lead_from, seller = r[1], r[2], r[3], r[4], r[5], r[6]
            tree.insert("", "end", iid=goal_id, values=(date, voucher, item_description, sale_price, lead_from, seller))
            count_rows += 1
            total_amount += float(sale_price or 0)
        summary.config(text=f"Total rows: {count_rows} | Total amount: ₪{total_amount:,.2f}")
        treeview_capture_default_order(tree)

    def add_goal_row(self):
        month = self.current_goal_month()
        if not month:
            messagebox.showwarning("No month", "Create/select a month tab first.")
            return

        tree = self.goals_month_tab_trees[month]
        editor = self.goals_month_tab_editors[month]
        self._new_goal_row_seq += 1
        iid = f"NEW_GOAL_{month}_{self._new_goal_row_seq}"

        today_str = datetime.now().strftime("%d/%m/%y")

        tree.insert("", 0, iid=iid, values=(today_str, "", "", "0", "", ""))
        tree.selection_set(iid)
        tree.focus(iid)

        # Start editing at Voucher, not Date
        self.after(50, lambda: editor.start_edit(iid, "voucher"))

    def delete_selected_goal(self):
        month = self.current_goal_month()
        if not month:
            return

        tree = self.goals_month_tab_trees.get(month)
        if not tree:
            return

        sel = tree.selection()
        goal_iid = sel[0] if sel else tree.focus()

        if not goal_iid:
            goal_iid = self._last_goal_row_iid


        if not goal_iid:
            messagebox.showinfo("Select", "Please select a goal row first.")
            return

        goal_id = int(goal_iid)

        if not messagebox.askyesno("Confirm", "Delete this row?"):
            return

        with connect() as c:
            c.execute("DELETE FROM goals WHERE id=?", (goal_id,))

        self.refresh_goal_month(month)

    def months_in_ui(self):
        return list(self.month_tab_frames.keys())

    def ensure_month_tab(self, month_name):
        month_name = month_name.strip()
        if not month_name:
            return
        if month_name in self.month_tab_frames:
            return
        frame = ttk.Frame(self.sales_months)
        self.month_tab_frames[month_name] = frame
        self.sales_months.add(frame, text=month_name)
        self.reorder_month_tabs()
        self._build_sales_tree(frame, month_name)


    def select_month_tab(self, month_name):
        if month_name in self.month_tab_frames:
            self.sales_months.select(self.month_tab_frames[month_name])

    def rename_month_tab(self, old, new):
        if old not in self.month_tab_frames:
            return
        frame = self.month_tab_frames.pop(old)
        self.month_tab_frames[new] = frame
        self.sales_months.tab(frame, text=new)
        self.reorder_month_tabs()
        self.month_tab_summaries[new] = self.month_tab_summaries.pop(old)
        self.month_tab_trees[new] = self.month_tab_trees.pop(old)
        self.month_tab_editors[new] = self.month_tab_editors.pop(old)
        self.refresh_month(new)

    def delete_month_tab(self, month_name):
        """Delete all sales for a month and remove its tab from the UI."""
        with connect() as c:
            c.execute("DELETE FROM sales WHERE month=?", (month_name,))
        if month_name not in self.month_tab_frames:
            return
        frame = self.month_tab_frames.pop(month_name)
        self.month_tab_summaries.pop(month_name, None)
        self.month_tab_trees.pop(month_name, None)
        self.month_tab_editors.pop(month_name, None)
        try:
            self.sales_months.forget(frame)
        except Exception:
            pass
        frame.destroy()

    def delete_goal_month_tab(self, month_name):
        """Delete all goals for a month and remove its tab from the UI."""
        with connect() as c:
            c.execute("DELETE FROM goals WHERE month=?", (month_name,))
        if month_name not in self.goals_month_tab_frames:
            return
        frame = self.goals_month_tab_frames.pop(month_name)
        self.goals_month_tab_summaries.pop(month_name, None)
        self.goals_month_tab_trees.pop(month_name, None)
        self.goals_month_tab_editors.pop(month_name, None)
        try:
            self.goals_months.forget(frame)
        except Exception:
            pass
        frame.destroy()

    def current_month(self):
        tab_id = self.sales_months.select()
        return self.sales_months.tab(tab_id, "text") if tab_id else None

    def _current_month_label(self) -> str:
        # Uses your existing Hebrew month list (_HE_MONTHS)
        # Example output: "פברואר 2026"
        d = datetime.now()
        return f"{_HE_MONTHS[d.month - 1]} {d.year}"


    def reorder_month_tabs(self):
        """Reorder the Sales month Notebook tabs chronologically."""
        if not self.sales_months:
            return
    
        # Current months that exist in UI
        current = list(self.month_tab_frames.keys())
        if not current:
            return
    
        # Sort them using month_to_key; unknown formats go last
        def key_fn(m):
            k = month_to_key(m)
            return k if k else (9999, 99)
    
        ordered = sorted(current, key=key_fn)
    
        # Re-add tabs in order (Notebook has no direct 'move' in older Tk versions)
        # We remove and add, but reuse same frames.
        for m in ordered:
            frame = self.month_tab_frames[m]
            try:
                self.sales_months.forget(frame)
            except Exception:
                pass
    
        for m in ordered:
            frame = self.month_tab_frames[m]
            self.sales_months.add(frame, text=m)


    def _build_sales_tree(self, parent, month):
        summary = ttk.Label(parent, text="", padding=(6, 4))
        summary.pack(anchor="w")

        columns = ("brand","model","sku","color","kind","price","seller","notes")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=18)

        headers = [
            ("brand","Brand (מותג)"),
            ("model","Model (דגם)"),
            ("sku","SKU (מק״ט)"),
            ("color","Color (צבע)"),
            ("kind","Type (ראיה\שמש)"),
            ("price","Sale price (מחיר מכירה)"),
            ("seller","Seller (מוכר)"),
            ("notes","Notes (הערות)"),
        ]
        widths = {"brand":150,"model":150,"color":120,"sku":100,"kind":120,"price":120,"seller":100,"notes":150}
        for c,t in headers:
            tree.heading(c, text=t, command=lambda cc=c: treeview_sort_column(tree, cc, False))
            tree.column(c, width=widths.get(c, 160))

        tree.pack(side="left", fill="both", expand=True, padx=(6,0), pady=6)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscroll=vsb.set)
        vsb.pack(side="right", fill="y", padx=(0,6), pady=6)

        self.month_tab_summaries[month] = summary
        self.month_tab_trees[month] = tree

        col_order = ["brand","model","sku","color","kind","price","seller","notes"]
        col_specs = {
            "brand": {"type":"combo", "restrict": False, "values_fn": lambda _row: list_brands()},
            "model": {"type":"combo", "restrict": False, "values_fn": lambda row: self._models_for_row(tree, row)},
            "color": {"type":"combo", "restrict": False, "values_fn": lambda _row: ["זהב","כסף","שחור","חום","מנומר", "אפור", "אדום", "שקוף", "כחול"]},
            "sku":   {"type":"combo", "restrict": False, "values_fn": lambda row: self._skus_for_row(tree, row)},
            "kind":  {"type":"combo", "restrict": False, "values_fn": lambda _row: ["Vision (ראיה)", "Sunglasses (שמש)"], "keep_if_blank": True},
            "price": {"type":"entry"},
            "seller": {"type":"combo", "restrict": False, "values_fn": lambda _row: list_sellers()},
            "notes": {"type":"combo", "restrict": False, "values_fn": lambda _row: ["Plastic","Metallic","Semi-Rimless","Rimless"]},
        }
        editor = InlineCellEditor(self, tree, col_order, col_specs, single_click=True)
        self.month_tab_editors[month] = editor
        self.refresh_month(month)

    def _models_for_row(self, tree, row_iid):
        vals = tree.item(row_iid, "values")
        brand = (vals[0] or "").strip()
        return list_models_for_brand(brand)

    def _skus_for_row(self, tree, row_iid):
        vals = tree.item(row_iid, "values")
        brand = (vals[0] or "").strip()
        model = (vals[1] or "").strip()
        # v7.0.3: If model exists for brand in inventory -> filter SKUs by model.
        # Otherwise (new/unknown model) -> show all SKUs for the brand.
        if brand and model:
            with connect() as c:
                exists = c.execute(
                    "SELECT 1 FROM items WHERE brand=? AND model=? LIMIT 1",
                    (brand, model),
                ).fetchone()
            if exists:
                return list_skus_for_brand_model(brand, model)
        return list_skus_for_brand_only(brand)

    def refresh_all_months(self):
        cur_year = datetime.now().year
        for m in list_months_from_db():
            if extract_year_from_month_name(m) == cur_year:
                self.ensure_month_tab(m)
        for m in list(self.month_tab_frames.keys()):
            self.refresh_month(m)

    def refresh_month(self, month):
        tree = self.month_tab_trees.get(month)
        summary = self.month_tab_summaries.get(month)
        if not tree or not summary:
            return
        tree.delete(*tree.get_children())
        total_q, total_s = 0, 0.0
        with connect() as c:
            rows = c.execute(
                "SELECT s.id, i.brand, i.model, s.color, i.sku, COALESCE(s.kind,'vision'), s.qty, s.price, s.seller, s.notes "
                "FROM sales s JOIN items i ON i.id=s.item_id WHERE s.month=? ORDER BY s.id DESC",
                (month,)
            ).fetchall()
        for r in rows:
            sale_id = str(r[0])
            brand, model, color, sku, kind, qty, price, seller, notes = r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]
            tree.insert("", "end", iid=sale_id, values=(brand, model, sku, color, kind, price, seller, notes))
            total_q += int(qty or 0)
            total_s += float(qty or 0) * float(price or 0)
        summary.config(text=f"סה״כ פריטים: {total_q} | סה״כ מכירות: ₪{total_s:,.2f}")
        treeview_capture_default_order(tree)

    def add_sale_row(self):
        month = self.current_month()
        if not month:
            messagebox.showwarning("No month", "Create/select a month tab first.")
            return 
        tree = self.month_tab_trees[month]
        editor = self.month_tab_editors[month]
        self._new_row_seq += 1
        iid = f"NEW_{month}_{self._new_row_seq}"
        tree.insert("", 0, iid=iid, values=("", "", "", "", "Vision (ראיה)", "0", "", ""))
        tree.selection_set(iid)
        tree.focus(iid)
        self.after(50, lambda: editor.start_edit(iid, "brand"))

    def on_cell_edited(self, tree, row_iid, field, new_val):
        month = None
        for m, t in self.month_tab_trees.items():
            if t == tree:
                month = m
                break

        if month:
            if field == "brand":
                vals = list(tree.item(row_iid, "values"))
                vals[1] = ""
                vals[2] = ""
                tree.item(row_iid, values=vals)
            elif field == "model":
                vals = list(tree.item(row_iid, "values"))
                vals[2] = ""
                tree.item(row_iid, values=vals)

            if str(row_iid).startswith("NEW_"):
                new_sale_id = self._maybe_commit_new_row(month, tree, row_iid)
                if field == "sku" and new_sale_id:
                    editor = self.month_tab_editors.get(month)
                    if editor:
                        self.after(50, lambda sid=str(new_sale_id): editor.start_edit(sid, "color"))
            else:
                self._update_existing_sale(month, int(row_iid), field)
            return

        goal_month = None
        for m, t in self.goals_month_tab_trees.items():
            if t == tree:
                goal_month = m
                break
        if not goal_month:
            return

        self._last_goal_row_iid = str(row_iid)

        if field == "date":
            normalized = normalize_goal_date_input(new_val, goal_month)
            if not normalized:
                if str(row_iid).startswith("NEW_GOAL_"):
                    tree.set(row_iid, "date", "")
                else:
                    with connect() as c:
                        old_row = c.execute("SELECT date FROM goals WHERE id=?", (int(row_iid),)).fetchone()
                    tree.set(row_iid, "date", old_row[0] if old_row else "")
                return
            tree.set(row_iid, "date", normalized)

        if str(row_iid).startswith("NEW_GOAL_"):
            new_goal_id = self._maybe_commit_new_goal_row(goal_month, tree, row_iid)

            editor = self.goals_month_tab_editors.get(goal_month)
            if new_goal_id and editor:
                if field == "date":
                    self.after(50, lambda gid=str(new_goal_id): editor.start_edit(gid, "voucher"))
                elif field == "voucher":
                    self.after(50, lambda gid=str(new_goal_id): editor.start_edit(gid, "item_description"))
        else:
            self._update_existing_goal(goal_month, int(row_iid), field)

    def _reclass_key(self, row_iid, brand, sku, model):
        return (str(row_iid), (brand or "").strip(), (sku or "").strip(), (model or "").strip())

    def delete_selected_sale(self):
        month = self.current_month()
        if not month:
            return 
        tree = self.month_tab_trees.get(month)
        if not tree:
            return 
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Please select a sale first.")
            return 
        sale_id = int(sel[0])

        ok = messagebox.askyesno("Confirm", "Delete this sale?\nThis will restore inventory quantity.")
        if not ok:
            return 

        with connect() as c:
            row = c.execute("SELECT item_id, qty FROM sales WHERE id=?", (sale_id,)).fetchone()
            if not row:
                return 
            item_id, qty = int(row[0]), int(row[1])
            c.execute("DELETE FROM sales WHERE id=?", (sale_id,))
            c.execute(
                "INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)",
                (item_id, +qty, "sale_delete_revert", now_iso()),
            )

        self.refresh_month(month)
        self.refresh_inventory()

    def _maybe_commit_new_row(self, month, tree, row_iid):
        vals = tree.item(row_iid, "values")
        brand = (vals[0] or "").strip()
        model = (vals[1] or "").strip()
        if model == "":
            model = "."
        sku = (vals[2] or "").strip()
        color = (vals[3] or "").strip()
        kind = (vals[4] or "Vision (ראיה)").strip() or "Sunglasses (שמש)"
        price_s = (vals[5] or "").strip()
        seller = (vals[6] or "").strip()
        notes = (vals[7] or "").strip()

        if not brand or not sku:
            return None

        from tkinter import messagebox

        with connect() as c:
            debit_item_id = None  # which item_id should receive the -qty movement (defaults to item_id)

            # 1) If exact (brand, sku, model) exists -> use it
            row = c.execute(
                "SELECT id FROM items WHERE brand=? AND sku=? AND model=?",
                (brand, sku, model),
            ).fetchone()
            if row:
                item_id = int(row[0])
                debit_item_id = item_id  # default: debit the same item
            
                # EXTENSION: if exact model has 0 on-hand, but placeholder for same brand+sku has stock -> debit placeholder instead
                if inventory_on_hand(c, item_id) <= 0:
                    rows = c.execute(
                        "SELECT id, model FROM items WHERE brand=? AND sku=? ORDER BY id",
                        (brand, sku),
                    ).fetchall()
            
                    placeholder_id = None
                    for iid, m0 in rows:
                        if is_placeholder_model(m0) and inventory_on_hand(c, int(iid)) > 0:
                            placeholder_id = int(iid)
                            break
            
                    if placeholder_id is not None:
                        debit_item_id = placeholder_id


            else:
                # 2) Find a placeholder item for (brand, sku) that has stock
                rows = c.execute(
                    "SELECT id, model FROM items WHERE brand=? AND sku=? ORDER BY id",
                    (brand, sku),
                ).fetchall()
                if not rows:
                    return None

                placeholder_id = None
                for iid, m0 in rows:
                    if is_placeholder_model(m0) and inventory_on_hand(c, int(iid)) > 0:
                        placeholder_id = int(iid)
                        break

                # If no placeholder stock exists, fallback: sell from any existing (brand, sku) item
                if placeholder_id is None:
                    item_id = int(rows[0][0])
                
                else:
                    # 3) If typed model itself is placeholder -> sell directly from placeholder
                    if is_placeholder_model(model):
                        item_id = placeholder_id
                
                    else:
                        # 4) Typed model is "real" but doesn't exist -> confirm & create new model
                        k = self._reclass_key(row_iid, brand, sku, model)

                        # If we already confirmed this exact reclass for this NEW row, don't ask again
                        if not self._reclass_confirmed.get(k, False):
                            row_key = str(row_iid)

                            # If we already confirmed for this NEW row, never ask again
                            if row_key in self._reclass_prompt_done:
                                pass
                            else:
                                # If a prompt is already open/being handled for this row, don't open another
                                if row_key in self._reclass_prompt_inflight:
                                    return None
                                self._reclass_prompt_inflight.add(row_key)

                            try:
                                if row_key not in self._reclass_prompt_done:
                                    msg = f"This will add {model} to {brand} in inventory.\nDo you wish to proceed?"
                                    if not messagebox.askokcancel("Confirm", msg, parent=self):
                                        return None
                                    self._reclass_prompt_done.add(row_key)
                            finally:
                                # Always release inflight lock (even if user cancels)
                                self._reclass_prompt_inflight.discard(row_key)

                
                        # Create (brand, sku, model) if missing
                        row2 = c.execute(
                            "SELECT id FROM items WHERE brand=? AND sku=? AND model=?",
                            (brand, sku, model),
                        ).fetchone()
                
                        if row2:
                            new_item_id = int(row2[0])
                        else:
                            cur = c.execute(
                                "INSERT INTO items(brand, sku, model) VALUES(?,?,?)",
                                (brand, sku, model),
                            )
                            new_item_id = int(cur.lastrowid)
                
                        # Move-1 from placeholder -> new model
                        c.execute(
                            "INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)",
                            (placeholder_id, -1, "reclass", now_iso()),
                        )
                        c.execute(
                            "INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)",
                            (new_item_id, +1, "reclass", now_iso()),
                        )
                
                        item_id = new_item_id


        try:
            price = float(price_s) if price_s else 0.0
        except Exception:
            return None

        qty = 1
        with connect() as c:
            # Decide which item actually loses stock (placeholder-aware)
            if debit_item_id is None:
                debit_item_id = item_id
        
            # HARD GUARD: do not allow inventory to go below 0
            onhand = inventory_on_hand(c, int(debit_item_id))
            if onhand - qty < 0:
                extra = ""
                try:
                    if int(debit_item_id) != int(item_id):
                        extra = "\n\n(Note: this sale would use placeholder stock for the same SKU.)"
                except Exception:
                    pass
        
                msg = (
                    "Cannot complete sale because it would make inventory negative.\n\n"
                    f"Item: {brand} | {model} | {sku}\n"
                    f"On hand: {onhand}\n\n"
                    "Please add this item to inventory first."
                    f"{extra}"
                )
                messagebox.showwarning("Not enough inventory", msg, parent=self)
                return None
        
            # Commit sale + movement
            cur = c.execute(
                "INSERT INTO sales(item_id,color,qty,price,seller,notes,month,created,kind) VALUES(?,?,?,?,?,?,?,?,?)",
                (item_id, color, qty, price, seller, notes, month, now_iso(), kind),
            )
            sale_id = int(cur.lastrowid)
        
            c.execute(
                "INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)",
                (debit_item_id, -qty, "sale", now_iso()),
            )

        
        # cleanup confirmation state for this NEW row
        for key in list(self._reclass_confirmed.keys()):
            if key and key[0] == str(row_iid):
                self._reclass_confirmed.pop(key, None)


        row_key = str(row_iid)
        self._reclass_prompt_done.discard(row_key)
        self._reclass_prompt_inflight.discard(row_key)

        tree.delete(row_iid)
        tree.insert("", 0, iid=str(sale_id), values=(brand, model, sku, color, kind, price, seller, notes))
        self.refresh_month(month)
        self.refresh_inventory()
        return sale_id

    def _update_existing_sale(self, month, sale_id: int, field: str):
        tree = self.month_tab_trees[month]
        ui_vals = tree.item(str(sale_id), "values")
        brand = (ui_vals[0] or "").strip()
        model = (ui_vals[1] or "").strip()
        sku = (ui_vals[2] or "").strip()
        color = (ui_vals[3] or "").strip()
        kind = (ui_vals[4] or "Vision (ראיה)").strip() or "Vision (ראיה)"
        price_s = (ui_vals[5] or "").strip()
        seller = (ui_vals[6] or "").strip()
        notes = (ui_vals[7] or "").strip()

        with connect() as c:
            row = c.execute("SELECT item_id, qty FROM sales WHERE id=?", (sale_id,)).fetchone()
            if not row:
                return
            old_item_id, old_qty = int(row[0]), int(row[1])

            if field in ("brand","model","sku"):
                exists = c.execute("SELECT id FROM items WHERE brand=? AND sku=? AND model=?", (brand, sku, model)).fetchone()
                if not exists:
                    # Silent validation: wait until Brand+Model+SKU form a valid inventory item
                    return
                new_item_id = int(exists[0])
                if new_item_id != old_item_id:
                    c.execute("UPDATE sales SET item_id=? WHERE id=?", (new_item_id, sale_id))
                    c.execute("INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)", (old_item_id, +old_qty, "sale_edit_revert", now_iso()))
                    c.execute("INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)", (new_item_id, -old_qty, "sale_edit_apply", now_iso()))
            elif field == "price":
                try:
                    price = float(price_s) if price_s else 0.0
                except Exception:
                    return
                c.execute("UPDATE sales SET price=? WHERE id=?", (price, sale_id))
            elif field == "kind":
                # Accept UI strings and legacy values
                if kind in ("vision", "Vision (ראיה)"):
                    db_kind = "Vision (ראיה)"
                elif kind in ("sunglasses", "Sunglasses (שמש)"):
                    db_kind = "Sunglasses (שמש)"
                else:
                    return
                c.execute("UPDATE sales SET kind=? WHERE id=?", (db_kind, sale_id))
            elif field == "color":
                c.execute("UPDATE sales SET color=? WHERE id=?", (color, sale_id))
            elif field == "seller":
                c.execute("UPDATE sales SET seller=? WHERE id=?", (seller, sale_id))
            elif field == "notes":
                c.execute("UPDATE sales SET notes=? WHERE id=?", (notes, sale_id))

        self.refresh_month(month)
        self.refresh_inventory()

    def _maybe_commit_new_goal_row(self, month, tree, row_iid):
        vals = tree.item(row_iid, "values")
        date = normalize_goal_date_input((vals[0] or "").strip(), month)
        voucher = (vals[1] or "").strip()
        item_description = (vals[2] or "").strip()
        sale_price_s = (vals[3] or "").strip()
        lead_from = (vals[4] or "").strip()
        seller = (vals[5] or "").strip()

        if not date:
            return None
        try:
            sale_price = float(sale_price_s) if sale_price_s else 0.0
        except Exception:
            return None

        with connect() as c:
            cur = c.execute(
                "INSERT INTO goals(date, voucher, item_description, sale_price, lead_from, seller, month, created) VALUES(?,?,?,?,?,?,?,?)",
                (date, voucher, item_description, sale_price, lead_from, seller, month, now_iso()),
            )
            goal_id = int(cur.lastrowid)

        self.refresh_goal_month(month)
        return goal_id

    def _update_existing_goal(self, month, goal_id: int, field: str):
        tree = self.goals_month_tab_trees[month]
        ui_vals = tree.item(str(goal_id), "values")
        date = normalize_goal_date_input((ui_vals[0] or "").strip(), month)
        voucher = (ui_vals[1] or "").strip()
        item_description = (ui_vals[2] or "").strip()
        sale_price_s = (ui_vals[3] or "").strip()
        lead_from = (ui_vals[4] or "").strip()
        seller = (ui_vals[5] or "").strip()

        with connect() as c:
            if field == "sale_price":
                try:
                    sale_price = float(sale_price_s) if sale_price_s else 0.0
                except Exception:
                    return
                c.execute("UPDATE goals SET sale_price=? WHERE id=?", (sale_price, goal_id))
            elif field == "date":
                if not date:
                    return
                c.execute("UPDATE goals SET date=? WHERE id=?", (date, goal_id))
            elif field == "voucher":
                c.execute("UPDATE goals SET voucher=? WHERE id=?", (voucher, goal_id))
            elif field == "item_description":
                c.execute("UPDATE goals SET item_description=? WHERE id=?", (item_description, goal_id))
            elif field == "lead_from":
                c.execute("UPDATE goals SET lead_from=? WHERE id=?", (lead_from, goal_id))
            elif field == "seller":
                c.execute("UPDATE goals SET seller=? WHERE id=?", (seller, goal_id))

        self.refresh_goal_month(month)

    # ----- Inventory -----

    # ------------------------------------------------------------------
    # Inventory per-brand selections: {brand -> {"model": str, "sku": str}}
    # Values are actual model/sku strings, or "" meaning "All".
    # ------------------------------------------------------------------
    def _inv_sel(self):
        """Return the _inv_selections dict, creating it if needed."""
        if not hasattr(self, "_inv_selections"):
            self._inv_selections = {}
        return self._inv_selections

    def _inv_brand_iid(self, brand):
        return f"brand::{brand}"

    def _inv_item_id_for_brand(self, brand, show_msg=True):
        """
        Return the single item_id that matches the current brand+model+sku
        selection, or None.  Used by Edit / Adjust / Delete.
        """
        sel = self._inv_sel().get(brand, {})
        model_sel = sel.get("model", "")
        sku_sel   = sel.get("sku",   "")
        if not model_sel or not sku_sel:
            if show_msg:
                messagebox.showinfo(
                    "Select item",
                    "Please select both a Model and a SKU from the dropdowns first."
                )
            return None
        with connect() as c:
            # Use actual model value (placeholder-aware)
            row = c.execute(
                "SELECT id FROM items WHERE brand=? AND model=? AND sku=?",
                (brand, model_sel, sku_sel)
            ).fetchone()
        if not row:
            if show_msg:
                messagebox.showinfo("Not found", "Could not find that exact item.")
            return None
        return int(row[0])

    def _selected_inventory_item_id(self, show_msg=True):
        """
        Return item_id for the currently selected brand row + its
        chosen model/sku dropdowns, or None.
        """
        sel = self.inv_tree.selection()
        if not sel:
            if show_msg:
                messagebox.showinfo("Select", "Select a brand row first.")
            return None
        iid = sel[0]
        if not iid.startswith("brand::"):
            if show_msg:
                messagebox.showinfo("Select", "Select a brand row.")
            return None
        brand = iid[len("brand::"):]
        return self._inv_item_id_for_brand(brand, show_msg=show_msg)

    def build_inventory(self):
        top = ttk.Frame(self.inv_tab, padding=6)
        top.pack(fill="x")

        ttk.Button(top, text="Add Inventory / הוסף למלאי",    command=self.add_inventory).pack(side="left", padx=6)
        ttk.Button(top, text="Adjust Qty (+/-) / עדכון כמות", command=self.adjust_selected_qty).pack(side="left", padx=6)
        ttk.Button(top, text="Edit Item / ערוך פריט",         command=self.edit_selected_item).pack(side="left", padx=6)
        ttk.Button(top, text="Delete Item / מחק פריט",        command=self.delete_selected_item).pack(side="left", padx=6)
        ttk.Button(top, text="Search / חיפוש", command=lambda: SearchDialog(self)).pack(side="left", padx=18)
        ttk.Button(top, text="Reset sort / איפוס מיון",
                   command=lambda: treeview_reset_to_default(self.inv_tree)).pack(side="left", padx=8)

        self.inv_summary = ttk.Label(top, text="", padding=(10,2))
        self.inv_summary.pack(side="left", padx=16)

        # Treeview — one row per brand; model/sku columns show dropdown widgets
        self.inv_tree = ttk.Treeview(
            self.inv_tab,
            columns=("brand","model","sku","onhand"),
            show="headings", height=22
        )
        headers = [
            ("brand",  "Brand (מותג)"),
            ("model",  "Model (דגם) ▾"),
            ("sku",    "SKU (מק״ט) ▾"),
            ("onhand", "On Hand (במלאי)"),
        ]
        widths = {"brand": 240, "model": 200, "sku": 160, "onhand": 130}
        for c, t in headers:
            self.inv_tree.heading(c, text=t,
                command=lambda cc=c: treeview_sort_column(self.inv_tree, cc, False))
            self.inv_tree.column(c, width=widths.get(c, 180))
        self.inv_tree.column("onhand", anchor="center")

        vsb = ttk.Scrollbar(self.inv_tab, orient="vertical", command=self.inv_tree.yview)
        self.inv_tree.configure(yscroll=vsb.set)
        self.inv_tree.pack(side="left", fill="both", expand=True, padx=(6,0), pady=6)
        vsb.pack(side="right", fill="y", padx=(0,6), pady=6)

        # Two overlay Comboboxes — repositioned over the selected row's cells
        self._inv_model_cb = ttk.Combobox(self.inv_tree, state="readonly", width=18)
        self._inv_sku_cb   = ttk.Combobox(self.inv_tree, state="readonly", width=14)
        self._inv_cb_brand = None   # which brand the comboboxes are currently for

        def _place_combos(brand):
            """Position the two comboboxes over the model/sku cells of the brand row."""
            iid = self._inv_brand_iid(brand)
            if not self.inv_tree.exists(iid):
                return
            bbox_model = self.inv_tree.bbox(iid, "model")
            bbox_sku   = self.inv_tree.bbox(iid, "sku")
            if not bbox_model or not bbox_sku:
                # Row scrolled out of view — hide
                self._inv_model_cb.place_forget()
                self._inv_sku_cb.place_forget()
                return
            x1, y1, w1, h1 = bbox_model
            x2, y2, w2, h2 = bbox_sku
            self._inv_model_cb.place(x=x1, y=y1, width=w1, height=h1)
            self._inv_sku_cb.place(x=x2, y=y2, width=w2, height=h2)

        def _update_combos_for_brand(brand, preserve=True):
            """Reload combobox values for brand; optionally keep existing selection."""
            sel = self._inv_sel().setdefault(brand, {"model": "", "sku": ""})

            models = [m for m in list_models_for_brand(brand) if not is_placeholder_model(m)]
            # Add placeholder models as one combined "— (no model)" entry if any exist
            has_placeholder = any(
                is_placeholder_model(m)
                for m in list_models_for_brand(brand)
            )
            model_values = (["— (no model)"] if has_placeholder else []) + models
            model_cb_values = ["All"] + model_values
            self._inv_model_cb["values"] = model_cb_values

            # Restore or default model selection
            cur_model = sel.get("model", "") if preserve else ""
            if cur_model == "" :
                self._inv_model_cb.set("All")
            elif is_placeholder_model(cur_model):
                self._inv_model_cb.set("— (no model)")
            elif cur_model in model_cb_values:
                self._inv_model_cb.set(cur_model)
            else:
                self._inv_model_cb.set("All")
                sel["model"] = ""

            # Refresh SKU values based on current model
            _refresh_sku_cb(brand, preserve=preserve)

        def _refresh_sku_cb(brand, preserve=True):
            """Reload SKU combobox based on current model selection."""
            sel = self._inv_sel().setdefault(brand, {"model": "", "sku": ""})
            model_val = sel.get("model", "")
            skus = list_skus_for_brand_model(brand, model_val)
            sku_cb_values = ["All"] + skus
            self._inv_sku_cb["values"] = sku_cb_values

            cur_sku = sel.get("sku", "") if preserve else ""
            if cur_sku == "" or cur_sku not in sku_cb_values:
                self._inv_sku_cb.set("All")
                sel["sku"] = ""
            else:
                self._inv_sku_cb.set(cur_sku)

        def _on_model_selected(event):
            brand = self._inv_cb_brand
            if not brand:
                return
            sel = self._inv_sel().setdefault(brand, {"model": "", "sku": ""})
            chosen = self._inv_model_cb.get()
            if chosen == "All":
                sel["model"] = ""
            elif chosen == "— (no model)":
                # Find the actual placeholder model string in DB
                all_models = list_models_for_brand(brand)
                ph = next((m for m in all_models if is_placeholder_model(m)), ".")
                sel["model"] = ph
            else:
                sel["model"] = chosen
            sel["sku"] = ""  # reset sku when model changes
            _refresh_sku_cb(brand, preserve=False)
            _refresh_inv_row(brand)

        def _on_sku_selected(event):
            brand = self._inv_cb_brand
            if not brand:
                return
            sel = self._inv_sel().setdefault(brand, {"model": "", "sku": ""})
            chosen = self._inv_sku_cb.get()
            sel["sku"] = "" if chosen == "All" else chosen
            _refresh_inv_row(brand)

        def _refresh_inv_row(brand):
            """Recompute on-hand for a brand row given current selections."""
            iid = self._inv_brand_iid(brand)
            if not self.inv_tree.exists(iid):
                return
            sel = self._inv_sel().get(brand, {})
            model_sel = sel.get("model", "")
            sku_sel   = sel.get("sku",   "")
            with connect() as c:
                if model_sel and sku_sel:
                    row = c.execute(
                        "SELECT id FROM items WHERE brand=? AND model=? AND sku=?",
                        (brand, model_sel, sku_sel)
                    ).fetchone()
                    onhand = inventory_on_hand(c, int(row[0])) if row else 0
                elif model_sel:
                    rows = c.execute(
                        "SELECT id FROM items WHERE brand=? AND model=?",
                        (brand, model_sel)
                    ).fetchall()
                    onhand = sum(inventory_on_hand(c, int(r[0])) for r in rows)
                elif sku_sel:
                    rows = c.execute(
                        "SELECT id FROM items WHERE brand=? AND sku=?",
                        (brand, sku_sel)
                    ).fetchall()
                    onhand = sum(inventory_on_hand(c, int(r[0])) for r in rows)
                else:
                    rows = c.execute(
                        "SELECT id FROM items WHERE brand=?", (brand,)
                    ).fetchall()
                    onhand = sum(inventory_on_hand(c, int(r[0])) for r in rows)
            model_disp = self._inv_model_cb.get() if self._inv_cb_brand == brand else (
                sel.get("model") or "All"
            )
            sku_disp = self._inv_sku_cb.get() if self._inv_cb_brand == brand else (
                sel.get("sku") or "All"
            )
            self.inv_tree.set(iid, "onhand", onhand)

        def _on_tree_select(event):
            sel = self.inv_tree.selection()
            if not sel:
                self._inv_model_cb.place_forget()
                self._inv_sku_cb.place_forget()
                self._inv_cb_brand = None
                return
            iid = sel[0]
            if not iid.startswith("brand::"):
                self._inv_model_cb.place_forget()
                self._inv_sku_cb.place_forget()
                self._inv_cb_brand = None
                return
            brand = iid[len("brand::"):]
            self._inv_cb_brand = brand
            _update_combos_for_brand(brand, preserve=True)
            _place_combos(brand)

        def _on_tree_scroll(*args):
            # Reposition combos after scroll
            if self._inv_cb_brand:
                self.inv_tree.after_idle(lambda: _place_combos(self._inv_cb_brand))

        self._inv_model_cb.bind("<<ComboboxSelected>>", _on_model_selected)
        self._inv_sku_cb.bind("<<ComboboxSelected>>",   _on_sku_selected)
        self.inv_tree.bind("<<TreeviewSelect>>", _on_tree_select)
        vsb.configure(command=lambda *a: (self.inv_tree.yview(*a), _on_tree_scroll()))
        self.inv_tree.bind("<MouseWheel>",   lambda e: self.inv_tree.after_idle(lambda: _on_tree_scroll()))
        self.inv_tree.bind("<Button-4>",     lambda e: self.inv_tree.after_idle(lambda: _on_tree_scroll()))
        self.inv_tree.bind("<Button-5>",     lambda e: self.inv_tree.after_idle(lambda: _on_tree_scroll()))

        # Store closures for use in refresh_inventory
        self._inv_place_combos          = _place_combos
        self._inv_update_combos         = _update_combos_for_brand
        self._inv_refresh_row           = _refresh_inv_row

        self.refresh_inventory()

    def add_inventory(self):
        win = tk.Toplevel(self)
        win.title("Add Inventory / הוסף למלאי")
        win.geometry("520x270")
        win.resizable(False, False)

        def add_row(r, label, default=""):
            ttk.Label(win, text=label).grid(row=r, column=0, sticky="w", padx=10, pady=6)
            e = ttk.Entry(win, width=34)
            e.insert(0, default)
            e.grid(row=r, column=1, sticky="w", padx=10, pady=6)
            return e

        ttk.Label(win, text="Brand (מותג) *").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        brand_var = tk.StringVar()
        brand_container, brand_e = create_autocomplete_entry(win, brand_var, values_fn=lambda: list_brands(), width=34)
        brand_container.grid(row=0, column=1, sticky="w", padx=10, pady=6)
        model_e = add_row(1, "Model (דגם) *")
        sku_e = add_row(2, "SKU (מק״ט) *")
        qty_e = add_row(3, "Qty (כמות) *", "1")

        def save():
            try:
                brand, model, sku = brand_e.get().strip(), model_e.get().strip(), sku_e.get().strip()
                qty = int(qty_e.get().strip())
                # Allow empty model; treat it as placeholder "."
                if model == "":
                    model = "."
                
                if not brand or not sku:
                    messagebox.showerror("Missing", "Brand and SKU are required."); return
                if qty <= 0:
                    messagebox.showerror("Invalid", "Qty must be > 0"); return
            except Exception:
                messagebox.showerror("Invalid", "Qty must be an integer."); return

            with connect() as c:
                item_id = get_or_create_item(c, brand, sku, model)
                c.execute("INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)", (item_id, qty, "receive", now_iso()))
            win.destroy()
            self.refresh_inventory()
            self.refresh_all_months()

        btns = ttk.Frame(win)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", padx=10, pady=14)
        ttk.Button(btns, text="Save / שמור", command=save).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel / ביטול", command=win.destroy).pack(side="left", padx=6)

    def adjust_selected_qty(self):
        item_id = self._selected_inventory_item_id()
        if not item_id:
            messagebox.showinfo("Select", "Select an inventory line first.")
            return

        with connect() as c:
            row = c.execute("SELECT brand, model, sku FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return
            brand, model, sku = row
            onhand = inventory_on_hand(c, item_id)

        win = tk.Toplevel(self)
        win.title("Adjust Qty / עדכון כמות")
        win.geometry("520x240")
        win.resizable(False, False)

        ttk.Label(win, text=f"{brand} | {model} | {sku}").pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(win, text=f"Current on-hand: {onhand}").pack(anchor="w", padx=12, pady=(0, 12))

        frm = ttk.Frame(win)
        frm.pack(fill="x", padx=12)
        ttk.Label(frm, text="Delta (+/-) (שינוי כמות):").grid(row=0, column=0, sticky="w")
        delta_e = ttk.Entry(frm, width=12)
        delta_e.insert(0, "1")
        delta_e.grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Reason (סיבה):").grid(row=1, column=0, sticky="w", pady=(10,0))
        reason_e = ttk.Entry(frm, width=28)
        reason_e.insert(0, "manual_adjust")
        reason_e.grid(row=1, column=1, sticky="w", padx=8, pady=(10,0))

        def save():
            try:
                delta = int(delta_e.get().strip())
            except Exception:
                messagebox.showerror("Invalid", "Delta must be integer.")
                return
            reason = reason_e.get().strip() or "manual_adjust"
            if delta == 0:
                win.destroy()
                return
            with connect() as c:
                c.execute("INSERT INTO movements(item_id,qty_delta,reason,created) VALUES(?,?,?,?)", (item_id, delta, reason, now_iso()))
            win.destroy()
            self.refresh_inventory()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=14)
        ttk.Button(btns, text="Save / שמור", command=save).pack(side="left")
        ttk.Button(btns, text="Cancel / ביטול", command=win.destroy).pack(side="left", padx=8)

    def edit_selected_item(self):
        item_id = self._selected_inventory_item_id()
        if not item_id:
            messagebox.showinfo("Select", "Select an inventory line first.")
            return
        with connect() as c:
            row = c.execute("SELECT brand, model, sku FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return
            brand0, model0, sku0 = row

        win = tk.Toplevel(self)
        win.title("Edit Item / ערוך פריט")
        win.geometry("520x260")
        win.resizable(False, False)

        def add_row(r, label, default=""):
            ttk.Label(win, text=label).grid(row=r, column=0, sticky="w", padx=10, pady=6)
            e = ttk.Entry(win, width=34)
            e.insert(0, default)
            e.grid(row=r, column=1, sticky="w", padx=10, pady=6)
            return e

        brand_e = add_row(0, "Brand (מותג) *", brand0)
        model_e = add_row(1, "Model (דגם) *", model0)
        sku_e = add_row(2, "SKU (מק״ט) *", sku0)

        def save():
            brand = brand_e.get().strip()
            model = model_e.get().strip()
            sku = sku_e.get().strip()
            if not brand or not model or not sku:
                messagebox.showerror("Missing", "Brand/Model/SKU required.")
                return
            with connect() as c:
                # uniqueness check
                exists = c.execute(
                    "SELECT id FROM items WHERE brand=? AND sku=? AND model=? AND id<>?",
                    (brand, sku, model, item_id),
                ).fetchone()
                if exists:
                    messagebox.showerror("Duplicate", "Another item with same Brand/Model/SKU already exists.")
                    return
                c.execute("UPDATE items SET brand=?, model=?, sku=? WHERE id=?", (brand, model, sku, item_id))
            win.destroy()
            self.refresh_inventory()
            self.refresh_all_months()

        btns = ttk.Frame(win)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", padx=10, pady=14)
        ttk.Button(btns, text="Save / שמור", command=save).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel / ביטול", command=win.destroy).pack(side="left", padx=6)

    def delete_selected_item(self):
        item_id = self._selected_inventory_item_id()
        if not item_id:
            messagebox.showinfo("Select", "Select an inventory line first.")
            return

        with connect() as c:
            row = c.execute("SELECT brand, model, sku FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return
            brand, model, sku = row
            sale_count = c.execute("SELECT COUNT(*) FROM sales WHERE item_id=?", (item_id,)).fetchone()[0]

        msg = f"Delete this item?\n\n{brand} | {model} | {sku}\n\nThis will also delete {sale_count} related sale(s)."
        if not messagebox.askyesno("Confirm delete", msg):
            return

        with connect() as c:
            c.execute("DELETE FROM items WHERE id=?", (item_id,))

        self.refresh_inventory()
        self.refresh_all_months()

    def refresh_inventory(self):
        # Hide combos while rebuilding
        if hasattr(self, "_inv_model_cb"):
            self._inv_model_cb.place_forget()
            self._inv_sku_cb.place_forget()
        prev_brand = getattr(self, "_inv_cb_brand", None)

        self.inv_tree.delete(*self.inv_tree.get_children())
        total_cost, total_units = 0.0, 0

        # Collect all brands and their total on-hand
        brand_onhand = {}   # brand -> total onhand (all items)
        with connect() as c:
            rows = c.execute(
                "SELECT id, brand, sku, model FROM items ORDER BY brand, model, sku"
            ).fetchall()
            for item_id, brand, sku, model in rows:
                onhand = inventory_on_hand(c, int(item_id))
                total_units += onhand
                try:
                    sku_num = float(str(sku).strip())
                    total_cost += onhand * (sku_num / 2.0)
                except Exception:
                    pass
                brand_onhand[brand] = brand_onhand.get(brand, 0) + onhand

        for brand, total in brand_onhand.items():
            iid = self._inv_brand_iid(brand)
            # Apply saved model/sku filter to compute displayed on-hand
            sel = self._inv_sel().get(brand, {})
            model_sel = sel.get("model", "")
            sku_sel   = sel.get("sku",   "")
            with connect() as c:
                if model_sel and sku_sel:
                    row = c.execute(
                        "SELECT id FROM items WHERE brand=? AND model=? AND sku=?",
                        (brand, model_sel, sku_sel)
                    ).fetchone()
                    display_onhand = inventory_on_hand(c, int(row[0])) if row else 0
                elif model_sel:
                    rs = c.execute(
                        "SELECT id FROM items WHERE brand=? AND model=?",
                        (brand, model_sel)
                    ).fetchall()
                    display_onhand = sum(inventory_on_hand(c, int(r[0])) for r in rs)
                elif sku_sel:
                    rs = c.execute(
                        "SELECT id FROM items WHERE brand=? AND sku=?",
                        (brand, sku_sel)
                    ).fetchall()
                    display_onhand = sum(inventory_on_hand(c, int(r[0])) for r in rs)
                else:
                    display_onhand = total

            self.inv_tree.insert("", "end", iid=iid, values=(brand, "—", "—", display_onhand))

        self.inv_summary.config(
            text=f"Units (יחידות): {total_units} | Estimated store cost (עלות משוערת): ₪{total_cost:,.2f}"
        )
        treeview_capture_default_order(self.inv_tree)

        # Re-select and re-place combos for previously selected brand
        if prev_brand and self.inv_tree.exists(self._inv_brand_iid(prev_brand)):
            self.inv_tree.selection_set(self._inv_brand_iid(prev_brand))
            self._inv_update_combos(prev_brand, preserve=True)
            self.inv_tree.after_idle(lambda: self._inv_place_combos(prev_brand))

if __name__ == "__main__":
    App().mainloop()
