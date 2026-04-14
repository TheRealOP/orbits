#!/usr/bin/env python3
import time
import os
import sys
import psutil
import json
import re

try:
    from rich.live import Live
    from rich.table import Table
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.align import Align
    from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
    from rich.text import Text
    from rich.box import ROUNDED, HEAVY_EDGE
except ImportError:
    print("Please install 'rich' package: pip install rich")
    sys.exit(1)

CONSOLE = Console()
ZOMBIE_FILE = ".zombie_mode"
ORBIT_JSON = "orbit.json"
TOKEN_FILE = "Knowledge/progress/token_usage.md"

RAM_CAP_MB = 5120
try:
    if os.path.exists(ORBIT_JSON):
        with open(ORBIT_JSON, "r") as f:
            RAM_CAP_MB = json.load(f).get("tracker", {}).get("ram_limit_mb", 5120)
except Exception:
    pass

# Token Limits configured for visualizing Caps
BUDGETS = {
    "Claude": 100000,
    "Gemini": 250000,
    "Codex":  50000
}

tokens = {
    "Claude": {"in": 0, "out": 0},
    "Gemini": {"in": 0, "out": 0},
    "Codex": {"in": 0, "out": 0}
}
# Load initial values realistically from token tracking ledger if it exists
if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE, "r") as f:
        content = f.read()
        for line in content.split('\n'):
            if "Claude" in line and "in" in line and "out" in line:
                nums = re.findall(r'\d+', line.replace(',', ''))
                if len(nums) >= 2: tokens["Claude"] = {"in": int(nums[-2]), "out": int(nums[-1])}
            elif "Gemini" in line and "in" in line and "out" in line:
                nums = re.findall(r'\d+', line.replace(',', ''))
                if len(nums) >= 2: tokens["Gemini"] = {"in": int(nums[-2]), "out": int(nums[-1])}
            elif "Codex" in line and "in" in line and "out" in line:
                nums = re.findall(r'\d+', line.replace(',', ''))
                if len(nums) >= 2: tokens["Codex"] = {"in": int(nums[-2]), "out": int(nums[-1])}

def get_zombie_status():
    return os.path.exists(ZOMBIE_FILE)

def toggle_zombie_mode():
    if get_zombie_status():
        os.remove(ZOMBIE_FILE)
    else:
        with open(ZOMBIE_FILE, "w") as f:
            f.write("1")

def get_ram_usage():
    targets = {
        "Graphify Operations": 0,
        "SuperLocalMemory DB": 0,
        "Agent 1 (Orchestrator)": 0,
        "Agent 2 (Knowledge)": 0,
        "Other Tracked Elements": 0
    }
    
    for proc in psutil.process_iter(['name', 'cmdline', 'memory_info']):
        try:
            cmdline = proc.info.get('cmdline')
            if not cmdline: continue
            cmd = " ".join(cmdline).lower()
            
            rss_mb = proc.info['memory_info'].rss / (1024 * 1024)
            
            if "graphify" in cmd:
                targets["Graphify Operations"] += rss_mb
            elif "superlocalmemory" in cmd or "slm" in cmd:
                targets["SuperLocalMemory DB"] += rss_mb
            elif "agent1" in cmd:
                targets["Agent 1 (Orchestrator)"] += rss_mb
            elif "agent2" in cmd:
                targets["Agent 2 (Knowledge)"] += rss_mb
            elif "orbits" in cmd or "opencode" in cmd:
                targets["Other Tracked Elements"] += rss_mb
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return targets

def make_layout():
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main")
    )
    layout["main"].split_row(
        Layout(name="left_panel", ratio=1),
        Layout(name="right_panel", ratio=1)
    )
    return layout

def make_header():
    zombie = get_zombie_status()
    style = "bold white on red" if zombie else "bold cyan on dark_blue"
    status_text = " [ ZOMBIE MODE ENGAGED • THROTTLING ] " if zombie else " [ SYSTEM ONLINE • OPTIMAL PERFORMANCE ] "
    
    header = Text()
    header.append(" 🌌 ORBITS TERMINAL COMMAND ", style="bold bright_magenta reverse")
    header.append(" " + status_text + " ", style=style)
    header.append(" |  Press 'Z' to Toggle  |  Ctrl+C to Exit ", style="dim italic")
    
    return Panel(Align.center(header, vertical="middle"), style="blue", box=ROUNDED)

def make_token_panel():
    progress = Progress(
        TextColumn("[bold cyan]{task.description}", justify="left"),
        BarColumn(bar_width=None, complete_style="cyan", finished_style="red"),
        TaskProgressColumn(),
        TextColumn("[yellow]{task.fields[remaining]} tokens remain"),
        expand=True
    )
    
    table = Table(show_header=True, header_style="bold magenta", expand=True, border_style="blue")
    table.add_column("LLM Provider")
    table.add_column("Inbound", style="cyan")
    table.add_column("Outbound", style="green")
    table.add_column("Total Usage", justify="right", style="bold white")
    
    for provider, counts in tokens.items():
        used = counts["in"] + counts["out"]
        budget = BUDGETS.get(provider, 100000)
        remaining = max(0, budget - used)
        
        progress.add_task(
            f"{provider:<7}", 
            total=budget, 
            completed=used, 
            remaining=f"{remaining:,}"
        )
        
        table.add_row(
            provider, 
            f"{counts['in']:,}", 
            f"{counts['out']:,}",
            f"{used:,}"
        )

    content = Group(
        progress,
        Text("\n"),
        table
    )
    
    return Panel(
        content,
        title="[bold yellow]API TOKEN ALLOCATIONS[/bold yellow]", 
        border_style="yellow",
        box=ROUNDED,
        padding=(1, 2)
    )

def make_ram_panel():
    ram_usage = get_ram_usage()
    total_used = sum(ram_usage.values())
    
    is_over = total_used > RAM_CAP_MB
    bar_color = "bright_red" if is_over else "bright_green"
    
    progress = Progress(
        TextColumn("[bold bright_magenta]Master RAM Cap[/bold bright_magenta]", justify="left"),
        BarColumn(bar_width=None, complete_style=bar_color),
        TaskProgressColumn(),
        TextColumn(f"[white]{total_used:.1f} MB / {RAM_CAP_MB:.1f} MB"),
        expand=True
    )
    progress.add_task("Total RAM", total=RAM_CAP_MB, completed=total_used)
    
    table = Table(show_header=True, header_style="bold green", expand=True, border_style="blue")
    table.add_column("Subsystem Breakdown")
    table.add_column("System RSS", justify="right")
    
    sorted_ram = sorted(ram_usage.items(), key=lambda x: x[1], reverse=True)
    for comp, mb in sorted_ram:
        if mb > 0 or comp == "Graphify Operations":
            color = "bright_red" if mb > (RAM_CAP_MB * 0.5) else "yellow" if mb > (RAM_CAP_MB * 0.2) else "bright_green"
            table.add_row(f"[white]{comp}[/white]", f"[{color}]{mb:.1f} MB[/{color}]")

    content = Group(
        progress,
        Text("\n"),
        table
    )
    
    return Panel(
        content,
        title="[bold red]MEMORY SATURATION[/bold red]", 
        border_style="red",
        box=ROUNDED,
        padding=(1, 2)
    )

def generate_dashboard():
    # Minor jitter simulation for visual life on the tables (since it's a dashboard)
    # tokens["Claude"]["in"] += 1
    layout = make_layout()
    layout["header"].update(make_header())
    layout["left_panel"].update(make_token_panel())
    layout["right_panel"].update(make_ram_panel())
    return layout

if __name__ == "__main__":
    import threading
    
    def key_listener():
        import tty, termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            while True:
                ch = sys.stdin.read(1)
                if ch.lower() == 'z':
                    toggle_zombie_mode()
                elif ch == '\x03': # Ctrl+C
                    os.kill(os.getpid(), 2)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    try:
        listener = threading.Thread(target=key_listener, daemon=True)
        listener.start()
    except Exception:
        pass 

    with Live(generate_dashboard(), console=CONSOLE, refresh_per_second=4, screen=True) as live:
        try:
            while True:
                time.sleep(0.25)
                live.update(generate_dashboard())
        except KeyboardInterrupt:
            pass
