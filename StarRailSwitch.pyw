"""崩坏：星穹铁道 账号快照切换器

原理：游戏把登录界面的账号列表（最多 3 个）和登录凭据整体存放在
HKCU\\Software\\miHoYo\\崩坏：星穹铁道 下的 MIHOYOSDK_ADL_PROD_CN_h3123967166（REG_BINARY）。
本程序把这个值整体备份为“快照”，切换时在游戏未运行的情况下原样写回。
"""
import base64
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
import winreg
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_TITLE = "星穹铁道账号切换"
FROZEN = getattr(sys, "frozen", False)  # 是否为 PyInstaller 打包的 exe
# 数据放在 exe（或脚本）所在目录；单文件 exe 运行时 __file__ 位于会被删除的临时解压目录
BASE_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "profiles")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# 图标随 exe 一起打包，从解压目录读取
ICON_PATH = os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "logo.ico")
APP_ID = "StarRailSwitch.AccountSwitcher"

REG_KEY = r"Software\miHoYo\崩坏：星穹铁道"
REG_VALUES = ["MIHOYOSDK_ADL_PROD_CN_h3123967166"]
PROCESS_NAME = "StarRail.exe"
DEFAULT_GAME_PATH = r"E:\Star Rail\Game\StarRail.exe"
DEFAULT_LAUNCHER_ARGS = "--game=hkrpg_cn"  # 米哈游启动器直接打开星穹铁道页面
AUTO_BACKUP_NAME = "_切换前自动备份"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
KILL_TIMEOUT = 15  # 秒

# 配色
THEMES = {
    "light": {
        "label": "浅色",
        "BG": "#f4f5f9", "SURFACE": "#ffffff", "SURFACE_HOVER": "#eceef6", "BORDER": "#dcdfea",
        "TEXT": "#1f2333", "MUTED": "#7a7f95", "ACCENT": "#f0c36b", "ACCENT_DARK": "#e2ad4a",
        "ACCENT_TEXT": "#b07a1c", "DANGER": "#d6454f", "THUMB": "#c4c8d8", "THUMB_HOVER": "#9ea3b8",
    },
    "dark": {
        "label": "深色",
        "BG": "#1b1d29", "SURFACE": "#252838", "SURFACE_HOVER": "#30344a", "BORDER": "#363a52",
        "TEXT": "#ecedf5", "MUTED": "#9396ad", "ACCENT": "#f0c36b", "ACCENT_DARK": "#d9a94c",
        "ACCENT_TEXT": "#f0c36b", "DANGER": "#e8737a", "THUMB": "#454a66", "THUMB_HOVER": "#62688a",
    },
}
DEFAULT_THEME = "light"
BG = SURFACE = SURFACE_HOVER = BORDER = TEXT = MUTED = ACCENT = ACCENT_DARK = ACCENT_TEXT = DANGER = None
THUMB = THUMB_HOVER = None
FONT = "Microsoft YaHei UI"


def apply_theme(name):
    """切换全局配色；控件在创建时读取这些颜色，换主题后需重建界面。"""
    global BG, SURFACE, SURFACE_HOVER, BORDER, TEXT, MUTED, ACCENT, ACCENT_DARK, ACCENT_TEXT, DANGER
    global THUMB, THUMB_HOVER
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    BG, SURFACE, SURFACE_HOVER, BORDER = t["BG"], t["SURFACE"], t["SURFACE_HOVER"], t["BORDER"]
    TEXT, MUTED, ACCENT, ACCENT_DARK = t["TEXT"], t["MUTED"], t["ACCENT"], t["ACCENT_DARK"]
    ACCENT_TEXT, DANGER, THUMB, THUMB_HOVER = t["ACCENT_TEXT"], t["DANGER"], t["THUMB"], t["THUMB_HOVER"]


apply_theme(DEFAULT_THEME)


# ---------- 注册表 / 进程 ----------

def read_reg_values():
    """读取当前登录数据，返回 {值名: bytes}；缺失的值不包含在内。"""
    result = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
        for name in REG_VALUES:
            try:
                data, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            if kind != winreg.REG_BINARY:
                raise RuntimeError(f"{name} 的类型不是 REG_BINARY（{kind}），游戏可能改了存储格式")
            result[name] = bytes(data)
    return result


def write_reg_values(values):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for name, data in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def game_running():
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {PROCESS_NAME}", "/NH", "/FO", "CSV"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    return PROCESS_NAME.lower() in out.lower()


def kill_game():
    # 游戏以管理员权限运行，非管理员时需要提权才能结束
    if is_admin():
        subprocess.run(["taskkill", "/F", "/IM", PROCESS_NAME],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "taskkill.exe", f"/F /IM {PROCESS_NAME}", None, 0)
        if ret <= 32:
            raise RuntimeError("无法结束游戏进程（可能取消了 UAC 授权）")


def split_command(path, args):
    """路径框里粘贴了完整命令（"C:\\...\\launcher.exe" --game=xx）时，拆成程序路径和参数。"""
    path = path.strip()
    if path.startswith('"') and '"' in path[1:]:
        end = path.index('"', 1)
        path, extra = path[1:end], path[end + 1:].strip()
        args = (extra + " " + args).strip() if extra and extra not in args else args
    return path, args.strip()


def launch_launcher(path, args):
    ret = ctypes.windll.shell32.ShellExecuteW(None, "open", path, args or None, os.path.dirname(path), 1)
    if ret <= 32:
        raise RuntimeError(f"启动器打开失败（ShellExecute 返回 {ret}）")


def launch_game(path):
    # 游戏要求管理员权限，用 runas 触发 UAC（本程序已提权时不会弹窗）
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", path, None, os.path.dirname(path), 1)
    if ret <= 32:
        raise RuntimeError(f"启动失败（ShellExecute 返回 {ret}），可能取消了 UAC 授权")


def fingerprint(values):
    h = hashlib.sha256()
    for name in sorted(values):
        h.update(name.encode())
        h.update(values[name])
    return h.hexdigest()


# ---------- 快照存储 ----------

def profile_path(name):
    return os.path.join(PROFILE_DIR, name + ".json")


def now_str():
    return time.strftime(TIME_FMT)


def save_profile(name, values, saved_at=None, last_login=None):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    data = {
        "name": name,
        "saved_at": saved_at or now_str(),
        "last_login": last_login,
        "values": {k: base64.b64encode(v).decode() for k, v in values.items()},
    }
    with open(profile_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_profile(name):
    with open(profile_path(name), encoding="utf-8") as f:
        data = json.load(f)
    data["values"] = {k: base64.b64decode(v) for k, v in data["values"].items()}
    data.setdefault("last_login", None)
    return data


def touch_last_login(name):
    p = load_profile(name)
    save_profile(name, p["values"], p.get("saved_at"), now_str())


def list_profiles(include_backup=True):
    if not os.path.isdir(PROFILE_DIR):
        return []
    names = [f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith(".json")]
    if not include_backup:
        names = [n for n in names if n != AUTO_BACKUP_NAME]
    return sorted(names, key=lambda n: (n == AUTO_BACKUP_NAME, n))


def valid_name(name):
    return bool(name) and not re.search(r'[\\/:*?"<>|]', name) and name not in (".", "..")


def relative_time(ts):
    if not ts:
        return "从未启动"
    try:
        dt = datetime.strptime(ts, TIME_FMT)
    except ValueError:
        return ts
    now = datetime.now()
    secs = (now - dt).total_seconds()
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟前"
    if dt.date() == now.date():
        return f"{int(secs // 3600)} 小时前"
    if dt.date() == now.date() - timedelta(days=1):
        return f"昨天 {dt:%H:%M}"
    if dt.year == now.year:
        return f"{dt.month}月{dt.day}日"
    return f"{dt.year}年{dt.month}月{dt.day}日"


# ---------- 配置 ----------

def load_config():
    cfg = {"game_path": DEFAULT_GAME_PATH, "theme": DEFAULT_THEME,
           "launcher_path": "", "launcher_args": DEFAULT_LAUNCHER_ARGS}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- 界面 ----------

def setup_style(root):
    root.configure(bg=BG)
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, font=(FONT, 10), bordercolor=BORDER,
                focuscolor=BG, troughcolor=SURFACE)
    s.configure("TFrame", background=BG)
    s.configure("Surface.TFrame", background=SURFACE)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Section.TLabel", font=(FONT, 11, "bold"))
    s.configure("Title.TLabel", font=(FONT, 15, "bold"))
    s.configure("Status.TLabel", background=SURFACE, foreground=MUTED, padding=(12, 5))

    s.configure("TButton", background=SURFACE, foreground=TEXT, borderwidth=0, padding=(12, 6), focuscolor=SURFACE)
    s.map("TButton", background=[("pressed", BORDER), ("active", SURFACE_HOVER)],
          foreground=[("disabled", MUTED)])
    s.configure("Launcher.TButton", background=ACCENT, foreground="#1b1d29", font=(FONT, 11, "bold"),
                padding=(14, 6))
    s.map("Launcher.TButton", background=[("disabled", SURFACE), ("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
          foreground=[("disabled", MUTED)])
    s.configure("Accent.TButton", background=ACCENT, foreground="#1b1d29", font=(FONT, 10, "bold"))
    s.map("Accent.TButton", background=[("pressed", ACCENT_DARK), ("active", ACCENT_DARK)])
    s.configure("Danger.TButton", foreground=DANGER)
    s.configure("Header.TButton", background=BG, foreground=TEXT, font=(FONT, 11), padding=(10, 4))
    s.map("Header.TButton", background=[("active", SURFACE)])

    s.configure("TRadiobutton", background=BG, foreground=TEXT, indicatorbackground=SURFACE,
                indicatorforeground=ACCENT_TEXT, padding=(0, 2, 16, 2))
    s.map("TRadiobutton", background=[("active", BG)],
          indicatorbackground=[("selected", SURFACE), ("active", SURFACE_HOVER)])
    s.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT, insertcolor=TEXT, borderwidth=1, padding=6)
    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                rowheight=30, borderwidth=0)
    s.map("Treeview", background=[("selected", SURFACE_HOVER)], foreground=[("selected", ACCENT_TEXT)])
    s.configure("Treeview.Heading", background=BG, foreground=MUTED, borderwidth=0, padding=(6, 6))
    s.map("Treeview.Heading", background=[("active", BG)])
    s.configure("DangerFill.TButton", background=DANGER, foreground="#ffffff", font=(FONT, 10, "bold"))
    s.map("DangerFill.TButton", background=[("pressed", DANGER), ("active", DANGER)])
    s.configure("Dialog.TFrame", background=SURFACE)
    s.configure("Dialog.TLabel", background=SURFACE, foreground=TEXT, font=(FONT, 11))
    s.configure("DialogTitle.TLabel", background=SURFACE, foreground=TEXT, font=(FONT, 13, "bold"))

    # 细滚动条：去掉箭头，只保留滑轨和滑块
    s.layout("Thin.Vertical.TScrollbar", [
        ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    s.configure("Thin.Vertical.TScrollbar", troughcolor=BG, background=THUMB, bordercolor=BG,
                lightcolor=THUMB, darkcolor=THUMB, arrowsize=8, gripcount=0, borderwidth=0)
    hover = [("pressed", THUMB_HOVER), ("active", THUMB_HOVER)]
    s.map("Thin.Vertical.TScrollbar", background=hover, lightcolor=hover, darkcolor=hover)


def autohide_scrollbar(sb, **pack_kw):
    """返回给 yscrollcommand 用的回调：内容放得下时隐藏滚动条。"""
    def set_(first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            sb.pack_forget()
        elif not sb.winfo_ismapped():
            sb.pack(**pack_kw)
        sb.set(first, last)
    return set_


def confirm(parent, title, message, ok_text="确定"):
    """与当前主题一致的确认对话框，返回 True/False。"""
    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    dlg.title(title)
    dlg.configure(bg=BORDER)
    dlg.resizable(False, False)
    dlg.transient(parent)
    result = {"ok": False}

    body = ttk.Frame(dlg, style="Dialog.TFrame", padding=(24, 20, 24, 18))
    body.pack(fill="both", expand=True, padx=1, pady=1)
    ttk.Label(body, text=title, style="DialogTitle.TLabel").pack(anchor="w")
    ttk.Label(body, text=message, style="Dialog.TLabel", justify="left").pack(anchor="w", pady=(10, 20))
    row = ttk.Frame(body, style="Dialog.TFrame")
    row.pack(fill="x")

    def close(ok):
        result["ok"] = ok
        dlg.destroy()

    ttk.Button(row, text=ok_text, style="DangerFill.TButton", command=lambda: close(True)).pack(side="right")
    cancel = ttk.Button(row, text="取消", command=lambda: close(False))
    cancel.pack(side="right", padx=(0, 8))
    dlg.bind("<Escape>", lambda e: close(False))
    dlg.protocol("WM_DELETE_WINDOW", lambda: close(False))

    dlg.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - dlg.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dlg.winfo_reqheight()) // 3
    dlg.geometry(f"+{x}+{y}")
    dlg.deiconify()
    dlg.grab_set()
    cancel.focus_set()  # 默认焦点在“取消”，防止误按回车删除
    dlg.bind("<Return>", lambda e: close(dlg.focus_get() is not cancel))
    parent.wait_window(dlg)
    return result["ok"]


class AccountCard(tk.Frame):
    """启动页中的单个账号卡片"""

    def __init__(self, master, name, last_login, current, on_activate):
        super().__init__(master, bg=SURFACE, cursor="hand2", highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        self.name = name
        self.last_login = last_login
        self._widgets = [self]

        strip = tk.Frame(self, bg=ACCENT if current else SURFACE, width=5)
        strip.pack(side="left", fill="y")

        body = tk.Frame(self, bg=SURFACE, padx=18, pady=14)
        body.pack(side="left", fill="both", expand=True)
        self._widgets.append(body)

        title = tk.Label(body, text=name, bg=SURFACE, fg=TEXT, font=(FONT, 18, "bold"), anchor="w")
        title.pack(side="left")
        self._widgets.append(title)
        if current:
            tag = tk.Label(body, text="  当前", bg=SURFACE, fg=ACCENT_TEXT, font=(FONT, 10, "bold"))
            tag.pack(side="left", pady=(6, 0))
            self._widgets.append(tag)

        self.time_label = tk.Label(body, bg=SURFACE, fg=MUTED, font=(FONT, 13), anchor="e")
        self.time_label.pack(side="right")
        self._widgets.append(self.time_label)
        self.update_time()

        for w in self._widgets:
            w.bind("<Enter>", lambda e: self._set_bg(SURFACE_HOVER))
            w.bind("<Leave>", self._on_leave)
            w.bind("<Double-1>", lambda e: on_activate(self.name))

    def _on_leave(self, event):
        # 在子控件之间移动时不要闪烁
        x, y = self.winfo_pointerxy()
        if self.winfo_containing(x, y) not in self._widgets:
            self._set_bg(SURFACE)

    def _set_bg(self, color):
        for w in self._widgets:
            w.configure(bg=color)

    def update_time(self):
        self.time_label.configure(text=relative_time(self.last_login))


class LaunchPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.cards = []

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="Thin.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=autohide_scrollbar(sb, side="right", fill="y", padx=(0, 4)))

        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))

    def on_wheel(self, event):
        if self.winfo_ismapped() and self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def refresh(self, current_name):
        for c in self.cards:
            c.destroy()
        self.cards.clear()
        for w in self.inner.winfo_children():
            w.destroy()

        names = list_profiles(include_backup=False)
        if not names:
            tk.Label(self.inner, text="还没有保存任何账号\n点击右上角「配置」添加",
                     bg=BG, fg=MUTED, font=(FONT, 13), justify="center").pack(pady=60)
            return

        profiles = []
        for n in names:
            try:
                profiles.append(load_profile(n))
            except (OSError, ValueError, KeyError):
                continue
        # 最近登录的排前面
        profiles.sort(key=lambda p: p.get("last_login") or "", reverse=True)
        for p in profiles:
            card = AccountCard(self.inner, p["name"], p["last_login"], p["name"] == current_name, self.app.activate)
            card.pack(fill="x", padx=16, pady=6)
            self.cards.append(card)

        tk.Label(self.inner, text="双击账号：关闭游戏 → 切换账号 → 重新启动",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(pady=(10, 4))

    def update_times(self):
        for c in self.cards:
            c.update_time()


class ConfigPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=(16, 8, 16, 8))
        self.app = app

        ttk.Label(self, text="游戏路径", style="Section.TLabel").pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(4, 14))
        self.path_var = tk.StringVar(value=app.cfg["game_path"])
        entry = ttk.Entry(row, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<FocusOut>", lambda e: app.game_path())
        ttk.Button(row, text="浏览…", command=self.browse).pack(side="left", padx=(8, 0))

        ttk.Label(self, text="官方启动器（路径 / 启动参数，可留空）", style="Section.TLabel").pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(4, 14))
        self.launcher_var = tk.StringVar(value=app.cfg["launcher_path"])
        self.launcher_args_var = tk.StringVar(value=app.cfg["launcher_args"])
        entry = ttk.Entry(row, textvariable=self.launcher_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<FocusOut>", lambda e: app.save_launcher())
        args_entry = ttk.Entry(row, textvariable=self.launcher_args_var, width=16)
        args_entry.pack(side="left", padx=(8, 0))
        args_entry.bind("<FocusOut>", lambda e: app.save_launcher())
        ttk.Button(row, text="浏览…", command=self.browse_launcher).pack(side="left", padx=(8, 0))

        theme_row = ttk.Frame(self)
        theme_row.pack(fill="x", pady=(0, 14))
        ttk.Label(theme_row, text="主题", style="Section.TLabel").pack(side="left", padx=(0, 16))
        self.theme_var = tk.StringVar(value=app.cfg["theme"])
        for key, t in THEMES.items():
            ttk.Radiobutton(theme_row, text=t["label"], value=key, variable=self.theme_var,
                            command=lambda: app.set_theme(self.theme_var.get())).pack(side="left")

        ttk.Label(self, text="账号快照", style="Section.TLabel").pack(anchor="w")
        ttk.Label(self, text="保存方法：在游戏里登录要保存的账号 → 点「保存当前登录」",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 6))

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True)

        cols = ("name", "saved_at", "last_login")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for col, text, width, anchor in [("name", "名称", 130, "w"), ("saved_at", "保存时间", 165, "center"),
                                         ("last_login", "上次启动", 95, "center")]:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.tag_configure("current", foreground=ACCENT_TEXT)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview, style="Thin.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=autohide_scrollbar(sb, side="left", fill="y", after=self.tree))

        side = ttk.Frame(mid)
        side.pack(side="left", fill="y", padx=(12, 0))
        buttons = [
            ("保存当前登录", self.save_current, "Accent.TButton"),
            ("用当前登录更新", self.overwrite, "TButton"),
            ("重命名", self.rename, "TButton"),
            ("删除", self.delete, "Danger.TButton"),
            None,
            ("仅切换（不启动）", self.switch_only, "TButton"),
            ("打开快照目录", self.open_dir, "TButton"),
        ]
        for b in buttons:
            if b is None:
                ttk.Frame(side, height=14).pack()
            else:
                ttk.Button(side, text=b[0], command=b[1], style=b[2], width=16).pack(fill="x", pady=3)

    def refresh(self, current_name):
        self.tree.delete(*self.tree.get_children())
        for name in list_profiles():
            try:
                p = load_profile(name)
            except (OSError, ValueError, KeyError):
                self.tree.insert("", "end", iid=name, values=(name, "（文件损坏）", ""))
                continue
            tags = ("current",) if name == current_name else ()
            label = ("● " if tags else "") + name
            self.tree.insert("", "end", iid=name, tags=tags,
                             values=(label, p.get("saved_at", ""), relative_time(p["last_login"])))

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "请先在列表中选择一个快照", parent=self)
            return None
        return sel[0]

    def browse(self):
        path = filedialog.askopenfilename(
            title="选择 StarRail.exe", filetypes=[("StarRail.exe", "StarRail.exe"), ("可执行文件", "*.exe")],
            initialdir=os.path.dirname(self.path_var.get()) or None, parent=self,
        )
        if path:
            self.path_var.set(os.path.normpath(path))
            self.app.game_path()

    def browse_launcher(self):
        current = split_command(self.launcher_var.get(), "")[0]
        path = filedialog.askopenfilename(
            title="选择官方启动器 launcher.exe", filetypes=[("launcher.exe", "launcher.exe"), ("可执行文件", "*.exe")],
            initialdir=os.path.dirname(current) or r"C:\Program Files\miHoYo Launcher", parent=self,
        )
        if path:
            self.launcher_var.set(os.path.normpath(path))
            self.app.save_launcher()

    def open_dir(self):
        os.makedirs(PROFILE_DIR, exist_ok=True)
        os.startfile(PROFILE_DIR)

    def _read_current(self):
        try:
            values = read_reg_values()
        except (OSError, RuntimeError) as e:
            messagebox.showerror(APP_TITLE, f"读取注册表失败：{e}", parent=self)
            return None
        if not values:
            messagebox.showerror(APP_TITLE, "注册表中没有登录数据，请先在游戏里登录一次", parent=self)
            return None
        return values

    def save_current(self):
        values = self._read_current()
        if values is None:
            return
        name = simpledialog.askstring(APP_TITLE, "账号名称（例如昵称或 UID）：", parent=self)
        if name is None:
            return
        name = name.strip()
        if not valid_name(name) or name == AUTO_BACKUP_NAME:
            messagebox.showerror(APP_TITLE, "名称为空或包含非法字符 \\ / : * ? \" < > |", parent=self)
            return
        if os.path.exists(profile_path(name)) and not messagebox.askyesno(
                APP_TITLE, f"【{name}】已存在，覆盖？", parent=self):
            return
        save_profile(name, values, last_login=now_str())
        self.app.refresh()
        self.app.set_status(f"已保存账号【{name}】")

    def overwrite(self):
        name = self.selected()
        if not name:
            return
        if not messagebox.askyesno(APP_TITLE, f"用当前注册表中的登录数据覆盖【{name}】？\n（用于令牌过期后重新登录再保存）",
                                   parent=self):
            return
        values = self._read_current()
        if values is None:
            return
        try:
            last = load_profile(name)["last_login"]
        except (OSError, ValueError, KeyError):
            last = None
        save_profile(name, values, last_login=last or now_str())
        self.app.refresh()
        self.app.set_status(f"已更新【{name}】")

    def rename(self):
        name = self.selected()
        if not name:
            return
        try:
            p = load_profile(name)
        except (OSError, ValueError, KeyError) as e:
            messagebox.showerror(APP_TITLE, f"读取失败：{e}", parent=self)
            return
        new = simpledialog.askstring(APP_TITLE, "新名称：", initialvalue=name, parent=self)
        if new is None or new.strip() == name:
            return
        new = new.strip()
        if not valid_name(new) or new == AUTO_BACKUP_NAME:
            messagebox.showerror(APP_TITLE, "名称为空或包含非法字符", parent=self)
            return
        if os.path.exists(profile_path(new)):
            messagebox.showerror(APP_TITLE, f"【{new}】已存在", parent=self)
            return
        save_profile(new, p["values"], p.get("saved_at"), p["last_login"])
        os.remove(profile_path(name))
        self.app.refresh()

    def delete(self):
        name = self.selected()
        if not name:
            return
        if confirm(self.app, "删除账号", f"确定要删除【{name}】吗？\n删除后该账号的登录快照无法恢复。", "删除"):
            os.remove(profile_path(name))
            self.app.refresh()
            self.app.set_status(f"已删除【{name}】")

    def switch_only(self):
        name = self.selected()
        if not name:
            return
        if game_running():
            messagebox.showwarning(APP_TITLE, "游戏正在运行，请先退出游戏", parent=self)
            return
        if self.app.switch_to(name):
            self.app.set_status(f"已切换到【{name}】（未启动游戏）")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        if os.path.isfile(ICON_PATH):
            self.iconbitmap(default=ICON_PATH)  # default= 让对话框等子窗口也使用该图标
        self.geometry("680x700")
        self.minsize(560, 680)
        self.cfg = load_config()
        # 首次运行即在程序目录创建存储文件
        os.makedirs(PROFILE_DIR, exist_ok=True)
        if not os.path.isfile(CONFIG_PATH):
            save_config(self.cfg)
        self.busy = False
        self.current_name = None
        self.status = tk.StringVar()
        self.bind_all("<MouseWheel>", lambda e: self.launch_page.on_wheel(e))

        self._build_ui()
        self.show_page("config" if not list_profiles(include_backup=False) else "launch")
        self.after(30_000, self._tick)
        if not is_admin():
            self.set_status(self.status.get() + "  ·  未以管理员运行，切换时会额外弹出 UAC")

    def _build_ui(self):
        apply_theme(self.cfg["theme"])
        setup_style(self)

        header = ttk.Frame(self, padding=(18, 12, 12, 8))
        header.pack(fill="x")
        self.title_lbl = ttk.Label(header, text="✦ 星穹铁道 · 账号切换", style="Title.TLabel")
        self.launcher_btn = ttk.Button(header, text="▶  官方启动器", style="Launcher.TButton",
                                       command=self.open_launcher)
        self.toggle_btn = ttk.Button(header, style="Header.TButton", command=self.toggle_page)
        self.toggle_btn.pack(side="right")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w").pack(fill="x", side="bottom")

        self.container = ttk.Frame(self, padding=(0, 8, 0, 0))
        self.container.pack(fill="both", expand=True)
        self.launch_page = LaunchPage(self.container, self)
        self.config_page = ConfigPage(self.container, self)
        self.refresh()

    def set_theme(self, name):
        if name == self.cfg["theme"] or name not in THEMES:
            return
        self.game_path()
        self.save_launcher()
        self.cfg["theme"] = name
        save_config(self.cfg)
        status = self.status.get()
        for w in self.winfo_children():
            w.destroy()
        self._build_ui()
        self.show_page("config")
        self.set_status(status)

    # ----- 页面 -----

    def show_page(self, page):
        self.page = page
        self.launch_page.pack_forget()
        self.config_page.pack_forget()
        self.title_lbl.pack_forget()
        self.launcher_btn.pack_forget()
        if page == "launch":
            # 启动页标题位置放官方启动器按钮，未填写路径时不可点
            self.launcher_btn.state(["!disabled"] if self.cfg["launcher_path"] else ["disabled"])
            self.launcher_btn.pack(side="left")
            self.launch_page.pack(fill="both", expand=True)
            self.toggle_btn.configure(text="⚙  配置")
        else:
            self.title_lbl.pack(side="left")
            self.config_page.pack(fill="both", expand=True)
            self.toggle_btn.configure(text="←  返回")

    def toggle_page(self):
        self.game_path()
        self.save_launcher()
        self.show_page("config" if self.page == "launch" else "launch")

    def save_launcher(self):
        path, args = split_command(self.config_page.launcher_var.get(), self.config_page.launcher_args_var.get())
        self.config_page.launcher_var.set(path)
        self.config_page.launcher_args_var.set(args)
        if (path, args) != (self.cfg["launcher_path"], self.cfg["launcher_args"]):
            self.cfg["launcher_path"], self.cfg["launcher_args"] = path, args
            save_config(self.cfg)

    def open_launcher(self):
        path = self.cfg["launcher_path"]
        if not path:
            return
        if not os.path.isfile(path):
            messagebox.showerror(APP_TITLE, f"找不到官方启动器：\n{path}\n请在「配置」中重新选择", parent=self)
            return
        try:
            launch_launcher(path, self.cfg["launcher_args"])
            self.set_status("已打开官方启动器")
        except RuntimeError as e:
            messagebox.showerror(APP_TITLE, str(e), parent=self)

    def _tick(self):
        self.launch_page.update_times()
        self.after(30_000, self._tick)

    # ----- 状态 -----

    def set_status(self, text):
        self.status.set(text)

    def game_path(self):
        path = self.config_page.path_var.get().strip()
        if path != self.cfg["game_path"]:
            self.cfg["game_path"] = path
            save_config(self.cfg)
        return path

    def refresh(self):
        try:
            current = fingerprint(read_reg_values())
        except (OSError, RuntimeError):
            current = None
        self.current_name = None
        if current:
            for n in list_profiles(include_backup=False):
                try:
                    if fingerprint(load_profile(n)["values"]) == current:
                        self.current_name = n
                        break
                except (OSError, ValueError, KeyError):
                    continue
        self.launch_page.refresh(self.current_name)
        self.config_page.refresh(self.current_name)
        if current is None:
            self.set_status("注册表中没有登录数据（游戏还没登录过？）")
        elif self.current_name:
            self.set_status(f"当前账号：{self.current_name}")
        else:
            self.set_status("当前登录的账号尚未保存")

    # ----- 切换流程 -----

    def switch_to(self, name):
        """把快照写回注册表；当前数据未保存时先自动备份。返回是否成功。"""
        try:
            target = load_profile(name)["values"]
            current = read_reg_values()
            if current:
                fp = fingerprint(current)
                known = any(fingerprint(load_profile(n)["values"]) == fp
                            for n in list_profiles(include_backup=False))
                if not known:
                    save_profile(AUTO_BACKUP_NAME, current)
            write_reg_values(target)
        except (OSError, ValueError, KeyError, RuntimeError) as e:
            messagebox.showerror(APP_TITLE, f"切换失败：{e}", parent=self)
            return False
        self.refresh()
        return True

    def activate(self, name):
        """双击：关闭游戏 → 切换 → 启动"""
        if self.busy:
            return
        path = self.game_path()
        if not os.path.isfile(path):
            messagebox.showerror(APP_TITLE, "游戏路径无效，请在「配置」中选择 StarRail.exe", parent=self)
            self.show_page("config")
            return
        running = game_running()
        if running and name == self.current_name:
            self.set_status(f"【{name}】已经在运行")
            return
        self.busy = True
        self.configure(cursor="watch")
        if running:
            self.set_status("正在关闭游戏…")
            try:
                kill_game()
            except RuntimeError as e:
                self._finish(str(e), error=True)
                return
            self._wait_exit(name, time.time() + KILL_TIMEOUT)
        else:
            self._switch_and_launch(name)

    def _wait_exit(self, name, deadline):
        if not game_running():
            # 留一点时间让进程彻底释放
            self.after(1000, lambda: self._switch_and_launch(name))
        elif time.time() > deadline:
            self._finish("游戏进程未能结束，请手动退出后重试", error=True)
        else:
            self.after(300, lambda: self._wait_exit(name, deadline))

    def _switch_and_launch(self, name):
        if not self.switch_to(name):
            self._finish(None)
            return
        try:
            launch_game(self.game_path())
        except RuntimeError as e:
            self._finish(f"已切换到【{name}】，但{e}", error=True)
            return
        try:
            touch_last_login(name)
        except (OSError, ValueError, KeyError):
            pass
        self.refresh()
        self._finish(f"已切换到【{name}】并启动游戏")

    def _finish(self, msg, error=False):
        self.busy = False
        self.configure(cursor="")
        if msg:
            self.set_status(msg)
            if error:
                messagebox.showerror(APP_TITLE, msg, parent=self)


def ensure_admin():
    """以管理员身份重启自身，这样关闭/启动游戏时不必反复弹 UAC。用户拒绝则以普通权限继续。"""
    # exe 在清单中声明了管理员权限（--uac-admin），由系统负责提权
    if FROZEN or is_admin() or "--no-elevate" in sys.argv:
        return
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    params = f'"{os.path.abspath(__file__)}" --no-elevate'
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, BASE_DIR, 1)
    if ret > 32:
        sys.exit(0)


if __name__ == "__main__":
    ensure_admin()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        # 独立的任务栏分组，任务栏显示本程序图标而不是 Python 图标
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass
    App().mainloop()
