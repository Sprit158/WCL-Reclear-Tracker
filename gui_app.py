from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from bootstrap import ensure_dependencies


def launch() -> None:
    ensure_dependencies()

    import customtkinter as ctk
    import tkinter as tk
    from tkinter import ttk

    from gui_data import NUMERIC_FILTERS, TABLE_COLUMNS, filter_schedule_rows, load_schedule_rows, summary_for
    from main import load_config_early
    from settings_manager import get_guild_profile_from_settings

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    colours = {
        "app": "#090B11",
        "sidebar": "#10141E",
        "panel": "#151B28",
        "panel_alt": "#1B2232",
        "border": "#2A3448",
        "text": "#F4F7FC",
        "muted": "#93A0B5",
        "accent": "#7C5CFC",
        "accent_hover": "#947AFF",
        "cyan": "#45C6F2",
        "gold": "#F4C560",
        "green": "#55D39A",
        "amber": "#F4B860",
        "red": "#F0728D",
    }

    class ReclearApp(ctk.CTk):
        def __init__(self) -> None:
            super().__init__()
            self.title("WCL Reclear Tracker 2.0")
            self.geometry("1540x940")
            self.minsize(1240, 760)
            self.configure(fg_color=colours["app"])
            self.config_data = load_config_early()
            self.rows: list[dict[str, Any]] = []
            self.filtered_rows: list[dict[str, Any]] = []
            self.shown_rows: list[dict[str, Any]] = []
            self.filters: dict[str, Any] = {}
            self.sort_key = "rank"
            self.sort_reverse = False
            self.worker: threading.Thread | None = None
            self.process: subprocess.Popen[str] | None = None
            self.log_queue: queue.Queue[str] = queue.Queue()
            self.active_page = "overview"
            self._build_shell()
            self.refresh_data()
            self.show_page("overview")
            self.after(120, self._drain_log_queue)

        def _build_shell(self) -> None:
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(0, weight=1)
            self.sidebar = ctk.CTkFrame(self, width=290, corner_radius=0, fg_color=colours["sidebar"])
            self.sidebar.grid(row=0, column=0, sticky="nsew")
            self.sidebar.grid_propagate(False)

            brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
            brand.pack(fill="x", padx=24, pady=(26, 24))
            crest = ctk.CTkLabel(brand, text="R", width=42, height=42, corner_radius=14, fg_color=colours["accent"], font=ctk.CTkFont(size=22, weight="bold"))
            crest.pack(side="left")
            brand_copy = ctk.CTkFrame(brand, fg_color="transparent")
            brand_copy.pack(side="left", padx=12)
            ctk.CTkLabel(brand_copy, text="WCL RECLEAR", text_color=colours["text"], font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(brand_copy, text="Tracker 2.0  •  VS / DR / MQD", text_color=colours["muted"], font=ctk.CTkFont(size=10)).pack(anchor="w")

            ctk.CTkLabel(self.sidebar, text="WORKSPACE", text_color=colours["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=26)
            self.nav_buttons: dict[str, Any] = {}
            for key, label, icon in [
                ("overview", "Overview", "◈"),
                ("schedules", "Guild schedules", "▦"),
                ("activity", "Scan activity", "◌"),
                ("settings", "Settings", "⚙"),
            ]:
                button = ctk.CTkButton(
                    self.sidebar, text=f"{icon}   {label}", anchor="w", height=44, corner_radius=12,
                    fg_color="transparent", hover_color=colours["panel_alt"], text_color=colours["muted"],
                    font=ctk.CTkFont(size=14), command=lambda page=key: self.show_page(page),
                )
                button.pack(fill="x", padx=16, pady=3)
                self.nav_buttons[key] = button

            self.sidebar.pack_propagate(False)
            self.sidebar_status = ctk.CTkFrame(self.sidebar, fg_color=colours["panel"], corner_radius=16, border_width=1, border_color=colours["border"])
            self.sidebar_status.pack(side="bottom", fill="x", padx=16, pady=18)
            ctk.CTkLabel(self.sidebar_status, text="DATA STATUS", text_color=colours["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(12, 3))
            self.sidebar_status_value = ctk.CTkLabel(self.sidebar_status, text="Loading cache…", text_color=colours["text"], font=ctk.CTkFont(size=12, weight="bold"), wraplength=220, justify="left")
            self.sidebar_status_value.pack(anchor="w", padx=14, pady=(0, 12))

            self.content = ctk.CTkFrame(self, fg_color="transparent")
            self.content.grid(row=0, column=1, sticky="nsew")
            self.content.grid_columnconfigure(0, weight=1)
            self.content.grid_rowconfigure(1, weight=1)
            self._build_topbar()
            self.page_host = ctk.CTkFrame(self.content, fg_color="transparent")
            self.page_host.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 26))
            self.page_host.grid_columnconfigure(0, weight=1)
            self.page_host.grid_rowconfigure(0, weight=1)
            self.pages: dict[str, Any] = {}
            self._build_overview()
            self._build_schedules()
            self._build_activity()
            self._build_settings()

        def _build_topbar(self) -> None:
            top = ctk.CTkFrame(self.content, fg_color="transparent")
            top.grid(row=0, column=0, sticky="ew", padx=30, pady=(24, 18))
            top.grid_columnconfigure(0, weight=1)
            self.page_title = ctk.CTkLabel(top, text="Overview", text_color=colours["text"], font=ctk.CTkFont(size=28, weight="bold"))
            self.page_title.grid(row=0, column=0, sticky="w")
            self.profile_chip = ctk.CTkLabel(top, text="  No saved guild  ", height=32, corner_radius=16, fg_color=colours["panel"], text_color=colours["muted"], font=ctk.CTkFont(size=12))
            self.profile_chip.grid(row=0, column=1, padx=(10, 8))
            self.scope_chip = ctk.CTkLabel(top, text="  VS / DR / MQD  ", height=32, corner_radius=16, fg_color="#172B3B", text_color=colours["cyan"], font=ctk.CTkFont(size=12, weight="bold"))
            self.scope_chip.grid(row=0, column=2)

        def _new_page(self, name: str) -> Any:
            page = ctk.CTkFrame(self.page_host, fg_color="transparent")
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            self.pages[name] = page
            return page

        def _build_overview(self) -> None:
            page = self._new_page("overview")
            page.grid_columnconfigure((0, 1, 2, 3), weight=1)
            hero = ctk.CTkFrame(page, height=176, fg_color=colours["panel"], corner_radius=22, border_width=1, border_color=colours["border"])
            hero.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 18))
            hero.grid_propagate(False)
            hero.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(hero, text="Your guild schedule intelligence", text_color=colours["text"], font=ctk.CTkFont(size=26, weight="bold")).grid(row=0, column=0, sticky="sw", padx=28, pady=(30, 2))
            ctk.CTkLabel(hero, text="Compare the full Mythic VS / DR / MQD ranking field, identify real two-day schedules and keep every verified result cached.", text_color=colours["muted"], font=ctk.CTkFont(size=13), wraplength=730, justify="left").grid(row=1, column=0, sticky="nw", padx=28)
            controls = ctk.CTkFrame(hero, fg_color="transparent")
            controls.grid(row=0, column=1, rowspan=2, sticky="e", padx=26)
            self.scope_selector = ctk.CTkSegmentedButton(controls, values=["Region", "World"], selected_color=colours["accent"], selected_hover_color=colours["accent_hover"], unselected_color=colours["panel_alt"], command=lambda _: None)
            self.scope_selector.set("Region")
            self.scope_selector.pack(anchor="e", pady=(0, 10))
            self.scan_button = ctk.CTkButton(controls, text="Run schedule scan", height=42, corner_radius=14, fg_color=colours["accent"], hover_color=colours["accent_hover"], font=ctk.CTkFont(size=14, weight="bold"), command=self.start_scan)
            self.scan_button.pack(anchor="e")

            self.stat_cards: dict[str, Any] = {}
            for index, (key, title, colour) in enumerate([
                ("guilds", "CACHED GUILDS", colours["cyan"]),
                ("two_day", "LIKELY TWO-DAY", colours["gold"]),
                ("verified", "VERIFIED", colours["green"]),
                ("attention", "NEEDS REVIEW", colours["amber"]),
            ]):
                card = ctk.CTkFrame(page, fg_color=colours["panel"], corner_radius=18, border_width=1, border_color=colours["border"])
                card.grid(row=1, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0 if index == 3 else 6), pady=(0, 18))
                ctk.CTkLabel(card, text=title, text_color=colours["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(16, 3))
                value = ctk.CTkLabel(card, text="—", text_color=colour, font=ctk.CTkFont(size=28, weight="bold"))
                value.pack(anchor="w", padx=18, pady=(0, 15))
                self.stat_cards[key] = value

            quick = ctk.CTkFrame(page, fg_color=colours["panel"], corner_radius=20, border_width=1, border_color=colours["border"])
            quick.grid(row=2, column=0, columnspan=4, sticky="nsew")
            quick.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(quick, text="Latest activity", text_color=colours["text"], font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, sticky="w", padx=22, pady=(18, 4))
            self.overview_activity = ctk.CTkTextbox(quick, height=270, corner_radius=12, fg_color="#0D111A", text_color=colours["muted"], font=("Consolas", 12))
            self.overview_activity.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
            self.overview_activity.insert("end", "Ready. Choose Region or World, then run a schedule scan.\n")
            self.overview_activity.configure(state="disabled")

        def _build_schedules(self) -> None:
            page = self._new_page("schedules")
            page.grid_columnconfigure(0, weight=1)
            page.grid_rowconfigure(2, weight=1)
            intro = ctk.CTkFrame(page, fg_color="transparent")
            intro.grid(row=0, column=0, sticky="ew", pady=(0, 12))
            intro.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(intro, text="Guild schedule explorer", text_color=colours["text"], font=ctk.CTkFont(size=21, weight="bold")).grid(row=0, column=0, sticky="w")
            self.table_count = ctk.CTkLabel(intro, text="0 results", text_color=colours["muted"], font=ctk.CTkFont(size=12))
            self.table_count.grid(row=0, column=1, sticky="e")

            filter_card = ctk.CTkFrame(page, fg_color=colours["panel"], corner_radius=18, border_width=1, border_color=colours["border"])
            filter_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
            filter_card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(filter_card, text="Filters", text_color=colours["text"], font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(13, 7))
            ctk.CTkButton(filter_card, text="Clear filters", width=100, height=28, corner_radius=10, fg_color=colours["panel_alt"], hover_color=colours["border"], command=self.clear_filters).grid(row=0, column=1, sticky="e", padx=14, pady=(12, 6))
            self.filter_scroll = ctk.CTkScrollableFrame(filter_card, height=152, fg_color="transparent", orientation="horizontal")
            self.filter_scroll.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
            self._build_filters()

            table_card = ctk.CTkFrame(page, fg_color=colours["panel"], corner_radius=18, border_width=1, border_color=colours["border"])
            table_card.grid(row=2, column=0, sticky="nsew")
            table_card.grid_columnconfigure(0, weight=1)
            table_card.grid_rowconfigure(0, weight=1)
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("Reclear.Treeview", background="#10151F", fieldbackground="#10151F", foreground="#DDE5F3", rowheight=34, borderwidth=0, font=("Segoe UI", 10))
            style.configure("Reclear.Treeview.Heading", background="#1B2232", foreground="#AEBBD0", relief="flat", font=("Segoe UI", 10, "bold"), padding=(9, 9))
            style.map("Reclear.Treeview", background=[("selected", "#2D2862")], foreground=[("selected", "#FFFFFF")])
            holder = tk.Frame(table_card, background=colours["panel"])
            holder.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
            holder.grid_rowconfigure(0, weight=1)
            holder.grid_columnconfigure(0, weight=1)
            keys = [column.key for column in TABLE_COLUMNS]
            self.table = ttk.Treeview(holder, columns=keys, show="headings", style="Reclear.Treeview", selectmode="browse")
            for column in TABLE_COLUMNS:
                self.table.heading(column.key, text=column.title, command=lambda key=column.key: self.sort_rows(key))
                self.table.column(column.key, width=column.width, minwidth=50, anchor="center" if column.key not in {"guild", "realm", "days"} else "w", stretch=column.key in {"guild", "days"})
            ybar = ttk.Scrollbar(holder, orient="vertical", command=self.table.yview)
            xbar = ttk.Scrollbar(holder, orient="horizontal", command=self.table.xview)
            self.table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
            self.table.grid(row=0, column=0, sticky="nsew")
            ybar.grid(row=0, column=1, sticky="ns")
            xbar.grid(row=1, column=0, sticky="ew")
            self.table.tag_configure("own", foreground="#F4C560")
            self.table.tag_configure("verified", foreground="#DDE5F3")
            self.table.tag_configure("attention", foreground="#F4C560")
            self.table.tag_configure("error", foreground="#F0728D")
            self.table.bind("<<TreeviewSelect>>", self.show_selected_details)
            self.detail = ctk.CTkLabel(page, text="Select a guild to see its recorded schedule evidence.", text_color=colours["muted"], anchor="w", justify="left", wraplength=1100)
            self.detail.grid(row=3, column=0, sticky="ew", pady=(9, 0))

        def _filter_entry(self, title: str, key: str, width: int = 130) -> None:
            frame = ctk.CTkFrame(self.filter_scroll, fg_color="transparent", width=width)
            frame.pack(side="left", padx=5, pady=4)
            ctk.CTkLabel(frame, text=title, text_color=colours["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w")
            value = ctk.StringVar()
            value.trace_add("write", lambda *_: self.apply_filters())
            entry = ctk.CTkEntry(frame, width=width, height=31, corner_radius=10, textvariable=value, fg_color="#0F141E", border_color=colours["border"], placeholder_text="Any")
            entry.pack()
            self.filters[key] = value

        def _build_filters(self) -> None:
            for title, key, width in [("Guild", "guild", 150), ("Realm", "realm", 125), ("Region", "region", 78), ("Common days", "days", 120)]:
                self._filter_entry(title, key, width)
            for title, key, values, width in [
                ("2-day", "two_day", ["All", "Yes", "No", "?"], 82),
                ("Status", "confidence", ["All", "high", "medium", "low", "ambiguous", "unverified", "error"], 105),
                ("Only me", "own_only", ["Off", "On"], 75),
            ]:
                frame = ctk.CTkFrame(self.filter_scroll, fg_color="transparent", width=width)
                frame.pack(side="left", padx=5, pady=4)
                ctk.CTkLabel(frame, text=title, text_color=colours["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w")
                value = ctk.StringVar(value=values[0])
                value.trace_add("write", lambda *_: self.apply_filters())
                box = ctk.CTkComboBox(frame, values=values, width=width, height=31, corner_radius=10, variable=value, state="readonly", fg_color="#0F141E", border_color=colours["border"], button_color=colours["panel_alt"])
                box.pack()
                self.filters[key] = value
            for key, title in [("rank", "Rank"), ("average", "Avg"), ("first_month", "M1"), ("median", "Med"), ("hours", "Hrs"), ("weeks", "Wks"), ("nights", "Nights"), ("reports", "Reports")]:
                frame = ctk.CTkFrame(self.filter_scroll, fg_color="transparent")
                frame.pack(side="left", padx=5, pady=4)
                ctk.CTkLabel(frame, text=title, text_color=colours["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w")
                range_row = ctk.CTkFrame(frame, fg_color="transparent")
                range_row.pack()
                for suffix, placeholder in [("min", "Min"), ("max", "Max")]:
                    value = ctk.StringVar()
                    value.trace_add("write", lambda *_: self.apply_filters())
                    ctk.CTkEntry(range_row, width=57, height=31, corner_radius=10, textvariable=value, fg_color="#0F141E", border_color=colours["border"], placeholder_text=placeholder).pack(side="left", padx=(0, 3))
                    self.filters[f"{key}_{suffix}"] = value

        def _build_activity(self) -> None:
            page = self._new_page("activity")
            page.grid_columnconfigure(0, weight=1)
            page.grid_rowconfigure(1, weight=1)
            ctk.CTkLabel(page, text="Scan activity", text_color=colours["text"], font=ctk.CTkFont(size=21, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 12))
            self.activity_log = ctk.CTkTextbox(page, corner_radius=18, fg_color="#0D111A", border_width=1, border_color=colours["border"], text_color="#B5C1D4", font=("Consolas", 12))
            self.activity_log.grid(row=1, column=0, sticky="nsew")
            self.activity_log.insert("end", "WCL Reclear Tracker 2.0 ready.\n")
            self.activity_log.configure(state="disabled")

        def _build_settings(self) -> None:
            page = self._new_page("settings")
            page.grid_columnconfigure((0, 1), weight=1)
            ctk.CTkLabel(page, text="Settings & maintenance", text_color=colours["text"], font=ctk.CTkFont(size=21, weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
            profile_card = ctk.CTkFrame(page, fg_color=colours["panel"], corner_radius=18, border_width=1, border_color=colours["border"])
            profile_card.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
            ctk.CTkLabel(profile_card, text="Guild profile", text_color=colours["text"], font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(18, 8))
            self.guild_input = ctk.CTkEntry(profile_card, placeholder_text="Guild name", height=38, corner_radius=11)
            self.realm_input = ctk.CTkEntry(profile_card, placeholder_text="Realm", height=38, corner_radius=11)
            self.region_input = ctk.CTkComboBox(profile_card, values=["EU", "US", "OC", "KR", "TW"], height=38, corner_radius=11)
            for widget in [self.guild_input, self.realm_input, self.region_input]:
                widget.pack(fill="x", padx=20, pady=5)
            self.region_input.set("EU")
            ctk.CTkButton(profile_card, text="Save guild profile", height=38, corner_radius=12, fg_color=colours["accent"], command=self.save_profile).pack(anchor="w", padx=20, pady=(12, 20))
            tools = ctk.CTkFrame(page, fg_color=colours["panel"], corner_radius=18, border_width=1, border_color=colours["border"])
            tools.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
            ctk.CTkLabel(tools, text="Tools", text_color=colours["text"], font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(18, 8))
            for text, command in [("Set up / test WCL v2", lambda: self.run_command(["--setup-v2"], "WCL setup")), ("Check for updates", lambda: self.run_command(["updater.py"], "Update check", direct=True)), ("Clear schedule cache", lambda: self.run_command(["--clear-cache"], "Clear cache"))]:
                ctk.CTkButton(tools, text=text, anchor="w", height=38, corner_radius=11, fg_color=colours["panel_alt"], hover_color=colours["border"], command=command).pack(fill="x", padx=20, pady=5)
            ctk.CTkLabel(tools, text="Updates and long-running commands are shown in Scan activity.", text_color=colours["muted"], wraplength=360, justify="left", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=20, pady=(10, 20))

        def show_page(self, page: str) -> None:
            self.active_page = page
            titles = {"overview": "Overview", "schedules": "Guild schedules", "activity": "Scan activity", "settings": "Settings & maintenance"}
            self.page_title.configure(text=titles[page])
            for key, frame in self.pages.items():
                (frame.grid() if key == page else frame.grid_remove())
            for key, button in self.nav_buttons.items():
                active = key == page
                button.configure(fg_color=colours["panel_alt"] if active else "transparent", text_color=colours["text"] if active else colours["muted"])

        def _format(self, value: Any) -> str:
            if value is None:
                return "—"
            if isinstance(value, float):
                return f"{value:.2f}".rstrip("0").rstrip(".")
            return str(value)

        def refresh_data(self) -> None:
            try:
                self.rows = load_schedule_rows(self.config_data)
            except Exception as exc:
                self.rows = []
                self._log(f"Could not load schedule cache: {type(exc).__name__}: {exc}")
            self.apply_filters()
            summary = summary_for(self.rows)
            for key, value in summary.items():
                self.stat_cards[key].configure(text=str(value))
            profile = get_guild_profile_from_settings()
            profile_text = f"  {profile.name} · {profile.realm}  " if profile else "  No saved guild  "
            self.profile_chip.configure(text=profile_text)
            self.sidebar_status_value.configure(text=f"{summary['guilds']} cached schedules\n{summary['verified']} verified")
            if profile:
                self.guild_input.delete(0, "end")
                self.realm_input.delete(0, "end")
                self.guild_input.insert(0, profile.name)
                self.realm_input.insert(0, profile.realm)
                self.region_input.set(profile.region.upper())

        def _filter_values(self) -> dict[str, str]:
            return {key: value.get() if hasattr(value, "get") else str(value) for key, value in self.filters.items()}

        def apply_filters(self) -> None:
            if not hasattr(self, "table"):
                return
            self.filtered_rows = filter_schedule_rows(self.rows, self._filter_values())
            self._render_table()

        def clear_filters(self) -> None:
            for key, value in self.filters.items():
                if key in {"two_day", "confidence"}:
                    value.set("All")
                elif key == "own_only":
                    value.set("Off")
                else:
                    value.set("")

        def sort_rows(self, key: str) -> None:
            if self.sort_key == key:
                self.sort_reverse = not self.sort_reverse
            else:
                self.sort_key, self.sort_reverse = key, False
            self._render_table()

        def _render_table(self) -> None:
            for item in self.table.get_children():
                self.table.delete(item)
            numeric = {column.key for column in TABLE_COLUMNS if column.numeric}
            def sorter(row: dict[str, Any]) -> Any:
                value = row.get(self.sort_key)
                if self.sort_key in numeric:
                    return (value is None, value if value is not None else float("inf"))
                return str(value or "").casefold()
            shown = sorted(self.filtered_rows, key=sorter, reverse=self.sort_reverse)
            self.shown_rows = shown
            for index, row in enumerate(shown):
                guild = row["guild"] + ("  (you)" if row["is_own"] else "")
                values = [
                    self._format(row.get("rank")), guild, row["realm"], row["region"], row["two_day"],
                    self._format(row["average"]), self._format(row["first_month"]), self._format(row["median"]),
                    self._format(row["hours"]), self._format(row["weeks"]), self._format(row["nights"]),
                    self._format(row["reports"]), row["days"] or "—", row["confidence"].title(),
                ]
                tag = "own" if row["is_own"] else ("error" if row["confidence"] == "error" else "attention" if row["confidence"] in {"unverified", "ambiguous", "low"} else "verified")
                self.table.insert("", "end", iid=str(index), values=values, tags=(tag,))
            self.table_count.configure(text=f"{len(shown):,} of {len(self.rows):,} schedules")

        def show_selected_details(self, _event=None) -> None:
            selected = self.table.selection()
            if not selected:
                return
            row = self.shown_rows[int(selected[0])]
            self.detail.configure(text=f"{row['guild']} · {row['realm']} · {row['confidence'].title()} — {row.get('reason') or 'No additional note was saved.'}")

        def _log(self, text: str) -> None:
            for box in [self.activity_log, self.overview_activity]:
                box.configure(state="normal")
                box.insert("end", text.rstrip() + "\n")
                box.see("end")
                box.configure(state="disabled")

        def _drain_log_queue(self) -> None:
            while True:
                try:
                    line = self.log_queue.get_nowait()
                except queue.Empty:
                    break
                self._log(line)
            self.after(120, self._drain_log_queue)

        def start_scan(self) -> None:
            scope = self.scope_selector.get().lower()
            self.run_command(["--schedule-scan", "--ranking-scope", scope], f"{scope.title()} schedule scan")

        def run_command(self, arguments: list[str], label: str, direct: bool = False) -> None:
            if self.worker and self.worker.is_alive():
                self._log("A command is already running.")
                return
            self.show_page("activity")
            self._log(f"\n── {label} started ──")
            self.scan_button.configure(state="disabled", text="Working…")
            command = [sys.executable, *(arguments if direct else ["START_HERE.py", *arguments])]
            self.worker = threading.Thread(target=self._run_worker, args=(command, label), daemon=True)
            self.worker.start()

        def _run_worker(self, command: list[str], label: str) -> None:
            try:
                self.process = subprocess.Popen(command, cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.log_queue.put(line.rstrip())
                code = self.process.wait()
                self.log_queue.put(f"── {label} finished (exit {code}) ──")
            except Exception as exc:
                self.log_queue.put(f"{label} failed to start: {type(exc).__name__}: {exc}")
            finally:
                self.after(0, self._command_finished)

        def _command_finished(self) -> None:
            self.process = None
            self.scan_button.configure(state="normal", text="Run schedule scan")
            self.refresh_data()

        def save_profile(self) -> None:
            guild, realm, region = self.guild_input.get().strip(), self.realm_input.get().strip(), self.region_input.get().strip().upper()
            if not guild or not realm:
                self._log("Guild name and realm are required before saving.")
                return
            self.run_command(["--configure-guild", "--guild", guild, "--realm", realm, "--region", region], "Save guild profile")

    ReclearApp().mainloop()


if __name__ == "__main__":
    launch()
